# Contributing

Thanks for helping. Issues and PRs are welcome, especially new stateful/compute
resource types and real-world plans that planguard gets wrong.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest && ruff check . && ruff format --check .
```

## Adding or changing a rule

1. Write the rule in `src/planguard/rules.py` with the `@rule(...)` decorator.
   Pick the next free `PGxxx` id and never reuse an id.
2. Add a fixture plan to `tests/fixtures/` if no existing one covers your case.
   Real `terraform show -json` output is best; scrub account IDs and names first.
3. Add tests to `tests/test_rules.py`, then update the rule table in `README.md`
   and add an entry to `CHANGELOG.md`.

## Ground rules

- Core logic uses only the standard library; PyYAML is the only dependency.
- Rules should report changes **introduced by the plan**, not things that already exist.
- Keep messages short and concrete: what happens, to which resource, and why.
