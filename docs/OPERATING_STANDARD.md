# Operating Standard

This document defines the repository’s baseline organisational controls.

## 1. Roles and responsibilities

- Maintainers approve merges, releases, and policy exceptions.
- Contributors implement scoped changes and provide evidence that required checks passed.
- Reviewers verify correctness, test coverage, data-handling compliance, and documentation updates.

## 2. Quality gate policy

All code changes must pass the following gates before merge:

```bash
black --check .
ruff check .
mypy
python -m unittest discover -s tests
```

Additional expectations:

- all public functions must be typed
- privacy, integrity, identity, aggregation, pagination, and source-assignment failures must block publication
- safe incomplete evidence must degrade to an explicit tier with actionable diagnostics and denominators
- report outputs must be deterministic for the same input set
- tests must cover both success and failure paths for new behaviour

## 3. Security review policy

Security review is mandatory for:

- dependency additions or upgrades
- changes to file ingestion or serialization
- changes that affect telemetry exposure, redaction, or persistence
- release automation changes
- any feature that touches secrets, credentials, or external services

Security review evidence should be captured in the pull request description.

## 4. Data-handling policy

Data classification baseline:

- `Restricted`: raw or near-raw production telemetry, prompt bodies, ticket text, and any records containing sensitive operational or user data
- `Internal`: derived metrics, scorecards, and workflow reports without restricted payload content
- `Public`: repository metadata and documentation intentionally approved for public distribution

Required controls:

- do not store restricted data in this repository unless it is anonymized and explicitly approved
- keep evidence references, but avoid embedding unnecessary sensitive payloads in derived artefacts
- sample data must be synthetic or anonymized
- missing telemetry must be reported, not fabricated
- retention and deletion of real operational data must follow the deployment organisation’s policy

## 5. Release and signing policy

Release policy baseline:

- follow semantic versioning
- every release must originate from the default branch after CI passes
- every release must have a changelog entry summarising behaviour changes, risks, and migration impact
- release artefacts must be built from a clean checkout
- signed tags or provenance-attested release artefacts are required when the hosting platform supports them

Minimum release checklist:

1. Confirm CI passed on the release commit.
2. Confirm `black`, `ruff`, `mypy`, and unit tests pass locally or in release CI.
3. Review changelog and release notes.
4. Build the package artefact from a clean environment.
5. Create a signed tag or equivalent provenance record.
6. Archive release evidence in the organisation’s release record.

## 6. Exception handling

Any deviation from this standard must be documented in the pull request or release record, including:

- what was waived
- why the waiver was necessary
- who approved it
- when it expires or will be revisited
