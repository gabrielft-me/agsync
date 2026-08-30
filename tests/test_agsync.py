"""Tests.

The ``decayed`` fixture is a reduction of a real agent-memory repository that
rotted in two days of multi-agent use. Every bug it contains was found in
production, not invented: a duplicated decision ID, a boot protocol pointing at
a file that never existed, statuses drifting out of the enum, and an index that
disagrees with the task files.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from agsync import check, parse
from agsync.engine import Config, write_baseline
from agsync.parser import parse_fields, relations_from_title, split_status
from agsync.rules import all_rules
from agsync.scaffold import scaffold

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
DECAYED = os.path.join(FIXTURES, "decayed")


@pytest.fixture(scope="module")
def decayed():
    return check(DECAYED, use_baseline=False)


def rules_fired(report):
    return {finding.rule for finding in report.findings}


# --------------------------------------------------------------- parser

def test_split_status_separates_enum_from_free_text():
    assert split_status("done") == ("done", "")
    assert split_status("done (stub)") == ("done", "(stub)")
    assert split_status("superseded → 11..15") == ("superseded", "→ 11..15")


def test_fields_fold_wrapped_continuation_lines():
    lines = [
        "**Status:** done (backend noop path smoke-tested;",
        "2026-08-08)",
        "**Depends on:** 25",
    ]
    fields = parse_fields(lines, 0, len(lines))
    assert fields["status"][0] == "done (backend noop path smoke-tested; 2026-08-08)"
    assert fields["depends on"][0] == "25"


def test_relations_are_recovered_from_heading_parentheses():
    assert relations_from_title("Title (amends D-022)") == ([], ["D-022"])
    assert relations_from_title("Title (supersedes D-001, D-011)") == (
        ["D-001", "D-011"],
        [],
    )
    assert relations_from_title("Title with (an unrelated aside)") == ([], [])


def test_parser_reads_tables_with_different_column_orders():
    memory = parse(DECAYED)
    # First table is `| # | File | Group | Status |`, second is `| # | File | Status |`.
    assert {row.num for row in memory.index} == {"09", "25"}
    assert {row.status_base for row in memory.index} == {"done", "todo"}


def test_duplicate_ids_do_not_silently_overwrite_each_other():
    memory = parse(DECAYED)
    assert len(memory.duplicate_decisions) == 1
    duplicate, first = memory.duplicate_decisions[0]
    assert duplicate.id == "D-021"
    assert first.line < duplicate.line


# ---------------------------------------------------------------- rules

def test_every_registered_rule_has_a_description():
    for rule in all_rules():
        assert rule.description, f"{rule.name} has no description"
        assert rule.default_severity in ("error", "warn")


def test_duplicate_decision_id_is_reported_with_both_locations(decayed):
    finding = next(f for f in decayed.findings if f.rule == "unique-decision-ids")
    assert "D-021" in finding.message
    assert "first defined at line" in finding.message


def test_unsatisfiable_boot_step_is_reported(decayed):
    finding = next(f for f in decayed.findings if f.rule == "protocol-files-exist")
    assert "goal.md" in finding.message
    assert finding.path == "AGENTS.md"


def test_orphan_task_is_reported(decayed):
    messages = [f.message for f in decayed.findings if f.rule == "index-matches-files"]
    assert any("task 26 exists" in m for m in messages)


def test_index_status_divergence_is_reported(decayed):
    messages = [f.message for f in decayed.findings if f.rule == "index-matches-files"]
    assert any("index says 'todo', file says 'done'" in m for m in messages)


def test_unresolved_relation_is_reported(decayed):
    finding = next(f for f in decayed.findings if f.rule == "relations-resolve")
    assert "D-019" in finding.message


def test_broken_link_is_reported(decayed):
    targets = [f.message for f in decayed.findings if f.rule == "links-resolve"]
    assert any("current-state.md" in m for m in targets)


def test_dependency_cycle_is_detected(decayed):
    finding = next(f for f in decayed.findings if f.rule == "no-dependency-cycles")
    assert "->" in finding.message


def test_missing_date_is_reported(decayed):
    finding = next(f for f in decayed.findings if f.rule == "decision-has-date")
    assert "D-027" in finding.message


def test_qualified_status_is_a_warning_not_an_error(decayed):
    qualified = [f for f in decayed.findings if f.rule == "status-not-qualified"]
    assert qualified
    assert all(f.severity == "warn" for f in qualified)


def test_decayed_fixture_fails_the_check(decayed):
    assert not decayed.ok
    assert len(decayed.errors) >= 6


# --------------------------------------------------------------- engine

def test_scaffolded_repo_passes_cleanly(tmp_path):
    created = scaffold(str(tmp_path))
    assert "AGENTS.md" in created
    report = check(str(tmp_path))
    assert report.ok, [f.message for f in report.errors]


def test_scaffold_is_idempotent(tmp_path):
    scaffold(str(tmp_path))
    assert scaffold(str(tmp_path)) == []


def test_config_can_downgrade_a_rule_to_warning():
    config = Config.from_dict({"rules": {"unique-decision-ids": "warn"}})
    report = check(DECAYED, config, use_baseline=False)
    finding = next(f for f in report.findings if f.rule == "unique-decision-ids")
    assert finding.severity == "warn"


def test_config_can_disable_a_rule():
    config = Config.from_dict({"rules": {"unique-decision-ids": "off"}})
    report = check(DECAYED, config, use_baseline=False)
    assert "unique-decision-ids" not in rules_fired(report)


def test_invalid_severity_is_rejected_loudly():
    with pytest.raises(ValueError, match="invalid severity"):
        Config.from_dict({"rules": {"unique-decision-ids": "fatal"}})


def test_baseline_suppresses_existing_findings_but_not_new_ones(tmp_path):
    import shutil

    repo = tmp_path / "repo"
    shutil.copytree(DECAYED, repo)
    before = check(str(repo), use_baseline=False)
    write_baseline(str(repo), before.findings)

    after = check(str(repo))
    assert after.ok
    assert after.suppressed == len(before.findings)

    (repo / "tasks" / "27-new.md").write_text(
        "# Task 27 — New\n\n**Status:** shipped\n", encoding="utf-8"
    )
    regressed = check(str(repo))
    assert not regressed.ok
    assert "status-in-enum" in rules_fired(regressed)


def test_fingerprint_survives_line_shifts(tmp_path):
    import shutil

    repo = tmp_path / "repo"
    shutil.copytree(DECAYED, repo)
    baseline = check(str(repo), use_baseline=False)
    write_baseline(str(repo), baseline.findings)

    protocol = repo / "AGENTS.md"
    protocol.write_text("<!-- inserted -->\n" + protocol.read_text(), encoding="utf-8")

    assert check(str(repo)).ok


# ------------------------------------------------------------------ cli

def _run(*args, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "agsync", *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ, "PYTHONPATH": os.path.join(os.path.dirname(__file__), "..", "src")},
    )


def test_cli_exits_nonzero_on_errors():
    assert _run("check", DECAYED).returncode == 1


def test_cli_warn_only_always_exits_zero():
    assert _run("check", DECAYED, "--warn-only").returncode == 0


def test_cli_json_output_is_machine_readable():
    result = _run("check", DECAYED, "--format", "json")
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["summary"]["errors"] >= 6
    assert {"rule", "path", "line", "message", "severity", "fingerprint"} <= set(
        payload["findings"][0]
    )


def test_cli_github_format_emits_workflow_commands():
    result = _run("check", DECAYED, "--format", "github")
    assert "::error file=" in result.stdout
    assert "title=agsync/" in result.stdout


def test_cli_rules_lists_the_registry():
    result = _run("rules")
    assert "unique-decision-ids" in result.stdout
    assert "protocol-files-exist" in result.stdout
