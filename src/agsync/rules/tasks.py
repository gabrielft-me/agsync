"""Rules over task files and the task index."""

from __future__ import annotations

from datetime import UTC, date, datetime

from ..model import ERROR, STATUSES, WARN, Finding, Memory
from . import rule

#: How long a claim may sit before it is called stale. A guess, deliberately —
#: which is exactly why the rule that uses it warns rather than errors.
STALE_AFTER_DAYS = 7


@rule(
    "status-in-enum",
    ERROR,
    "A task status is outside the declared enum.",
)
def status_in_enum(memory: Memory):
    for task in sorted(memory.tasks.values(), key=lambda t: t.num):
        if not task.status_raw:
            yield Finding(
                "status-in-enum", task.path, 1, "no **Status:** field",
                subject=f"{task.num}:status",
            )
        elif task.status_base not in STATUSES:
            yield Finding(
                "status-in-enum",
                task.path,
                task.status_line,
                f"status {task.status_base or task.status_raw!r} is not one of "
                f"{', '.join(STATUSES)}",
                subject=f"{task.num}:status",
            )


@rule(
    "status-not-qualified",
    WARN,
    "A status carries trailing free text ('done (stub)'), so it is not machine-readable.",
)
def status_not_qualified(memory: Memory):
    for task in sorted(memory.tasks.values(), key=lambda t: t.num):
        if task.status_base in STATUSES and task.status_qualifier:
            qualifier = task.status_qualifier
            if len(qualifier) > 50:
                qualifier = qualifier[:47] + "..."
            yield Finding(
                "status-not-qualified",
                task.path,
                task.status_line,
                f"status is qualified with free text ({qualifier!r}); "
                f"move the caveat into the body and keep the status machine-readable",
                WARN,
                subject=f"{task.num}:status-qualifier",
            )


@rule(
    "index-matches-files",
    ERROR,
    "The task index disagrees with the task files, or omits one entirely.",
)
def index_matches_files(memory: Memory):
    indexed = {row.num for row in memory.index}
    for num, task in sorted(memory.tasks.items()):
        if num not in indexed:
            yield Finding(
                "index-matches-files",
                memory.index_path,
                0,
                f"task {num} exists ({task.path}) but is absent from the index — "
                f"invisible to anyone reading it",
                subject=f"{num}:absent-from-index",
            )
    for row in memory.index:
        task = memory.tasks.get(row.num)
        if task is None:
            yield Finding(
                "index-matches-files",
                memory.index_path,
                row.line,
                f"index lists task {row.num}, but no matching file exists",
                subject=f"{row.num}:no-such-file",
            )
            continue
        if row.status_base and task.status_base and row.status_base != task.status_base:
            yield Finding(
                "index-matches-files",
                memory.index_path,
                row.line,
                f"task {row.num}: index says {row.status_base!r}, "
                f"file says {task.status_base!r}",
                subject=f"{row.num}:status-divergence",
            )


@rule(
    "task-refs-resolve",
    ERROR,
    "A task depends on or blocks a task number that does not exist.",
)
def task_refs_resolve(memory: Memory):
    for task in sorted(memory.tasks.values(), key=lambda t: t.num):
        for kind, numbers in (("depends on", task.depends_on), ("blocks", task.blocks)):
            for number in sorted(set(numbers)):
                if number not in memory.tasks:
                    yield Finding(
                        "task-refs-resolve",
                        task.path,
                        1,
                        f"{kind} task {number}, which does not exist",
                        subject=f"{task.num}:{kind}:{number}",
                    )


@rule(
    "no-dependency-cycles",
    ERROR,
    "Task dependencies form a cycle, so no valid execution order exists.",
)
def no_dependency_cycles(memory: Memory):
    graph = {num: [d for d in task.depends_on if d in memory.tasks]
             for num, task in memory.tasks.items()}
    state: dict = {}
    reported: set = set()

    def walk(node: str, trail: list) -> None:
        state[node] = "open"
        for neighbour in graph.get(node, []):
            if state.get(neighbour) == "open":
                cycle = trail[trail.index(neighbour):] + [neighbour]
                reported.add(" -> ".join(cycle))
            elif neighbour not in state:
                walk(neighbour, trail + [neighbour])
        state[node] = "closed"

    for num in sorted(graph):
        if num not in state:
            walk(num, [num])

    for cycle in sorted(reported):
        first = cycle.split(" -> ")[0]
        yield Finding(
            "no-dependency-cycles",
            memory.tasks[first].path,
            1,
            f"dependency cycle: {cycle}",
            subject=cycle,
        )


