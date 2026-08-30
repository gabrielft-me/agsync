# Contributing

```bash
git clone https://github.com/gabrielft-me/agsync
cd agsync
pip install -e ".[dev]"
pytest -q
```

No dependencies in the runtime package. Ever. This runs inside git hooks on
other people's machines, and a virtualenv surprise there is a broken commit for
someone who never asked for our opinions about packaging.

## Before you start

[docs/design.md](docs/design.md) explains why the architecture is the way it is
— why the parser normalizes instead of demanding, why severity is configuration,
why fingerprints hash structured fields, why replay clones instead of checking
out, and why intent is never inferred from a diff. Worth reading if a change
starts to feel like it is fighting the codebase.

## Adding a rule

A rule is one function. Start here — it's the easiest contribution and the most
valuable one.

1. Write it in `src/agsync/rules/` (decisions, tasks, or links):

```python
@rule("no-undated-decisions", ERROR, "One line: what silently breaks.")
def no_undated_decisions(memory):
    for decision in memory.decisions.values():
        if "date" not in decision.fields:
            yield Finding("no-undated-decisions", decision.path,
                          decision.line, f"{decision.id} has no date")
```

2. Add the failing case to `tests/fixtures/decayed/` and a test asserting the
   rule fires with a useful message.
3. Add a row to the rules table in `README.md`.

### What makes a good rule

- **It catches something invisible to a careful reader.** A duplicate `D-021`
  fifty lines apart is the archetype.
- **It has zero false positives on a healthy repo.** Run it against
  `agsync init` output; that must stay clean.
- **The message says what broke and why it matters**, not just which check
  failed. Compare: `"duplicate ID"` versus `"D-021 is redefined here (first
  defined at line 7); 9 reference(s) are now ambiguous"`.
- **Default to `warn` if it encodes a style opinion**, `error` only if the
  memory is now factually wrong.

## Parser changes

The parser normalizes; it never demands. If a real repo uses a format we don't
read, that's a parser bug, not a user error. Add the shape to
`tests/fixtures/` and make the parser absorb it.

Never make a rule reach into raw markdown. If a rule needs something the model
doesn't carry, add the field to the model. The reasoning is in
[docs/design.md](docs/design.md).

## Style

`ruff check src tests` must pass. Comments explain *why*, not *what* — the code
already says what.
