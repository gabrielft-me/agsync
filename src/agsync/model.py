"""Normalized data model.

Rules operate exclusively on these objects, never on raw markdown. Keeping the
parser and the rules separated by a stable structure is what lets the parser
absorb messy real-world formats without every rule learning about them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

#: The only task statuses that carry machine-readable meaning.
STATUSES = ("todo", "in-progress", "done", "superseded")

ERROR = "error"
WARN = "warn"
OFF = "off"


@dataclass(frozen=True)
class Finding:
    """A single rule violation.

    The report contract is deliberately fixed and minimal: every consumer
    (text output, JSON, GitHub annotations, editors) derives from these five
    fields alone.
    """

    rule: str
    path: str
    line: int
    message: str
    severity: str = ERROR

    def fingerprint(self) -> str:
        """Stable identity across line shifts, used by the baseline file.

        Deliberately excludes ``line`` so that unrelated edits above a known
        violation do not resurrect it as "new".
        """
        raw = f"{self.rule}\x00{self.path}\x00{self.message}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def as_dict(self) -> dict:
        return {
            "rule": self.rule,
            "path": self.path,
            "line": self.line,
            "message": self.message,
            "severity": self.severity,
            "fingerprint": self.fingerprint(),
        }


@dataclass
class Decision:
    """One entry in the decision ledger."""

    id: str
    title: str
    path: str
    line: int
    #: field name (lowercased) -> (folded value, line number)
    fields: dict[str, tuple[str, int]] = field(default_factory=dict)
    supersedes: list[str] = field(default_factory=list)
    amends: list[str] = field(default_factory=list)


@dataclass
class Task:
    """One task file.

    ``status_base`` is the enum candidate; ``status_qualifier`` is whatever
    free text trailed it. Splitting them is what lets an existing repo become
    parseable without rewriting every file at once.
    """

    num: str
    path: str
    line: int = 1
    title: str = ""
    status_raw: str = ""
    status_base: str = ""
    status_qualifier: str = ""
    status_line: int = 1
    depends_on: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)


@dataclass
class IndexRow:
    """One row of a task table in the index file."""

    num: str
    target: str
    status_raw: str
    status_base: str
    line: int


@dataclass
class Memory:
    """The parsed memory graph handed to every rule."""

    root: str
    decisions: dict[str, Decision] = field(default_factory=dict)
    #: (redefinition, first definition) pairs — duplicates never enter ``decisions``
    duplicate_decisions: list[tuple[Decision, Decision]] = field(default_factory=list)
    tasks: dict[str, Task] = field(default_factory=dict)
    index: list[IndexRow] = field(default_factory=list)
    index_path: str = "tasks/README.md"
    protocol_path: str = "AGENTS.md"
    #: every markdown file in the repo, repo-relative
    markdown: list[str] = field(default_factory=list)
    #: the subset that is actually agent memory — the protocol file and
    #: anything under memory/ or tasks/. A ``D-021`` in a README is prose;
    #: the same token inside the memory surface is a reference that must
    #: resolve. Rules that scan text use this, never ``markdown``.
    surface: list[str] = field(default_factory=list)
    #: path -> lines, populated lazily by the parser and reused by rules
    sources: dict[str, list[str]] = field(default_factory=dict)

    def lines(self, path: str) -> list[str]:
        return self.sources.get(path, [])
