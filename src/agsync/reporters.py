"""Output formats.

All of them derive from the same :class:`~agsync.model.Finding` contract, so
adding a format never touches a rule.
"""

from __future__ import annotations

import json
import os
import sys

from .engine import Report
from .model import ERROR

_COLORS = {"error": "\033[31m", "warn": "\033[33m", "reset": "\033[0m",
           "dim": "\033[2m", "bold": "\033[1m"}


def _supports_color(stream) -> bool:
    return (
        hasattr(stream, "isatty")
        and stream.isatty()
        and os.environ.get("NO_COLOR") is None
        and os.environ.get("TERM") != "dumb"
    )


NO_MEMORY = (
    "No agent memory found — nothing to check.\n"
    "Looked for a protocol file (AGENTS.md), memory/ and tasks/.\n"
    "Run `agsync init` to scaffold one."
)


def text(report: Report, stream=sys.stdout) -> None:
    color = _supports_color(stream)

    # A repo with no memory at all must not render as a clean pass. "0 errors"
    # on an empty repo is the same silent success this tool exists to catch.
    if not report.memory.surface and not report.findings:
        print(NO_MEMORY, file=stream)
        return

    def paint(token: str, text_: str) -> str:
        return f"{_COLORS[token]}{text_}{_COLORS['reset']}" if color else text_

    current_path = None
    for finding in report.findings:
        if finding.path != current_path:
            current_path = finding.path
            print(paint("bold", finding.path), file=stream)
        location = f"{finding.line}" if finding.line else "-"
        print(
            f"  {location:>4}  {paint(finding.severity, finding.severity):<7} "
            f"{paint('dim', finding.rule)}  {finding.message}",
            file=stream,
        )

    memory = report.memory
    print(
        f"\n{len(memory.decisions)} decisions, {len(memory.tasks)} tasks, "
        f"{len(memory.index)} index rows",
        file=stream,
    )
    summary = f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)"
    if report.suppressed:
        summary += f", {report.suppressed} suppressed by baseline"
    print(paint("error" if report.errors else "reset", summary), file=stream)
    if report.baseline_moved:
        was, now = report.baseline_moved[0]
        count = len(report.baseline_moved)
        subject = "1 finding is" if count == 1 else f"{count} findings are"
        print(
            paint("warn", f"{subject} already in "
                          f"the baseline under a different path ({was} -> {now}). "
                          f"A fingerprint includes the path, so moving a file "
                          f"un-suppresses it. Re-record with `agsync check --baseline`."),
            file=stream,
        )
    if report.baseline_stale:
        print(
            paint("warn", "baseline is in an older format; re-record it with "
                          "`agsync check --baseline` so it survives an upgrade"),
            file=stream,
        )


def as_json(report: Report, stream=sys.stdout) -> None:
    payload = {
        "ok": report.ok,
        "summary": {
            "errors": len(report.errors),
            "warnings": len(report.warnings),
            "suppressed": report.suppressed,
            "decisions": len(report.memory.decisions),
            "tasks": len(report.memory.tasks),
            "memory_found": bool(report.memory.surface),
        },
        "findings": [finding.as_dict() for finding in report.findings],
    }
    json.dump(payload, stream, indent=2)
    stream.write("\n")


def github(report: Report, stream=sys.stdout) -> None:
    """GitHub Actions workflow commands — renders inline on the PR diff."""
    for finding in report.findings:
        level = "error" if finding.severity == ERROR else "warning"
        message = finding.message.replace("\n", " ")
        line = max(finding.line, 1)
        print(
            f"::{level} file={finding.path},line={line},"
            f"title=agsync/{finding.rule}::{message}",
            file=stream,
        )
    print(
        f"::notice::agsync: {len(report.errors)} error(s), "
        f"{len(report.warnings)} warning(s)",
        file=stream,
    )


FORMATS = {"text": text, "json": as_json, "github": github}
