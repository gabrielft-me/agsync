# Design

Why agsync is built the way it is. If you are adding a rule, read
[CONTRIBUTING.md](../CONTRIBUTING.md) first — this document is the reasoning
underneath it, for when a change feels like it is fighting the architecture.

One idea holds the rest together:

> A memory system whose integrity depends on being maintained by hand degrades
> at exactly the speed it is used.

Agents do not decay memory through carelessness. They decay it because each
session sees a slice, every edit is locally reasonable, and nothing anywhere
checks the whole. So the tool's job is not to be clever. It is to be the thing
that always runs, at the two points nobody can skip: the boot, where memory is
believed, and the push, where it is written.

---

## 1. The parser normalizes; it never demands

A linter that requires a rigid format fails on the first real repository it
meets, because the format is irregular *by nature*. Agent memory is written by
several different agents, in different sessions, over weeks. Nobody rereads the
file for consistency of shape — they read it for content and append.

What that looks like in practice: field sets differ between entries in the same
ledger; a value wraps across two physical lines; the index has several tables
with different column orders; relationships are written in prose parentheses
(`(supersedes D-001, D-011)`); a decision ID is a bare token that is never a
link.

So the parser absorbs all of it into a graph, and **rules only ever see the
graph**. Concretely:

- **Fields land in a dict and are never required.** No rule assumes a field is
  present, because in real data it often is not.
- **Continuation lines are folded** into the field above them before anything
  reads the value.
- **Tables are read by locating their header columns**, per table, not by
  position.
- **Status splits into a base and a qualifier.** `done (stub)` parses as base
  `done` plus qualifier `(stub)`. The base is checked against the enum as an
  error; the qualifier is a *warning*. This is what lets an existing repo become
  parseable today rather than after someone rewrites thirty files, and the
  warning becomes the migration path.

The rule of thumb: **if a real repository uses a shape we cannot read, that is a
parser bug, not a user error.** Add the shape to `tests/fixtures/` and make the
parser absorb it. Never make a rule reach into raw markdown; if a rule needs
something the model does not carry, add the field to the model.

### What counts as memory

Text-scanning rules run over a *memory surface* — the protocol file and
anything under `memory/`, `tasks/` or `.agsync/` — not every markdown file in
the repository. A decision ID quoted in a README is prose. The same token inside
the memory surface is a reference that must resolve. Scoping is the fix rather
than an exclude list, because otherwise every user ends up writing the same
excludes.

---

## 2. Severity is configuration; rule logic is not

Rules declare a default severity. `.agsync.toml` overrides it per rule,
including `off`. The exit code is 1 only when at least one `error` survives.

This is borrowed from ESLint, and it is not a nicety. A linter that lands
mid-project and rejects most of an existing repository on day one gets
uninstalled on day one. Gradual adoption is the entire game, so `--warn-only`
and `--baseline` are first-class rather than afterthoughts, and **every rule
must behave correctly at any severity**.

The corollary for rule authors: default to `warn` if a rule encodes a style
opinion, and `error` only if the memory is now factually wrong. "This is
untidy" and "this is a lie a future agent will act on" are different claims.

---

## 3. Fingerprints hash structure, not prose

The baseline records a fingerprint per violation so that existing problems can
be accepted while new ones still fail. That fingerprint hashes **rule, path and
subject** — the subject being what the finding is *about*: a decision ID, a task
number plus what went wrong with it, a link target.

It deliberately does not hash the line number, and — this is the part that is
easy to get wrong — it does not hash the rendered message either.

Messages are prose written for humans, and useful messages quote things:
a line number, a reference count, a truncated free-text value. Hashing the
message therefore smuggles all of that into the identity of the violation. The
concrete failure: a rule that names *another* entry's line number in its message
would change fingerprint whenever anyone inserted a paragraph above that other
entry, and a violation the user had already baselined would resurrect itself as
new. That is exactly what excluding the line number was supposed to prevent, and
it teaches people to delete the baseline rather than shrink it.

A subject also means a message can be reworded — clearer wording, a typo fix —
without invalidating every baseline in existence.

Accepted consequence: two findings that share a subject in one file share a
fingerprint and are suppressed together. That is deliberate and now precise,
rather than accidental.

---

## 4. Replay clones; it never checks out in place

`agsync replay` walks a repository's history and runs the check engine at every
commit, to answer "how many of these pushes would the gate have stopped, and how
long has each violation been there".

The obvious implementation checks out each commit in the target repository and
restores `HEAD` at the end. Do not do this. A crash, a `Ctrl-C`, or an exception
in the middle leaves someone's working tree stranded on a three-week-old commit,
and it cannot run at all against a repository with local modifications.

Instead replay clones the target to a temp directory and moves `HEAD` only
inside that clone. `git clone --local` hardlinks the object store, so the safe
version is also nearly free. Nothing needs restoring because nothing was
disturbed, the target needs no write access, and a URL works as well as a path.

Three smaller decisions fall out of the same "say what you mean" principle:

- **Baselines are ignored.** Replay answers what the *rules* would have caught,
  not what a gate that had already been told to look away would have caught.
- **Exit code 0 even when every commit would be rejected.** Replay reports on
  history; it does not gate it. A non-zero exit is reserved for the run itself
  failing, because a script has no other way to tell those apart.
- **It judges old commits by today's rules.** The claim is "these would be
  rejected now", never "these were rejected then". Say so in any writeup.

---

## 5. Intent is captured, never inferred

