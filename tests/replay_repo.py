"""Builds a real git repository, with real commits, for the replay tests.

Replay's entire job is reading history, so a fixture that faked git would test
nothing that matters. This makes actual commits in a temp directory with pinned
author dates, which keeps shas ordered and day-spans constant no matter when
the suite runs — and keeps the tests off the network and away from whatever
repositories happen to exist on the machine.

The shape of the history is deliberate. It contains a violation that is
introduced and later fixed, one that is introduced and never fixed, and an edit
that shifts line numbers under both without changing either.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from agsync.scaffold import scaffold

NAME = "Replay Fixture"
EMAIL = "fixture@example.invalid"

UNSATISFIABLE_STEP = "6. Read `memory/roadmap.md` — the delivery plan\n"

DUPLICATE_ENTRY = """
## D-001 — A second, unrelated decision reusing an existing ID
- **Date:** 2026-08-02
- **Decision:** Something else entirely, filed under an ID that is already taken.
- **Consequences:** Every reference to D-001 is now ambiguous.
"""

INTERLEAVED_NOTE = """
<!-- An ordinary edit landing between the two definitions. It shifts the
     duplicate's line number without changing what is wrong. -->
"""


@dataclass(frozen=True)
class Fixture:
    root: str
    shas: tuple[str, ...]

    @property
    def total(self) -> int:
        return len(self.shas)


def _git(root: str, *args: str, date: str | None = None) -> str:
    env = dict(os.environ)
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    result = subprocess.run(
        [
            "git",
            "-c",
            f"user.name={NAME}",
            "-c",
            f"user.email={EMAIL}",
            "-c",
            "commit.gpgsign=false",
            # The machine running the suite may have agsync's own hooks
            # installed globally; a fixture must not run them.
            "-c",
            "core.hooksPath=",
            *args,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return result.stdout.strip()


def _write(root: str, rel_path: str, content: str) -> None:
    path = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(path) or root, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _read(root: str, rel_path: str) -> str:
    with open(os.path.join(root, rel_path), encoding="utf-8") as handle:
        return handle.read()


def _commit(root: str, message: str, date: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "--no-verify", "-m", message, date=date)
    return _git(root, "rev-parse", "HEAD")


def build_replay_repo(root: str) -> Fixture:
    """Six commits: clean, two decays, a line shift, a fix, and a tip."""
    os.makedirs(root, exist_ok=True)
    _git(root, "init", "--quiet", "--initial-branch=main")

    shas = []

    # 1. A clean scaffold. Nothing fires here, which is what makes the rest
    #    of the history meaningful.
    scaffold(root)
    shas.append(_commit(root, "scaffold memory structure", "2026-08-01T09:00:00+00:00"))

    # 2. The boot protocol starts pointing at a file nobody ever wrote.
    _write(root, "AGENTS.md", _read(root, "AGENTS.md") + UNSATISFIABLE_STEP)
    shas.append(_commit(root, "add roadmap step to boot protocol", "2026-08-01T15:00:00+00:00"))

    # 3. A decision ID is reused.
    _write(root, "memory/decisions.md", _read(root, "memory/decisions.md") + DUPLICATE_ENTRY)
    shas.append(_commit(root, "log a decision", "2026-08-02T09:00:00+00:00"))

    # 4. An unrelated edit above both violations. Line numbers move; neither
    #    violation changes, so neither clock may restart.
    _write(root, "AGENTS.md", "<!-- preamble -->\n" + _read(root, "AGENTS.md"))
    ledger = _read(root, "memory/decisions.md")
    head, marker, tail = ledger.partition(DUPLICATE_ENTRY)
    _write(root, "memory/decisions.md", head + INTERLEAVED_NOTE + marker + tail)
    shas.append(_commit(root, "editorial pass", "2026-08-03T09:00:00+00:00"))

    # 5. The missing file is finally written; only that violation clears.
    _write(root, "memory/roadmap.md", "# Roadmap\n\nReferenced by the boot protocol.\n")
    shas.append(_commit(root, "write the roadmap", "2026-08-04T09:00:00+00:00"))

    # 6. Tip. The duplicate ID is still here.
    _write(root, "memory/state.md", _read(root, "memory/state.md") + "\n<!-- touched -->\n")
    shas.append(_commit(root, "update state", "2026-08-05T09:00:00+00:00"))

    return Fixture(root=root, shas=tuple(shas))
