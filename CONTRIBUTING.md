# Contributing

This repository is operated as a controlled research codebase. Changes should favor auditability, reproducibility, and operational safety over feature breadth.

## Setup

```bash
uv venv .venv
source .venv/bin/activate
uv pip install ".[dev]"
```

## Required local checks

Run all of these before opening a pull request:

```bash
black --check .
ruff check .
mypy
python -m unittest discover -s tests
```

## Change requirements

- Every behaviour change must include or update tests.
- Public CLI or package changes must update `README.md`.
- New telemetry fields or report outputs must preserve traceability to source evidence.
- Missing telemetry must be surfaced explicitly. Do not backfill or invent operational data.
- Assumption-based estimates must remain separate from observed metrics.
- New dependencies require explicit justification in the pull request.

## Review policy

- At least one maintainer review is required for routine changes.
- Two maintainer reviews are required for security-sensitive or data-handling changes.
- Changes to normalization rules, metrics formulas, or report semantics must include a short rationale in the pull request.

## Pull request checklist

- Scope is limited and clearly described.
- Validation logic has been updated where relevant.
- Tests cover the changed behaviour.
- `black`, `ruff`, `mypy`, and unit tests pass.
- Documentation is updated.
- No secrets, real customer data, or unsanitized telemetry were added to the repository.
