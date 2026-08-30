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
                                      9 reference(s) to D-021 are now ambiguous
tasks/README.md
     -  error   index-matches-files   task 26 exists (tasks/26-error-marks.md) but is absent
                                      from the index — invisible to anyone reading it
    14  error   index-matches-files   task 25: index says 'todo', file says 'done'

5 decisions, 3 tasks, 2 index rows
10 error(s), 2 warning(s)
```

---

## The problem

You gave your agents a memory. `AGENTS.md` with a boot protocol, a numbered
decision log, a task queue. Every session starts by reading it. It is the only
thing standing between a new agent and a cold start.

**Nothing checks that it is true.**

Here is what happened to one such repo after *two days* of multi-agent use — a
real audit, not a hypothetical:

| The repo declares | Reality |
|---|---|
| Decision IDs are unique | `D-021` defined **twice**, with two unrelated decisions. Nine references split between the two meanings. |
| Boot step 2: "read `memory/goal.md`" | `goal.md` **never existed**. Zero commits ever touched it. Four files link to it. Every session silently improvised past step one. |
| `status ∈ {todo, in-progress, done, superseded}` | `done (stub)`, `done (live-key acceptance pending)`, `superseded → 11..15`. "Done" stopped meaning anything. |
| `tasks/README.md` is the index | Task 25: `todo` in the index, `done` in its file. Task 26: complete, and **missing from the index entirely.** |

All four invariants the repo declared about itself were violated. The design
wasn't wrong — it's the standard `AGENTS.md` + decision-log pattern. The problem
is that **nothing enforced it**.

> A memory system whose integrity depends on being maintained by hand degrades
> at exactly the speed you use it.

Your agents are reading this file at the start of every session and treating it
as ground truth.

## The fix is not a smarter agent

Agents self-report badly. They tell you what they *intended* to do, not what
they did, and every harness writes in a different dialect. "Be more careful" does
not survive a new agent joining.

`git push` is a choke point nobody can skip. Put the validation there.

## Install

```bash
uvx agsync check          # try it, no install
pipx install agsync       # keep it
pip install agsync
```

Python 3.11+. **Zero dependencies** — the whole thing is stdlib, so it runs in a
git hook without a virtualenv surprise.

## Use

```bash
agsync check                    # lint the repo
agsync check --warn-only        # first run: see everything, fail nothing
agsync check --baseline         # accept today's mess, fail on new mess
agsync check --format github    # inline annotations on the PR diff
agsync check --format json      # for your own tooling
agsync replay <repo>            # how many past pushes the gate would have stopped
agsync rules                    # what it checks and why
agsync init                     # scaffold a memory structure from scratch
agsync install-hooks            # gate commits locally
```

### Adopting an existing repo

Do not turn everything on at once — a linter that rejects 20 of your 29 pushes
on day one gets uninstalled on day one.

```bash
agsync check --warn-only    # 1. look at the damage
agsync check --baseline     # 2. freeze it; new violations now fail
```

The baseline records a fingerprint per violation that survives line shifts, so
unrelated edits above a known problem don't resurrect it as "new". Delete
entries from `.agsync-baseline.json` as you fix them.

### Replay: how bad is it already?

`agsync replay` runs the same check engine at every commit in a repository's
history and counts the pushes the gate would have stopped. It is the argument
for the tool, measured instead of asserted.

```console
$ agsync replay ~/src/Shared_Brain --ref main --first-seen
sha      date        errors  rules
c29e63a  2026-08-07       0  —
...
108c83f  2026-08-08       4  index-matches-files, links-resolve, unique-decision-ids

24 of 29 pushes would have been rejected

rule                   first failed         survived
links-resolve          eb2c931 2026-08-07   24 commits, 15 hours (still failing at HEAD)
index-matches-files    66aee59 2026-08-07   21 commits, 15 hours (still failing at HEAD)
unique-decision-ids    29af037 2026-08-07   18 commits, 11 hours (still failing at HEAD)
decision-refs-resolve  eb2c931 2026-08-07   3 commits, 9 minutes

longest-lived violation: links-resolve in tasks/README.md — 24 commits —
link target '../memory/goal.md' does not exist
```

That is the repository from the table above, and the numbers are the same story
told precisely: the boot protocol's missing `goal.md` broke on the sixth commit
and was still broken at HEAD, through eighteen further pushes, two days later.

Replay clones the target to a temp directory and moves HEAD only inside that
clone, so it cannot leave your repository detached at an old commit. It accepts a
path or a clone URL, exits 0 unless the run itself fails — it reports on history,
it does not gate it — and ignores any baseline, because the question is what the
rules would have caught, not what someone had already told the gate to overlook.

One caveat worth stating out loud: replay judges old commits by today's rules.
The claim is "these would be rejected now", not "these were rejected then".

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
| `no-orphan-memory-files` | warn | A memory file nothing references, so no agent reads it |

## Configuration

`.agsync.toml`. Severity is configuration; rule logic is not.

```toml
exclude = ["archive/"]

[rules]
status-not-qualified = "warn"
decision-has-date = "off"
```

Also readable from `[tool.agsync]` in `pyproject.toml`.

## CI

```yaml
- uses: gabrielft-me/agsync@v1
```

Or directly:

```yaml
- run: pipx install agsync && agsync check --format github
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

**One report contract.** Every finding is `{rule, path, line, message, severity}`.
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
