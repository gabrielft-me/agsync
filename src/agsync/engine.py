"""Configuration, baseline filtering, and the check engine."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field, replace

from .model import ERROR, OFF, WARN, Finding, Memory
from .parser import parse
from .rules import all_rules

CONFIG_NAMES = (".agsync.toml", "agsync.toml")
BASELINE_NAME = ".agsync-baseline.json"
#: Bumped whenever the baseline file or the fingerprint inputs change, so an
#: older file is recognised and reported rather than silently resurrecting
#: every violation it was suppressing.
BASELINE_VERSION = 2
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
    #: The baseline was written by an older format and should be re-recorded.
    baseline_stale: bool = False
    #: (recorded path, current path) for findings the baseline already holds
    #: under a different path — almost always a file that was moved.
    baseline_moved: list[tuple[str, str]] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == WARN]

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class Baseline:
    """Violations a repo has chosen to accept.

    Entries record what the finding is *about* — rule, path, subject — rather
    than only its hash, so a future change to the fingerprint can re-derive
    them instead of quietly un-suppressing everything the user accepted. A
    baseline is also read by humans in a pull request; opaque hex is not.
    """

    fingerprints: set[str] = field(default_factory=set)
    #: (rule, subject) -> the paths it was recorded at. A fingerprint includes
    #: the path, so a moved file un-suppresses itself; this is what lets the
    #: report say so instead of presenting an old violation as new.
    paths: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    version: int = BASELINE_VERSION
    #: Written by an older format. Still honoured, but it cannot survive a
    #: change to the fingerprint, so the user is told to re-record it.
    stale: bool = False


def load_baseline(root: str) -> Baseline:
    path = os.path.join(root, BASELINE_NAME)
    if not os.path.isfile(path):
        return Baseline()
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)

    version = int(data.get("version", 1))
    if version >= 2:
        fingerprints = set()
        paths: dict[tuple[str, str], set[str]] = {}
        for entry in data.get("violations") or []:
            rule = entry.get("rule", "")
            path = entry.get("path", "")
            subject = entry.get("subject", "")
            fingerprints.add(Finding(rule, path, 0, "", subject=subject).fingerprint())
            paths.setdefault((rule, subject), set()).add(path)
        return Baseline(fingerprints=fingerprints, paths=paths, version=version)

    # v1 stored bare hashes. They still match today, so honour them — but they
    # carry nothing to re-derive from, which is the whole reason for v2.
    return Baseline(
        fingerprints=set(data.get("fingerprints", [])), version=1, stale=True
    )


def write_baseline(root: str, findings: list[Finding]) -> str:
    path = os.path.join(root, BASELINE_NAME)
    seen = {}
    for finding in findings:
        seen[finding.fingerprint()] = {
            "rule": finding.rule,
            "path": finding.path,
            "subject": finding.subject or finding.message,
        }
    payload = {
        "version": BASELINE_VERSION,
        "note": "Pre-existing violations, ignored by `agsync check`. "
                "Delete entries as you fix them; never add by hand.",
        "violations": [seen[key] for key in sorted(seen)],
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
    baseline = load_baseline(root) if use_baseline else Baseline()

    findings: list[Finding] = []
    suppressed = 0
    moved: list[tuple[str, str]] = []
    for rule in all_rules():
        severity = config.severity_for(rule.name, rule.default_severity)
        if severity == OFF:
            continue
        for finding in rule.fn(memory):
            if _excluded(finding.path, config.exclude):
                continue
            if finding.fingerprint() in baseline.fingerprints:
                suppressed += 1
                continue
            # Not suppressed — but is this the same violation at a new path?
            recorded = baseline.paths.get(
                (finding.rule, finding.subject or finding.message)
            )
            if recorded and finding.path not in recorded:
                moved.append((min(recorded), finding.path))
            # Config overrides the severity the rule declared for itself.
            # `replace` rather than a rebuild: a new field added to Finding
            # must not be silently dropped here, which would change the
            # fingerprint of every re-severitied finding.
            findings.append(
                finding if finding.severity == severity
                else replace(finding, severity=severity)
            )

    findings.sort(key=lambda f: (f.path, f.line, f.rule))
    return Report(
        findings=findings,
        memory=memory,
        suppressed=suppressed,
        baseline_stale=baseline.stale,
        baseline_moved=moved,
    )


def _validate_config_keys(section: dict) -> None:
    """Guard against the TOML footgun where a top-level key lands inside [rules]."""
    unknown = set(section) - {"rules", "exclude"}
    if unknown:
        raise ValueError(
            f"unknown config key(s): {', '.join(sorted(unknown))}"
        )
