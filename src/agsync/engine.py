"""Configuration, baseline filtering, and the check engine."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field

from .model import ERROR, OFF, WARN, Finding, Memory
from .parser import parse
from .rules import all_rules

CONFIG_NAMES = (".agsync.toml", "agsync.toml")
BASELINE_NAME = ".agsync-baseline.json"
VALID_SEVERITIES = (ERROR, WARN, OFF)


@dataclass
class Config:
    """Severity per rule, plus paths to ignore.

    Severity is configuration; rule logic is not. This split is what allows a
    repo with existing violations to adopt the tool gradually — turn everything
    to ``warn``, then promote rules to ``error`` one at a time.
    """

    severities: dict[str, str] = field(default_factory=dict)
    exclude: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, root: str) -> Config:
        for name in CONFIG_NAMES:
            path = os.path.join(root, name)
            if os.path.isfile(path):
                with open(path, "rb") as handle:
                    data = tomllib.load(handle)
                return cls.from_dict(data)
        return cls()

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        section = data.get("tool", {}).get("agsync", data)
        _validate_config_keys(section)
        severities = {}
        for name, value in (section.get("rules") or {}).items():
            value = str(value).lower()
            if value not in VALID_SEVERITIES:
                raise ValueError(
                    f"invalid severity {value!r} for rule {name!r}; "
                    f"expected one of {', '.join(VALID_SEVERITIES)}"
                )
            severities[name] = value
        return cls(severities=severities, exclude=list(section.get("exclude") or []))

    def severity_for(self, rule_name: str, default: str) -> str:
        return self.severities.get(rule_name, default)


@dataclass
class Report:
    findings: list[Finding]
    memory: Memory
    suppressed: int = 0

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == WARN]

    @property
    def ok(self) -> bool:
        return not self.errors


def load_baseline(root: str) -> set:
    path = os.path.join(root, BASELINE_NAME)
    if not os.path.isfile(path):
        return set()
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return set(data.get("fingerprints", []))


def write_baseline(root: str, findings: list[Finding]) -> str:
    path = os.path.join(root, BASELINE_NAME)
    payload = {
        "version": 1,
        "note": "Pre-existing violations, ignored by `agsync check`. "
                "Delete entries as you fix them; never add by hand.",
        "fingerprints": sorted({f.fingerprint() for f in findings}),
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    return path


def _excluded(path: str, patterns: list[str]) -> bool:
    normalized = path.replace(os.sep, "/")
    return any(
        normalized == pattern or normalized.startswith(pattern.rstrip("/") + "/")
        for pattern in patterns
    )


def check(root: str, config: Config | None = None, use_baseline: bool = True) -> Report:
    """Parse ``root`` and run every enabled rule over it."""
    config = config or Config.load(root)
    memory = parse(root)
    baseline = load_baseline(root) if use_baseline else set()

    findings: list[Finding] = []
    suppressed = 0
    for rule in all_rules():
        severity = config.severity_for(rule.name, rule.default_severity)
        if severity == OFF:
            continue
        for finding in rule.fn(memory):
            if _excluded(finding.path, config.exclude):
                continue
            if finding.fingerprint() in baseline:
                suppressed += 1
                continue
            # Config overrides the severity the rule declared for itself.
            findings.append(
                finding if finding.severity == severity
                else Finding(finding.rule, finding.path, finding.line,
                             finding.message, severity)
            )

    findings.sort(key=lambda f: (f.path, f.line, f.rule))
    return Report(findings=findings, memory=memory, suppressed=suppressed)


def _validate_config_keys(section: dict) -> None:
    """Guard against the TOML footgun where a top-level key lands inside [rules]."""
    unknown = set(section) - {"rules", "exclude"}
    if unknown:
        raise ValueError(
            f"unknown config key(s): {', '.join(sorted(unknown))}"
        )
