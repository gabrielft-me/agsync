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


def text(report: Report, stream=sys.stdout) -> None:
    color = _supports_color(stream)

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


def as_json(report: Report, stream=sys.stdout) -> None:
    payload = {
        "ok": report.ok,
        "summary": {
            "errors": len(report.errors),
            "warnings": len(report.warnings),
            "suppressed": report.suppressed,
            "decisions": len(report.memory.decisions),
            "tasks": len(report.memory.tasks),
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