@rule(
    "blocked-task-not-done",
    WARN,
    "A task is done while a task it depends on is not.",
)
def blocked_task_not_done(memory: Memory):
    for task in sorted(memory.tasks.values(), key=lambda t: t.num):
        if task.status_base != "done":
            continue
        for number in sorted(set(task.depends_on)):
            upstream = memory.tasks.get(number)
            if upstream and upstream.status_base in ("todo", "in-progress"):
                yield Finding(
                    "blocked-task-not-done",
                    task.path,
                    task.status_line,
                    f"marked done, but depends on task {number} which is "
                    f"{upstream.status_base!r}",
                    WARN,
                    subject=f"{task.num}:blocked-by:{number}",
                )


# ------------------------------------------------------------------- claims
#
# Claiming is a commit. An agent writes its name into the task file and pushes
# that before starting work; if two agents claim at once, the loser's push is
# rejected and it learns there is an owner. Git is the lock, so these rules only
# have to notice when the record of a claim stops being usable.


@rule(
    "in-progress-needs-owner",
    ERROR,
    "A task is in progress with nobody named as its owner.",
)
def in_progress_needs_owner(memory: Memory):
    for task in sorted(memory.tasks.values(), key=lambda t: t.num):
        if task.status_base == "in-progress" and not task.owner:
            yield Finding(
                "in-progress-needs-owner",
                task.path,
                task.status_line,
                "in progress with no **Owner:** — another agent cannot tell "
                "whether this is being worked on or was abandoned",
                subject=f"{task.num}:owner",
            )


@rule(
    "stale-claim",
    WARN,
    "A task has been claimed for a long time, or its claim carries no usable date.",
)
def stale_claim(memory: Memory):
    # UTC, not local: agents claiming from different machines must agree on
    # how old a claim is, and a claim date carries no timezone.
    today = datetime.now(UTC).date()
    for task in sorted(memory.tasks.values(), key=lambda t: t.num):
        if task.status_base != "in-progress" or not task.owner:
            continue
        claimed = _as_date(task.claimed_at)
        if claimed is None:
            yield Finding(
                "stale-claim",
                task.path,
                task.claim_line,
                f"claimed by {task.owner!r} with no readable **Claimed at:** "
                f"date, so nobody can tell whether the claim is still live",
                WARN,
                subject=f"{task.num}:claim-date",
            )
            continue
        age = (today - claimed).days
        if age > STALE_AFTER_DAYS:
            yield Finding(
                "stale-claim",
                task.path,
                task.claim_line,
                f"claimed by {task.owner!r} {age} days ago and still in "
                f"progress; if that agent is gone the task is stuck",
                WARN,
                subject=f"{task.num}:stale",
            )


@rule(
    "one-claim-per-owner",
    WARN,
    "An owner holds more than one task in progress at the same time.",
)
def one_claim_per_owner(memory: Memory):
    held: dict[str, list] = {}
    for task in sorted(memory.tasks.values(), key=lambda t: t.num):
        if task.status_base == "in-progress" and task.owner:
            held.setdefault(task.owner, []).append(task)
    for owner, tasks in sorted(held.items()):
        if len(tasks) < 2:
            continue
        numbers = ", ".join(task.num for task in tasks)
        for task in tasks:
            yield Finding(
                "one-claim-per-owner",
                task.path,
                task.claim_line,
                f"{owner!r} holds {len(tasks)} tasks at once ({numbers}); "
                f"each claim tells other agents this one is being worked on",
                WARN,
                subject=f"{owner}:multiple-claims",
            )


def _as_date(raw: str):
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None
