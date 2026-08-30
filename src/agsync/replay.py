"""Historical replay — run the check engine at every commit in a repo's history.

The question this answers is *"how many of these pushes would the gate have
rejected?"*. It is the cheapest evidence that a choke point is worth having:
instead of arguing that hand-maintained memory decays, replay measures it on a
repository that already decayed.

Two properties matter more than the number itself:

* **The target repository is never touched.** Replay clones it to a temp
  directory and moves HEAD only inside that clone, so an interrupted run cannot
  leave someone's repo detached at a commit from three weeks ago.
* **Rejection means exactly what the gate means.** The same
  :func:`~agsync.engine.check` runs here as in ``pre-commit`` and CI, with the
  same config resolution, so replay and the gate can never disagree.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from .engine import CONFIG_NAMES, Config, check
from .model import ERROR, Finding
from .reporters import _COLORS, _supports_color

TEMP_PREFIX = "agsync-replay-"

# ``https://…``, ``ssh://…``, ``file://…`` or the scp-like ``git@host:path``.
RE_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://|^[^/\\:]+@[^/\\:]+:")


class ReplayError(Exception):
    """An operational failure: not a repo, unresolvable ref, clone failed.

    Distinct from a commit being rejected, which is an ordinary result.
    """


# ------------------------------------------------------------------ model


@dataclass(frozen=True)
class CommitResult:
    """One commit, judged by today's rules."""

    sha: str
    date: str  # ISO 8601 author date
    errors: int
    warnings: int
    rules: tuple[str, ...]  # rule names that produced errors, sorted
    fingerprints: tuple[str, ...]

    @property
    def short(self) -> str:
        return self.sha[:7]

    @property
    def day(self) -> str:
        return self.date[:10]

    @property
    def rejected(self) -> bool:
        return self.errors > 0

    def as_dict(self) -> dict:
        return {
            "sha": self.sha,
            "short": self.short,
            "date": self.date,
            "errors": self.errors,
            "warnings": self.warnings,
            "rules": list(self.rules),
            "rejected": self.rejected,
        }


@dataclass(frozen=True)
class Span:
    """How long one violation, or one rule, went unnoticed.

    ``commits_survived`` and ``days_survived`` run from the first commit where
    it appeared to the last one where it was still there, inclusive. A
    violation that was fixed and later reintroduced therefore reads as a single
    long span rather than two short ones — the pessimistic reading, and the
    honest one for "how long could an agent have believed this".
    """

    key: str  # rule name, or fingerprint for a single violation
    rule: str
    path: str
    message: str
    first_sha: str
    first_date: str
    last_sha: str
    last_date: str
    still_failing: bool
    commits_survived: int
    seconds_survived: int

    @property
    def first_short(self) -> str:
        return self.first_sha[:7]

    @property
    def days_survived(self) -> int:
        return self.seconds_survived // 86400

    @property
    def duration(self) -> str:
        """Human units. A ledger bug that lived nine hours is not "0 days"."""
        if self.seconds_survived >= 86400:
            days = self.days_survived
            return f"{days} day{'s' if days != 1 else ''}"
        hours = self.seconds_survived // 3600
        if hours:
            return f"{hours} hour{'s' if hours != 1 else ''}"
        minutes = self.seconds_survived // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"

    def as_dict(self) -> dict:
        return {
            "rule": self.rule,
            "path": self.path,
            "message": self.message,
            "first_sha": self.first_sha,
            "first_date": self.first_date,
            "last_sha": self.last_sha,
            "last_date": self.last_date,
            "still_failing": self.still_failing,
            "commits_survived": self.commits_survived,
            "days_survived": self.days_survived,
            "seconds_survived": self.seconds_survived,
        }


@dataclass(frozen=True)
class ReplayResult:
    source: str
    ref: str
    commits: tuple[CommitResult, ...]
    first_seen: dict[str, Span]  # rule name -> span
    violations: tuple[Span, ...]  # individual violations, longest-lived first

    @property
    def total(self) -> int:
        return len(self.commits)

    @property
    def rejected(self) -> int:
        return sum(1 for commit in self.commits if commit.rejected)

    @property
    def headline(self) -> str:
        return f"{self.rejected} of {self.total} pushes would have been rejected"

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "ref": self.ref,
            "total": self.total,
            "rejected": self.rejected,
            "commits": [commit.as_dict() for commit in self.commits],
            "first_seen": {
                rule: span.as_dict() for rule, span in sorted(self.first_seen.items())
            },
            "violations": [span.as_dict() for span in self.violations],
        }


# -------------------------------------------------------------------- git


