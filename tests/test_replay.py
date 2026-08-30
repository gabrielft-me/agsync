"""Tests for historical replay.

Every test here runs against a git repository built commit by commit in a temp
directory (see ``replay_repo``), never against the network and never against a
repository that happens to exist on the machine running the suite.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import tempfile

import pytest
from replay_repo import build_replay_repo

from agsync.replay import TEMP_PREFIX, ReplayError, _remove_tree, is_url, replay


@pytest.fixture(scope="module")
def fixture(tmp_path_factory):
    return build_replay_repo(str(tmp_path_factory.mktemp("source") / "repo"))


@pytest.fixture(scope="module")
def result(fixture):
    return replay(fixture.root, "main")


def _leftover_temp_dirs() -> list[str]:
    return glob.glob(os.path.join(tempfile.gettempdir(), TEMP_PREFIX + "*"))


def _git(root: str, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


# ------------------------------------------------------------------ counts


def test_clean_commit_is_not_rejected(result):
    assert result.commits[0].errors == 0
    assert result.commits[0].rejected is False


def test_headline_counts_only_commits_with_errors(result, fixture):
    assert result.total == fixture.total == 6
    assert result.rejected == 5
    assert result.headline == "5 of 6 pushes would have been rejected"


def test_each_decay_is_attributed_to_the_commit_that_introduced_it(result):
    assert "protocol-files-exist" in result.commits[1].rules
    assert "unique-decision-ids" not in result.commits[1].rules
    assert "unique-decision-ids" in result.commits[2].rules


def test_a_fixed_violation_stops_being_reported(result):
    assert "protocol-files-exist" in result.commits[3].rules
    assert "protocol-files-exist" not in result.commits[4].rules
    # The duplicate ID was never fixed, so the commit is still rejected.
    assert result.commits[4].rejected


# -------------------------------------------------------------- first-seen


def test_first_seen_points_at_the_introducing_commit(result, fixture):
    assert result.first_seen["protocol-files-exist"].first_sha == fixture.shas[1]
    assert result.first_seen["unique-decision-ids"].first_sha == fixture.shas[2]


def test_a_fixed_violation_is_not_still_failing(result, fixture):
    span = result.first_seen["protocol-files-exist"]
    assert span.still_failing is False
    assert span.last_sha == fixture.shas[3]
    assert span.commits_survived == 3


def test_an_unfixed_violation_runs_to_the_tip(result, fixture):
    span = result.first_seen["unique-decision-ids"]
    assert span.still_failing is True
    assert span.last_sha == fixture.shas[5]
    assert span.commits_survived == 4
    assert span.days_survived == 3


def test_line_shifts_do_not_restart_a_violations_clock(result):
    """Commit 4 moves both violations down the file without changing them.

    If identity depended on the line, each would split into two short-lived
    violations instead of one long one — and the number the whole command
    exists to produce would be wrong.
    """
    duplicates = [span for span in result.violations if span.rule == "unique-decision-ids"]
    assert len(duplicates) == 1
    assert duplicates[0].commits_survived == 4

    protocol = [span for span in result.violations if span.rule == "protocol-files-exist"]
    assert len(protocol) == 1
    assert protocol[0].commits_survived == 3


def test_longest_lived_violation_sorts_first(result):
    assert result.violations[0].rule == "unique-decision-ids"


def test_duration_reads_in_human_units(result):
    # Introduced 2026-08-01T15:00 and last seen 2026-08-03T09:00 — 42 hours.
    assert result.first_seen["protocol-files-exist"].duration == "1 day"
    assert result.first_seen["unique-decision-ids"].duration == "3 days"


# ------------------------------------------------------------------- json


def test_json_payload_carries_the_whole_result(result):
    payload = json.loads(json.dumps(result.as_dict()))
    assert payload["total"] == 6
    assert payload["rejected"] == 5
    assert len(payload["commits"]) == 6
    assert {"sha", "short", "date", "errors", "warnings", "rules", "rejected"} <= set(
        payload["commits"][0]
    )
    assert "unique-decision-ids" in payload["first_seen"]
    assert {"first_sha", "still_failing", "commits_survived", "days_survived"} <= set(
        payload["first_seen"]["unique-decision-ids"]
    )
    assert payload["violations"]


# ------------------------------------------------------------------ safety


def test_the_source_repository_is_never_touched(fixture):
    head = _git(fixture.root, "rev-parse", "HEAD")
    status = _git(fixture.root, "status", "--porcelain")

    replay(fixture.root, "main")

    assert _git(fixture.root, "rev-parse", "HEAD") == head
    assert _git(fixture.root, "status", "--porcelain") == status
    assert _git(fixture.root, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_temp_clone_is_removed_on_success(fixture):
    before = _leftover_temp_dirs()
    replay(fixture.root, "main")
    assert _leftover_temp_dirs() == before


def test_temp_clone_is_removed_after_a_failure(fixture):
    before = _leftover_temp_dirs()
    with pytest.raises(ReplayError):
        replay(fixture.root, "no-such-branch")
    assert _leftover_temp_dirs() == before


def test_cleanup_removes_read_only_git_objects(tmp_path):
    """Git writes its object files read-only; the clone still has to go."""
    victim = tmp_path / "clone"
    (victim / "objects").mkdir(parents=True)
    obj = victim / "objects" / "ab12"
    obj.write_text("packed", encoding="utf-8")
    obj.chmod(0o444)

    _remove_tree(str(victim))
    assert not victim.exists()


def test_cleanup_is_a_no_op_on_a_path_that_is_already_gone(tmp_path):
    _remove_tree(str(tmp_path / "never-existed"))


# ------------------------------------------------------------------ inputs


def test_a_file_url_behaves_exactly_like_a_path(fixture, result):
    from_url = replay(f"file://{fixture.root}", "main")
    assert from_url.rejected == result.rejected
    assert [c.sha for c in from_url.commits] == [c.sha for c in result.commits]


def test_url_detection_covers_the_forms_git_accepts():
    assert is_url("https://github.com/o/r")
    assert is_url("file:///tmp/repo")
    assert is_url("git@github.com:o/r.git")
    assert not is_url("/Users/someone/repo")
    assert not is_url("../repo")


def test_unresolvable_ref_names_the_refs_that_exist(fixture):
    with pytest.raises(ReplayError, match="main"):
        replay(fixture.root, "no-such-branch")


def test_a_directory_that_is_not_a_repository_is_rejected(tmp_path):
    with pytest.raises(ReplayError, match="not a git repository"):
        replay(str(tmp_path), "main")


def test_a_missing_directory_is_rejected(tmp_path):
    with pytest.raises(ReplayError, match="no such directory"):
        replay(str(tmp_path / "absent"), "main")


# -------------------------------------------------------------------- cli


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", "agsync", *args],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": os.path.join(os.path.dirname(__file__), "..", "src"),
            "NO_COLOR": "1",
        },
    )


def test_cli_exits_zero_even_when_commits_would_be_rejected(fixture):
    result = _run("replay", fixture.root)
    assert result.returncode == 0
    assert "5 of 6 pushes would have been rejected" in result.stdout


def test_cli_first_seen_table_is_opt_in(fixture):
    plain = _run("replay", fixture.root)
    assert "first failed" not in plain.stdout

    detailed = _run("replay", fixture.root, "--first-seen")
    assert "first failed" in detailed.stdout
    assert "still failing at HEAD" in detailed.stdout


def test_cli_json_is_machine_readable(fixture):
    payload = json.loads(_run("replay", fixture.root, "--format", "json").stdout)
    assert payload["rejected"] == 5
    assert payload["ref"] == "main"


def test_cli_reports_operational_failure_with_exit_one(fixture):
    result = _run("replay", fixture.root, "--ref", "no-such-branch")
    assert result.returncode == 1
    assert "agsync:" in result.stderr
