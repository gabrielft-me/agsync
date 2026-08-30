"""Markdown -> :class:`Memory`.

Design rule: **normalize, don't demand.** Real agent-memory repos are written
by several different agents over weeks and the format drifts. Field sets vary
between entries, values wrap across lines, index tables use different column
orders. The parser absorbs all of that so rules can stay trivial.
"""

from __future__ import annotations

import os
import re

from .model import STATUSES, Decision, IndexRow, Memory, Task

# ``## D-025 — Title (amends D-022)`` — em dash, double hyphen or hyphen.
RE_DECISION_HEADING = re.compile(r"^##\s+(D-\d+)\s*(?:—|--|-)?\s*(.*)$")
# ``- **Status:** done`` and ``**Status:** done`` are both in the wild.
RE_FIELD = re.compile(r"^-?\s*\*\*([A-Za-z][A-Za-z ]*?):\*\*\s*(.*)$")
RE_DECISION_ID = re.compile(r"\bD-\d{3}\b")
RE_MD_LINK = re.compile(r"\[(?:[^\]]*)\]\(([^)\s]+)\)")
RE_TASK_FILE = re.compile(r"^(\d{2})-.+\.md$")
RE_TASK_NUM = re.compile(r"\b(\d{2})\b")
RE_BACKTICK_MD = re.compile(r"`([^`]+\.md)`")
RE_STATUS_BASE = re.compile(r"^([a-z][a-z-]*)")

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}

MEMORY_DIRS = ("memory", ".agsync", "docs/memory")
DECISION_NAMES = ("decisions.md", "decision-log.md", "DECISIONS.md")
PROTOCOL_NAMES = ("AGENTS.md", "agents.md", "CLAUDE.md")
EXTERNAL_LINK_PREFIXES = ("http://", "https://", "mailto:", "#")


def _read(path: str) -> list[str]:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read().splitlines()


def _first(root: str, dirs, names) -> str | None:
    for directory in dirs:
        for name in names:
            candidate = os.path.join(root, directory, name)
            if os.path.isfile(candidate):
                return os.path.relpath(candidate, root)
    return None


def _first_at_root(root: str, names) -> str | None:
    for name in names:
        candidate = os.path.join(root, name)
        if os.path.isfile(candidate):
            return name
    return None


def split_status(raw: str) -> tuple[str, str]:
    """``done (stub)`` -> ``('done', '(stub)')``.

    The base is validated against the enum; the qualifier is reported as a
    warning rather than an error, which is what makes gradual adoption
    possible in a repo that already has 27 hand-written status lines.
    """
    head = raw.strip()
    match = RE_STATUS_BASE.match(head.lower())
    if not match:
        return "", head
    base = match.group(1)
    return base, head[len(base):].strip()


def parse_fields(lines: list[str], start: int, stop: int) -> dict[str, tuple[str, int]]:
    """Collect ``**Field:**`` values between two line offsets.

    Continuation lines are folded into the preceding field: a ``**Status:**``
    value that wraps onto a second physical line is one logical value. A blank
    line closes the current field.
    """
    collected: dict[str, list] = {}
    current: str | None = None
    for offset in range(start, min(stop, len(lines))):
        raw = lines[offset]
        match = RE_FIELD.match(raw)
        if match:
            current = match.group(1).strip().lower()
            collected[current] = [match.group(2).strip(), offset + 1]
        elif not raw.strip():
            current = None
        elif current and not raw.lstrip().startswith("#"):
            collected[current][0] += " " + raw.strip()
    return {key: (value, line) for key, (value, line) in collected.items()}


def relations_from_title(title: str) -> tuple[list[str], list[str]]:
    """Extract ``(supersedes D-001, D-011)`` / ``(amends D-022)`` from a heading.

    Relationships live in prose parentheses in every repo we have seen, so this
    is the only place the graph edges can be recovered from today's format.
    """
    supersedes: list[str] = []
    amends: list[str] = []
    for chunk in re.findall(r"\(([^)]*)\)", title):
        ids = RE_DECISION_ID.findall(chunk)
        if not ids:
            continue
        lowered = chunk.lower()
        if "supersede" in lowered or "replace" in lowered:
            supersedes.extend(ids)
        elif "amend" in lowered:
            amends.extend(ids)
    return supersedes, amends


def _parse_decisions(memory: Memory, rel_path: str | None) -> None:
    if not rel_path:
        return
    lines = memory.sources[rel_path]
    headings = [
        (offset, match)
        for offset, line in enumerate(lines)
        if (match := RE_DECISION_HEADING.match(line))
    ]
    for position, (offset, match) in enumerate(headings):
        stop = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        identifier, title = match.group(1), match.group(2).strip()
        supersedes, amends = relations_from_title(title)
        decision = Decision(
            id=identifier,
            title=title,
            path=rel_path,
            line=offset + 1,
            fields=parse_fields(lines, offset, stop),
            supersedes=supersedes,
            amends=amends,
        )
        existing = memory.decisions.get(identifier)
        if existing is not None:
            memory.duplicate_decisions.append((decision, existing))
        else:
            memory.decisions[identifier] = decision


