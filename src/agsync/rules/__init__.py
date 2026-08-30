"""Rule registry.

Every rule is a small, isolated function that receives the parsed
:class:`~agsync.model.Memory` and yields :class:`~agsync.model.Finding`
objects. Rules never share state and never know about each other — that is what
keeps the rule set growable without the core growing.

Adding a rule is one decorator::

    @rule("my-rule", ERROR, "One line explaining what it catches.")
    def my_rule(memory):
        yield Finding("my-rule", "path.md", 1, "what is wrong")
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ..model import ERROR, Finding, Memory

RuleFn = Callable[[Memory], Iterable[Finding]]


@dataclass(frozen=True)
class Rule:
    name: str
    default_severity: str
    description: str
    fn: RuleFn


_REGISTRY: dict[str, Rule] = {}


def rule(name: str, default_severity: str = ERROR, description: str = "") -> Callable:
    def decorator(fn: RuleFn) -> RuleFn:
        if name in _REGISTRY:
            raise ValueError(f"duplicate rule name: {name}")
        _REGISTRY[name] = Rule(name, default_severity, description.strip(), fn)
        return fn

    return decorator


def all_rules() -> list[Rule]:
    from . import decisions, links, tasks  # noqa: F401  (registers on import)

    return sorted(_REGISTRY.values(), key=lambda r: r.name)


def get(name: str) -> Rule:
    all_rules()
    return _REGISTRY[name]


__all__ = ["Rule", "all_rules", "get", "rule"]