def _git(cwd: str | None, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as exc:  # pragma: no cover - git is a hard requirement
        raise ReplayError("git is not installed or not on PATH") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ReplayError(detail or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _git_ok(cwd: str | None, *args: str) -> bool:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    return result.returncode == 0


def is_url(source: str) -> bool:
    """``file://`` counts as a URL, which is how tests stay off the network."""
    return bool(RE_URL.match(source))


@contextmanager
def clone(source: str):
    """Yield a throwaway clone of ``source``; remove it on the way out.

    ``--local`` hardlinks the object store, so cloning a neighbouring directory
    costs almost nothing. The point is not speed though — it is that every
    checkout below happens somewhere we own.
    """
    parent = tempfile.mkdtemp(prefix=TEMP_PREFIX)
    work = os.path.join(parent, "repo")
    try:
        if is_url(source):
            _git(None, "clone", "--quiet", source, work)
        else:
            root = os.path.abspath(os.path.expanduser(source))
            if not os.path.isdir(root):
                raise ReplayError(f"{source}: no such directory")
            if not _git_ok(root, "rev-parse", "--git-dir"):
                raise ReplayError(f"{source}: not a git repository")
            _git(None, "clone", "--quiet", "--local", root, work)
        yield work
    finally:
        _remove_tree(parent)


def _make_writable(path: str) -> None:
    """Clear read-only attributes across a tree, best effort."""
    for target in (path, *(
        os.path.join(base, name)
        for base, dirs, files in os.walk(path)
        for name in (*dirs, *files)
    )):
        try:
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        except OSError:  # pragma: no cover - best effort
            pass


def _remove_tree(path: str) -> None:
    """Remove a temp clone, retrying before giving up.

    Two failure modes, neither theoretical:

    * Git marks its object files read-only. On Windows a read-only file cannot
      be unlinked at all, so the clone survives every attempt until the
      attribute is cleared. POSIX allows the unlink, which is why this only
      ever shows up on someone else's machine.
    * The contents go, then unlinking the directory itself loses a race with
      whatever held it for a moment, leaving an empty husk.

    ``ignore_errors`` hides both, which is how a temp clone outlives the
    process that promised to remove it. Retrying costs microseconds.
    """
    for delay in (0, 0.05, 0.2):
        if delay:
            time.sleep(delay)
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return
        _make_writable(path)
    shutil.rmtree(path, ignore_errors=True)


def _resolve_ref(work: str, ref: str) -> str:
    """Accept a branch, a remote branch or a tag.

    ``git clone`` only creates a local branch for the source's HEAD; every other
    branch arrives as ``origin/<name>``, so a bare ``--ref main`` has to be
    allowed to mean either.
    """
    for candidate in (ref, f"origin/{ref}", f"refs/tags/{ref}"):
        if _git_ok(work, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"):
            return candidate
    available = _git(
        work,
        "for-each-ref",
        "--format=%(refname:short)",
        "refs/heads",
        "refs/remotes/origin",
        "refs/tags",
    ).split()
    # ``refs/remotes/origin/HEAD`` shortens to a bare ``origin``, which is not a
    # ref anyone can pass; listing it would only send people down a dead end.
    known = ", ".join(
        sorted(
            {
                name.removeprefix("origin/")
                for name in available
                if name != "origin" and not name.endswith("/HEAD")
            }
        )
    )
    raise ReplayError(f"ref {ref!r} not found; this repository has: {known or 'no refs'}")


def _commit_log(work: str, ref: str) -> list[tuple[str, str]]:
    """(sha, ISO author date) oldest first."""
    out = _git(work, "log", "--reverse", "--format=%H%x09%aI", ref)
    entries = []
    for line in out.splitlines():
        sha, _, iso = line.partition("\t")
        if sha:
            entries.append((sha, iso))
    return entries


def _checkout(work: str, sha: str) -> None:
    _git(work, "checkout", "--quiet", "--force", "--detach", sha)
    # The checker walks the filesystem, so anything left over from the previous
    # commit would be read as if this commit had written it. Safe here and only
    # here: `work` is a temp clone we created.
    _git(work, "clean", "-qfdx")


# ----------------------------------------------------------------- engine


def _check_commit(work: str) -> tuple[list[Finding], int]:
    """Errors and warning count for the tree currently checked out.

    Config is read from the tree *at this commit*, which is what the gate would
    have done at the time. A commit whose config does not parse is a rejection
    too — ``agsync check`` exits non-zero there as well.
    """
    try:
        config = Config.load(work)
    except ValueError as exc:
        broken = Finding(
            "invalid-config",
            CONFIG_NAMES[0],
            0,
            f"configuration cannot be loaded: {exc}",
            ERROR,
        )
        return [broken], 0
    report = check(work, config, use_baseline=False)
    return report.errors, len(report.warnings)


def _spans(
    commits: list[CommitResult],
    labels: dict[str, tuple[str, str, str]],
    occurrences: dict[str, tuple[int, int]],
) -> list[Span]:
    last_index = len(commits) - 1
    spans = []
    for key, (first, last) in occurrences.items():
        rule, path, message = labels[key]
        spans.append(
            Span(
                key=key,
                rule=rule,
                path=path,
                message=message,
                first_sha=commits[first].sha,
                first_date=commits[first].date,
                last_sha=commits[last].sha,
                last_date=commits[last].date,
                still_failing=last == last_index,
                commits_survived=last - first + 1,
                seconds_survived=_seconds_between(
                    commits[first].date, commits[last].date
                ),
            )
        )
    return spans


def _seconds_between(start: str, end: str) -> int:
    try:
        delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    except ValueError:  # pragma: no cover - git always emits valid ISO 8601
        return 0
    return int(delta.total_seconds())


def replay(
    source: str,
    ref: str = "main",
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> ReplayResult:
    """Run the check engine at every commit reachable from ``ref``.

    ``source`` is a local path or a clone URL — both are accepted and neither is
    assumed. Raises :class:`ReplayError` for operational failures; a commit that
    would have been rejected is a result, not an error.
    """
    with clone(source) as work:
        resolved = _resolve_ref(work, ref)
        entries = _commit_log(work, resolved)
        if not entries:
            raise ReplayError(f"ref {ref!r} has no commits")

        total = len(entries)
        commits: list[CommitResult] = []
        # fingerprint -> (rule, path, message) and -> (first index, last index)
        labels: dict[str, tuple[str, str, str]] = {}
        occurrences: dict[str, tuple[int, int]] = {}

        for index, (sha, iso) in enumerate(entries):
            _checkout(work, sha)
            errors, warnings = _check_commit(work)
            for finding in errors:
                key = finding.fingerprint()
                labels.setdefault(key, (finding.rule, finding.path, finding.message))
                first, _ = occurrences.get(key, (index, index))
                occurrences[key] = (first, index)
            commits.append(
                CommitResult(
                    sha=sha,
                    date=iso,
                    errors=len(errors),
                    warnings=warnings,
                    rules=tuple(sorted({finding.rule for finding in errors})),
                    fingerprints=tuple(sorted({f.fingerprint() for f in errors})),
                )
            )
            if progress:
                progress(index + 1, total, sha)

    violations = _spans(commits, labels, occurrences)
    violations.sort(key=lambda span: (-span.commits_survived, span.rule, span.path))

    # A rule's span is the union of its violations' spans: first time the rule
    # ever fired, last time it still did.
    by_rule: dict[str, tuple[int, int]] = {}
    rule_labels: dict[str, tuple[str, str, str]] = {}
    index_of = {commit.sha: position for position, commit in enumerate(commits)}
    for span in violations:
        first = index_of[span.first_sha]
        last = index_of[span.last_sha]
        if span.rule in by_rule:
            known_first, known_last = by_rule[span.rule]
            by_rule[span.rule] = (min(known_first, first), max(known_last, last))
        else:
            by_rule[span.rule] = (first, last)
            rule_labels[span.rule] = (span.rule, "", "")
    first_seen = {span.key: span for span in _spans(commits, rule_labels, by_rule)}

    return ReplayResult(
        source=source,
        ref=ref,
        commits=tuple(commits),
        first_seen=first_seen,
        violations=tuple(violations),
    )


# -------------------------------------------------------------- rendering


def render_text(
    result: ReplayResult,
    stream=sys.stdout,
    show_first_seen: bool = False,
) -> None:
    color = _supports_color(stream)

    def paint(token: str, text: str) -> str:
        return f"{_COLORS[token]}{text}{_COLORS['reset']}" if color else text

    print(paint("bold", f"{'sha':<7}  {'date':<10}  {'errors':>6}  rules"), file=stream)
    for commit in result.commits:
        rules = ", ".join(commit.rules) if commit.rules else "—"
        count = paint("error", f"{commit.errors:>6}") if commit.rejected else f"{commit.errors:>6}"
        print(f"{commit.short:<7}  {commit.day:<10}  {count}  {rules}", file=stream)

    print(file=stream)
    print(paint("error" if result.rejected else "reset", result.headline), file=stream)

    if not show_first_seen or not result.first_seen:
        return

    print(file=stream)
    width = max(len(rule) for rule in result.first_seen)
    print(
        paint("bold", f"{'rule':<{width}}  {'first failed':<19}  survived"),
        file=stream,
    )
    for rule, span in sorted(
        result.first_seen.items(), key=lambda item: -item[1].commits_survived
    ):
        located = f"{span.first_short} {span.first_date[:10]}"
        survived = f"{span.commits_survived} commits, {span.duration}"
        if span.still_failing:
            survived += " (still failing at HEAD)"
        print(f"{rule:<{width}}  {located:<19}  {survived}", file=stream)

    if result.violations:
        worst = result.violations[0]
        print(
            paint(
                "dim",
                f"\nlongest-lived violation: {worst.rule} in {worst.path} — "
                f"{worst.commits_survived} commits — {_truncate(worst.message, 70)}",
            ),
            file=stream,
        )


def _truncate(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


__all__ = [
    "CommitResult",
    "ReplayError",
    "ReplayResult",
    "Span",
    "clone",
    "is_url",
    "render_text",
    "replay",
]
