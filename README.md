# agsync

**ESLint for your agents' memory — because the decision log your AI agents write is already lying to them.**

[![CI](https://github.com/gabrielft-me/agsync/actions/workflows/ci.yml/badge.svg)](https://github.com/gabrielft-me/agsync/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/agsync.svg)](https://pypi.org/project/agsync/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

```console
$ agsync check
AGENTS.md
     6  error   protocol-files-exist  boot protocol requires 'memory/goal.md', which does not
                                      exist — this step is unsatisfiable and every session skips it
memory/decisions.md
    11  error   unique-decision-ids   D-021 is redefined here (first defined at line 7);
                                      7 reference(s) to D-021 are now ambiguous
    88  error   relations-resolve     D-027 supersedes D-019, which is not defined
tasks/25-search-indexing.md
     3  warn    status-not-qualified  status is qualified with free text ('(all tiers except the
                                      edge cache)'); keep the status machine-readable
tasks/README.md
     -  error   index-matches-files   task 26 exists (tasks/26-webhook-retries.md) but is absent
                                      from the index — invisible to anyone reading it
    14  error   index-matches-files   task 25: index says 'todo', file says 'done'

27 decisions, 26 tasks, 25 index rows
9 error(s), 4 warning(s)
```

---

## The problem

You gave your agents a memory. `AGENTS.md` with a boot protocol, a numbered
decision log, a task queue. Every session starts by reading it. It is the only
thing standing between a new agent and a cold start.

**Nothing checks that it is true.**

Markdown never fails. A decision ID reused for a second, unrelated decision. A
boot step pointing at a file nobody ever wrote. A status of `done (stub)`. An
index row that disagrees with the task file it names. Every one of these parses,
renders, and reviews perfectly well, and every one is read as ground truth by the
next agent that boots.

Four things make the decay invisible, and they compound:

- **The reference graph is prose.** `D-021` is a bare token, not a link. Nothing
  resolves it, so nothing notices when a second entry claims the same ID and
  every existing reference to it silently becomes ambiguous.
- **Contradictions live in separate files.** The index says `todo`, the task file
  says `done`. The protocol says "read this", and the file was never created. No
  single diff contains both halves, so no review can catch it.
- **Every writer has a partial view.** Each session is a different agent, and
  each edit is locally reasonable. Rot is what locally reasonable edits add up to
  when nothing holds the whole.
- **A false memory looks exactly like a true one.** No crash, no red test. The
  agent reads a confident, well-formatted, wrong statement and acts on it.

The failure is not hypothetical and it is not slow. A repo written by several
agents over a couple of days will already contain several of these.

> A memory system whose integrity depends on being maintained by hand degrades
> at exactly the speed you use it.

The design is not the problem — `AGENTS.md` plus a decision log is the right
shape. The problem is that nothing enforces it.

## The fix is not a smarter agent

Agents self-report badly. They tell you what they *intended* to do, not what
they did, and every harness writes in a different dialect. "Be more careful" does
not survive a new agent joining.

`git push` is a choke point nobody can skip. Put the validation there.

## Install

```bash
pip install agsync
```

Python 3.11+. **Zero dependencies** — the whole thing is stdlib, so it runs in a
git hook without a virtualenv surprise.

## Use

```bash
agsync check                    # lint the repo
agsync check --warn-only        # first run: see everything, fail nothing
agsync check --baseline         # accept today's mess, fail on new mess
agsync check --no-baseline      # what an agent runs at boot: everything, unsuppressed
agsync check --format github    # inline annotations on the PR diff
agsync check --format json      # for your own tooling
agsync replay <repo>            # how many past pushes the gate would have stopped
agsync rules                    # what it checks and why
agsync init                     # scaffold a memory structure from scratch
agsync install-hooks            # gate commits locally
```

### Adopting an existing repo

Do not turn everything on at once — a linter that rejects most of your pushes on
day one gets uninstalled on day one.

```bash
agsync check --warn-only    # 1. look at the damage
agsync check --baseline     # 2. freeze it; new violations now fail
```

The baseline records a fingerprint per violation that survives line shifts, so
unrelated edits above a known problem don't resurrect it as "new". Delete
entries from `.agsync-baseline.json` as you fix them.

A fingerprint does include the **path**, so moving or renaming a file
un-suppresses everything baselined in it. `check` says so when it happens —
"already in the baseline under a different path" — rather than presenting an old
violation as new; re-record with `agsync check --baseline` after a move.

**The baseline governs the gate, and never the reader.** It records what should
stop a push — a decision about your rollout, not a statement about what is true.
An agent about to act on this memory needs the whole picture, including the parts
you chose not to block, so the boot protocol runs:

```bash
agsync check --no-baseline
```

Suppressing a finding is how you keep working. Believing it was suppressed
because it was fixed is how a new session acts on something false.

### Replay: how bad is it already?

`agsync replay` runs the same check engine at every commit in a repository's
history and counts the pushes the gate would have stopped. Point it at a repo
that has been running on hand-maintained memory for a while, and it will tell you
when each violation started and how long it has been sitting there.

```console
$ agsync replay . --ref main --first-seen
sha      date        errors  rules
3ad9e51  2026-03-02       0  —
...
b7e2d13  2026-03-14       4  index-matches-files, links-resolve, unique-decision-ids

18 of 42 pushes would have been rejected

rule                  first failed         survived
protocol-files-exist  9f1c0a4 2026-03-04   31 commits, 9 days (still failing at HEAD)
unique-decision-ids   c40b8f2 2026-03-09   14 commits, 4 days (still failing at HEAD)
index-matches-files   c40b8f2 2026-03-09   14 commits, 4 days (still failing at HEAD)
```

The second table is the uncomfortable one. A broken boot step usually dates from
the commit that introduced the protocol and is usually still broken at HEAD,
because nothing has ever checked it.

Replay clones the target to a temp directory and moves HEAD only inside that
clone, so it cannot leave your repository detached at an old commit. It takes a
path or a clone URL, exits 0 unless the run itself fails — it reports on history,
it does not gate it — and ignores any baseline, because the question is what the
rules would have caught, not what someone had already told the gate to overlook.

One caveat worth stating out loud: replay judges old commits by today's rules.
The claim is "these would be rejected now", not "these were rejected then".

### Two agents, one task

Nothing stops two agents picking the same task and discovering it at push time,
after both have done the work. agsync uses git as the lock rather than adding a
service: an agent claims a task by setting `**Owner:**` and `**Claimed at:**`,
committing that alone, and pushing it *before* starting. If two claim at once
the second push is rejected — it pulls, sees an owner, and takes another task.

```
**Status:** in-progress
**Owner:** agent-a
**Claimed at:** 2026-08-31
```

The generated boot protocol spells this out, and three rules keep the record
honest: an in-progress task with no owner is an error, a claim left sitting or
carrying no readable date is a warning, and so is one owner holding several
tasks at once.

Memory commits also stay separate from code commits, so `git log -- memory/
tasks/` reads as a timeline. The installed `pre-commit` hook warns when a commit
mixes the two; it never blocks, because commit shape is a convention rather than
a fact about the memory.

## Rules

| Rule | Default | Catches |
|---|---|---|
| `unique-decision-ids` | error | Two ledger entries sharing an ID, making every reference ambiguous |
| `decision-refs-resolve` | error | A `D-xxx` cited anywhere that the ledger never defines |
| `relations-resolve` | error | `(supersedes D-019)` where `D-019` doesn't exist |
| `no-self-reference` | error | A decision superseding itself |
| `decision-has-date` | error | An entry with no date, so the ledger can't be ordered |
| `protocol-files-exist` | error | A boot instruction pointing at a file that isn't there |
| `links-resolve` | error | Any relative markdown link with no target |
| `status-in-enum` | error | A status outside `todo/in-progress/done/superseded` |
| `status-not-qualified` | warn | `done (stub)` — not machine-readable |
| `index-matches-files` | error | Index and task files disagreeing, or a task missing entirely |
| `task-refs-resolve` | error | A dependency on a task that doesn't exist |
| `no-dependency-cycles` | error | Tasks that can never be executed in any order |
| `blocked-task-not-done` | warn | A task marked done while its dependency is still open |
| `in-progress-needs-owner` | error | A task in progress that nobody has claimed |
| `stale-claim` | warn | A claim held for a long time, or with no usable date |
| `one-claim-per-owner` | warn | One owner holding several tasks in progress at once |
| `no-orphan-memory-files` | warn | A memory file nothing references, so no agent reads it |

## Configuration

`.agsync.toml`, or `agsync.toml`. Severity is configuration; rule logic is not.

```toml
exclude = ["archive/"]

[rules]
status-not-qualified = "warn"
decision-has-date = "off"
```


## CI

```yaml
- uses: gabrielft-me/agsync@v0.1.1
```

Or directly:

```yaml
- run: pip install agsync && agsync check --format github
```

Findings render inline on the pull request diff. Pair it with branch protection
and the memory can't rot through a merge.

## How it works

**Normalize, don't demand.** Real memory repos are written by several agents over
weeks and the format drifts: field sets vary between entries, values wrap across
lines, index tables use different column orders in the same file. The parser
absorbs all of it into a graph. Rules only ever see the graph.

**Rules are tiny and isolated.** Each is a function taking the parsed memory and
yielding findings. No shared state, no ordering, no knowledge of each other.

```python
from agsync.rules import rule
from agsync.model import ERROR, Finding

@rule("no-undated-tasks", ERROR, "A task file carries no date.")
def no_undated_tasks(memory):
    for task in memory.tasks.values():
        if "date" not in task.status_raw:
            yield Finding("no-undated-tasks", task.path, 1, "no date")
```

**One report contract.** Every finding is
`{rule, path, line, message, severity, subject}`, and JSON adds the `fingerprint`
the baseline matches on.
Text output, JSON, and GitHub annotations all derive from it, so a new output
format never touches a rule.

## Roadmap

- [x] Linter, config, baseline, JSON/GitHub output
- [x] `init` scaffold, local hooks with chaining
- [x] `replay`: measure the decay across a repository's whole history
- [ ] `--fix`: regenerate the task index; interactive ID renumbering
- [ ] YAML front matter schema — make the D-ID ↔ task graph structured instead of prose
- [ ] Harness adapters (Claude Code, Cursor) writing commit trailers at commit time
- [ ] `agsync draft`: turn captured trailers into a ledger entry, BYO key, opened as a PR
- [ ] Single static binary

### What this will never be

**An intent-inference engine.** A `post-receive` hook fires *after* the push —
the agent's turn is over and its context is gone. All the server sees is a diff
and a message, strictly less than the agent had. The reason a project pivoted
from one approach to another is not in the diff and no model recovers it from
there.

So: **capture at commit time, enforce at push time.** When the ledger entry
lands, the model formats a *why* that a human or agent already stated. It never
invents one. If nobody supplied a reason, the right outcome is a rejected push —
not a plausible-looking fabrication. A ledger that looks trustworthy and isn't is
worse than no ledger.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). New rules are welcome and are the easiest
place to start — one function, one test, one row in the table above.

## License

MIT.
