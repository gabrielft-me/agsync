"""Templates written by ``agsync init`` and ``agsync install-hooks``."""

from __future__ import annotations

import os

AGENTS_MD = """\
# Agent boot protocol

Before doing ANY work:

1. `git pull`
2. Read `memory/goal.md` — what success means for this project
3. Read `memory/decisions.md` — why the system is the way it is
4. Read `memory/state.md` — what is delivered, and what is merely written
5. Read your assigned file in `tasks/`

Before finishing:

6. Append a decision entry for anything a future agent could not infer from the diff
7. Update your task file's `status` and `## Log`
8. `git pull`, resolve conflicts, then `git push`

Never edit `memory/goal.md`. It is set by a human.

Run `agsync check` before pushing. A failing check means the memory is
inconsistent, not that your code is wrong.
"""

GOAL_MD = """\
# Goal

<!--
Written by a human, never by an agent.

State what success looks like in a way an agent can check itself against.
Decisions and tasks are the "how"; this file is the "what for". Without it,
every agent improvises its own objective function.
-->

TODO: describe the objective, the constraints, and what is explicitly out of scope.
"""

DECISIONS_MD = """\
# Decisions

Append-only. Never edit or delete an entry — supersede it with a new one.
IDs are unique and sequential. Every entry records why, not just what.

## D-001 — Memory lives in git, validated by agsync
- **Date:** TODO
- **Decision:** Agent memory is kept as markdown in this repository and
  validated by `agsync check` on every commit and push.
- **Rationale:** A memory system whose integrity depends on being maintained
  by hand degrades at exactly the speed it is used. The push is the one choke
  point every agent must pass through.
- **Consequences:** A malformed ledger fails CI. Statuses must stay inside the
  enum. New agents can trust what they read here.
"""

STATE_MD = """\
# State

Replaceable snapshot, not a journal. Keep it under 50 lines — every agent reads
it at boot, so its length is a tax on every session. History belongs in the
decision ledger and in task logs.

## Delivered and verified

## Delivered, NOT verified
<!-- Written but never executed. Be honest here; this section is what makes
     the rest of the file trustworthy. -->

## Known gaps
"""

TASKS_README = """\
# Tasks

Index of all task files. Statuses: `todo`, `in-progress`, `done`, `superseded`.

Read [`../memory/decisions.md`](../memory/decisions.md) before starting any task.

| # | File | Status |
|---|------|--------|
| 00 | [00-example.md](00-example.md) | todo |
"""

TASK_EXAMPLE = """\
# Task 00 — Example task

**Status:** todo
**Depends on:**
**Blocks:**
**Related decisions:** D-001

## Goal
What this task is for, in two sentences.

## Acceptance
A check someone else could run to confirm this is done.

## Log
<!-- Append dated entries as you work. This is where the detail that a future
     agent cannot reconstruct from the diff belongs. -->
"""

CONFIG_TOML = """\
# agsync configuration.
# Severity is config; rule logic is not. Start permissive, promote to `error`
# as the repo gets clean. Run `agsync rules` to list every rule.

# Paths never linted. Top-level keys must precede [rules] — this is TOML.
exclude = []

[rules]
status-not-qualified = "warn"
no-orphan-memory-files = "warn"
"""

HOOK_DISPATCHER = """\
#!/bin/sh
# Installed by agsync. Chains to any pre-existing hook so that husky,
# pre-commit and friends keep working.
hook_name="$(basename "$0")"
previous="@CHAIN@/$hook_name"
if [ -x "$previous" ]; then
  "$previous" "$@" || exit $?
fi
"""

PRE_COMMIT = """\
#!/bin/sh
# Installed by agsync. Remove with: git config --unset core.hooksPath
set -e

previous="@CHAIN@/pre-commit"
if [ -x "$previous" ]; then
  "$previous" "$@" || exit $?
fi

if ! command -v agsync >/dev/null 2>&1; then
  echo "agsync: not on PATH, skipping memory check" >&2
  exit 0
fi

agsync check "$(git rev-parse --show-toplevel)"
"""

FILES = (
    ("AGENTS.md", AGENTS_MD),
    ("memory/goal.md", GOAL_MD),
    ("memory/decisions.md", DECISIONS_MD),
    ("memory/state.md", STATE_MD),
    ("tasks/README.md", TASKS_README),
    ("tasks/00-example.md", TASK_EXAMPLE),
    (".agsync.toml", CONFIG_TOML),
)


def scaffold(root: str, force: bool = False) -> list[str]:
    """Write the memory skeleton. Returns the repo-relative paths created."""
    created = []
    for rel_path, content in FILES:
        path = os.path.join(root, rel_path)
        if os.path.exists(path) and not force:
            continue
        os.makedirs(os.path.dirname(path) or root, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        created.append(rel_path)
    return created
