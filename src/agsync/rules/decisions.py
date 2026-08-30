"""Rules over the decision ledger and its reference graph."""

from __future__ import annotations

from ..model import ERROR, Finding, Memory
from ..parser import RE_DECISION_HEADING, RE_DECISION_ID
from . import rule


@rule(
    "unique-decision-ids",
    ERROR,
    "A decision ID is defined more than once, making every reference to it ambiguous.",
)
def unique_decision_ids(memory: Memory):
    for duplicate, first in memory.duplicate_decisions:
        references = _reference_count(memory, duplicate.id)
        yield Finding(
            "unique-decision-ids",
            duplicate.path,
            duplicate.line,
            f"{duplicate.id} is redefined here (first defined at line {first.line}); "
            f"{references} reference(s) to {duplicate.id} are now ambiguous",
        )


@rule(
    "decision-refs-resolve",
    ERROR,
    "A D-xxx token is referenced somewhere in the repo but never defined in the ledger.",
)
def decision_refs_resolve(memory: Memory):
    known = set(memory.decisions)
    for path in memory.surface:
        for offset, line in enumerate(memory.lines(path)):
            if RE_DECISION_HEADING.match(line):
                continue  # this line *is* a definition
            for identifier in sorted(set(RE_DECISION_ID.findall(line))):
                if identifier not in known:
                    yield Finding(
                        "decision-refs-resolve",
                        path,
                        offset + 1,
                        f"{identifier} is referenced but never defined in the ledger",
                    )


@rule(
    "relations-resolve",
    ERROR,
    "A supersedes/amends relationship points at a decision that does not exist.",
)
def relations_resolve(memory: Memory):
    known = set(memory.decisions)
    for decision in memory.decisions.values():
        for kind, targets in (("supersedes", decision.supersedes), ("amends", decision.amends)):
            for target in targets:
                if target not in known:
                    yield Finding(
                        "relations-resolve",
                        decision.path,
                        decision.line,
                        f"{decision.id} {kind} {target}, which is not defined",
                    )


@rule(
    "no-self-reference",
    ERROR,
    "A decision supersedes or amends itself.",
)
def no_self_reference(memory: Memory):
    for decision in memory.decisions.values():
        if decision.id in decision.supersedes + decision.amends:
            yield Finding(
                "no-self-reference",
                decision.path,
                decision.line,
                f"{decision.id} refers to itself",
            )


@rule(
    "decision-has-date",
    ERROR,
    "A decision entry carries no Date field, so the ledger cannot be ordered.",
)
def decision_has_date(memory: Memory):
    for decision in memory.decisions.values():
        if "date" not in decision.fields:
            yield Finding(
                "decision-has-date",
                decision.path,
                decision.line,
                f"{decision.id} has no **Date:** field",
            )


def _reference_count(memory: Memory, identifier: str) -> int:
    """Count non-defining mentions of a decision ID across the whole repo."""
    count = 0
    for path in memory.surface:
        for line in memory.lines(path):
            if RE_DECISION_HEADING.match(line):
                continue
            count += sum(1 for found in RE_DECISION_ID.findall(line) if found == identifier)
    return count