def _parse_tasks(memory: Memory, tasks_dir: str) -> None:
    absolute = os.path.join(memory.root, tasks_dir)
    if not os.path.isdir(absolute):
        return
    for name in sorted(os.listdir(absolute)):
        match = RE_TASK_FILE.match(name)
        if not match:
            continue
        rel_path = os.path.join(tasks_dir, name)
        lines = memory.sources.get(rel_path) or _read(os.path.join(absolute, name))
        memory.sources.setdefault(rel_path, lines)
        fields = parse_fields(lines, 0, len(lines))
        task = Task(num=match.group(1), path=rel_path)
        for line in lines[:5]:
            if line.startswith("# "):
                task.title = line[2:].strip()
                break
        if "status" in fields:
            task.status_raw, task.status_line = fields["status"]
            task.status_base, task.status_qualifier = split_status(task.status_raw)
        task.depends_on = RE_TASK_NUM.findall(fields.get("depends on", ("", 0))[0])
        task.blocks = RE_TASK_NUM.findall(fields.get("blocks", ("", 0))[0])
        task.decisions = RE_DECISION_ID.findall(
            fields.get("related decisions", ("", 0))[0]
        )
        memory.tasks[task.num] = task


def _parse_index(memory: Memory, rel_path: str) -> None:
    lines = memory.sources.get(rel_path)
    if lines is None:
        return
    status_column: int | None = None
    file_column = 1
    for offset, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("|"):
            status_column = None  # a table ended; forget its columns
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        lowered = [cell.lower() for cell in cells]
        if "status" in lowered:
            # Header row. Column positions differ between tables in the same
            # file, so they are resolved per table rather than assumed.
            status_column = lowered.index("status")
            file_column = lowered.index("file") if "file" in lowered else 1
            continue
        if set("".join(cells)) <= set("-: "):
            continue  # separator row
        if status_column is None or len(cells) <= status_column:
            continue
        number = cells[0].strip()
        if not number.isdigit():
            continue
        target = ""
        if file_column < len(cells):
            link = RE_MD_LINK.search(cells[file_column])
            if link:
                target = link.group(1)
        raw_status = cells[status_column]
        base, _ = split_status(raw_status)
        memory.index.append(
            IndexRow(
                num=number.zfill(2),
                target=target,
                status_raw=raw_status,
                status_base=base,
                line=offset + 1,
            )
        )


def _collect_markdown(memory: Memory) -> None:
    for base, dirs, files in os.walk(memory.root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".git")]
        for name in files:
            if not name.endswith(".md"):
                continue
            rel_path = os.path.relpath(os.path.join(base, name), memory.root)
            memory.markdown.append(rel_path)
            memory.sources[rel_path] = _read(os.path.join(base, name))
    memory.markdown.sort()


def parse(root: str) -> Memory:
    """Parse a repository into a :class:`Memory` graph."""
    memory = Memory(root=os.path.abspath(root))
    _collect_markdown(memory)

    decisions_path = _first(memory.root, MEMORY_DIRS, DECISION_NAMES)
    protocol_path = _first_at_root(memory.root, PROTOCOL_NAMES)
    if protocol_path:
        memory.protocol_path = protocol_path

    tasks_dir = "tasks" if os.path.isdir(os.path.join(memory.root, "tasks")) else ""
    index_path = os.path.join(tasks_dir, "README.md") if tasks_dir else ""
    if index_path:
        memory.index_path = index_path

    memory.surface = _memory_surface(memory, decisions_path)

    _parse_decisions(memory, decisions_path)
    if tasks_dir:
        _parse_tasks(memory, tasks_dir)
        _parse_index(memory, index_path)
    return memory


def _memory_surface(memory: Memory, decisions_path):
    """Files that constitute agent memory, as opposed to ordinary docs.

    Scoping matters: a nested fixture repo or a README that quotes an example
    decision ID must not be linted as if it were this repo's memory.
    """
    roots = ("memory/", "tasks/", ".agsync/")
    surface = []
    for path in memory.markdown:
        normalized = path.replace(os.sep, "/")
        if normalized == memory.protocol_path or normalized.startswith(roots):
            surface.append(path)
    if decisions_path and decisions_path not in surface:
        surface.append(decisions_path)
    return sorted(set(surface))


def is_external(target: str) -> bool:
    return target.startswith(EXTERNAL_LINK_PREFIXES)


__all__ = [
    "RE_BACKTICK_MD",
    "RE_DECISION_HEADING",
    "RE_DECISION_ID",
    "RE_MD_LINK",
    "STATUSES",
    "is_external",
    "parse",
    "parse_fields",
    "relations_from_title",
    "split_status",
]