The tempting design is to let a model read the diff after a push and write the
decision entry — explain *why* the change happened.

This does not work, and the reason is structural rather than a question of model
quality. A post-push hook fires after the agent's turn has ended and its context
is gone. What the server sees is a diff and a commit message: **strictly less
information than the agent had.** The reason a project abandoned one approach for
another is a judgement about a constraint the author hit. It is not in the diff,
and no model recovers it from there. There is also an operational problem: if the
hook asks a question, nobody is awake to answer it when an autonomous agent
pushes at 3 a.m.

So the split is:

| Concern | Mechanism | When |
|---|---|---|
| Facts the harness already has | commit trailers, zero judgement | commit time |
| Structural invariants | deterministic parser + rules | push time, blocking |
| Prose composition | a model | after the push, async |
| Approval | a human | pull request |

**Capture at commit time, enforce at push time.** When a model is involved at
all, it formats a *why* that a human or an agent already stated. It never
invents one. If nobody supplied a reason, the correct outcome is a rejected
push, not a plausible-looking fabrication — a ledger that looks trustworthy and
is not is worse than no ledger.

The general principle, which decides most arguments in this codebase:
**model where there is judgement, parse where there is a rule.** Every bug this
tool was built to catch is caught by a few hundred lines of deterministic Python.
Do not spend a model on what a parser solves.

---

## 6. Two consumers, one command

`agsync check` answers two different questions with the same findings.

**The gate** asks *should this be blocked* — at `pre-commit`, at `pre-receive`,
in CI. **The reader** asks *what here can I not trust* — an agent at boot, about
to read a ledger and act on it. That second consumer is the one the tool exists
for, and it is the one that is easy to forget, because the gate is where the
code runs.

Two consequences follow, and both are in the scaffolded boot protocol:

- **Validation happens before the read, not only after the write.** The
  protocol runs `check` at step 2, before the agent opens `memory/goal.md`. A
  ledger with two entries sharing an ID makes an agent pick one and work from a
  false premise, and a commit hook fires far too late to prevent that.
- **The baseline governs the gate and never the reader**, so the boot check
  passes `--no-baseline`. A baseline records what should stop a push — a
  decision about your rollout. It is not a claim that anything was fixed, and an
  agent about to act on the memory needs the parts you chose not to block.

A failing boot check tells the agent to **repair what is unambiguous and report
what needs judgement**. An index row that disagrees with its task file is
mechanical. Which of two entries sharing an ID the references meant is not.
Instructing an agent to simply stop is worse than it sounds: a stalled
autonomous agent is a more expensive failure than a slightly wrong one.

### Why this is not a second command

The obvious move is a separate reader-facing command — something that answers
"is this memory safe to act on" instead of listing violations. It is deliberately
not built. The engine is where the value is, and duplicating its surface invites
divergence: a rule enabled for the gate but not for the reader is a lie by
omission, and a reader that silently checks less is worse than one that checks
bluntly.

But the one-command answer has a real defect, and it is worth knowing before you
add a rule. **Severity is configuration, and it is tuned for the gate.** A repo
that promotes a cosmetic rule to `error` stalls every agent boot. A repo that
sets `unique-decision-ids` to `off` makes the boot check certify a ledger it
never inspected. The reader's verdict should not depend on the gate's tuning,
and today it does.

The fix is a property on the rule rather than a new command: an intrinsic,
non-configurable statement of *what a finding makes untrustworthy* — the ledger,
the task graph, the boot path. With that, a per-artifact verdict is derivable
from the same findings and becomes a format on `check`. Without it, a second
command would only move the problem somewhere harder to see.

**A note before you file this as a bug.** The boot check inherits the gate's
configured severity, and that is a known limitation rather than an oversight. If
`.agsync.toml` promotes a style rule to `error`, every agent boot fails on
something cosmetic; if it sets a structural rule to `off`, the boot check
reports a clean ledger it never inspected. Please do not route around it — not
with a second command, not with a hardcoded severity list in the boot path, and
not by teaching the protocol to skip named rules. Both symptoms are one missing
piece: a rule cannot yet say what it makes untrustworthy, so the reader has
nothing to consult except the gate's tuning. Add that property and both
disappear at once.

So when you write a rule, choose a `subject` that identifies **the artifact**,
not merely the violation. It is what the baseline hashes today, and it is the
seed of that classification tomorrow.

## 7. One report contract

Every finding is `{rule, path, line, message, severity, subject}`. Text output,
JSON, and GitHub annotations all derive from those fields alone, so adding an
output format never touches a rule, and adding a rule never touches an output
format.

Rules are small, isolated generator functions over the parsed memory. They share
no state, run in no particular order, and know nothing about each other. That is
why the rule set can grow without the core growing — and why a new rule is one
function, one test, and one row in the README table.

---

## What is deliberately out of scope

- **A git server.** Smart HTTP, pack negotiation, auth and storage cost months
  and reimplement a commodity. agsync uses the extension points that already
  exist: local hooks, `pre-receive` on self-hosted remotes, and a GitHub Action
  for everyone else.
- **The memory system itself.** You own your protocol file, your decisions, your
  workflow. agsync only guarantees the thing does not rot.
- **Runtime dependencies.** The package imports only the standard library, which
  is why the floor is Python 3.11 (`tomllib`). This runs inside git hooks on
  machines we do not control, and a dependency resolution failure there is a
  broken commit for someone who never opted into our packaging opinions.
