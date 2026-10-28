# Thesis Verification Evidence

This directory contains the predeclared verification contract used to evaluate the
toolkit implementation.

- `FROZEN_VERIFICATION_PROTOCOL.md` defines the claim boundary, confirmatory sequence,
  metamorphic relations, mutation policy, and acceptance thresholds.
- `requirements_matrix.csv` links each normative rule to its validity threat,
  implementation control, positive and negative cases, expected result, and evidence.
- `defect_log.csv` records thesis-critical defects found during developmental review
  and the control introduced before the confirmatory run.
- `build_oracle_workbook.mjs` records the historical authoring procedure for the
  formula-driven oracle, using the optional `@oai/artifact-tool` library. The prebuilt
  workbook and its inspection export are retained by the author, outside this public
  release. The public tests and readiness suite use checked-in synthetic fixture
  oracles and do not require this authoring library or workbook.
- `critical_mutants.json` declares the 21 non-equivalent thesis-critical mutations.
- `critical_branches.json` maps those mutations to the complete outgoing arcs of 21
  critical decisions. Validate a branch-enabled coverage JSON report with:
  `python verification/validate_critical_branches.py --coverage-json coverage.json`.

The generated evidence bundle is not considered frozen until every check in the
protocol has passed and `bundle_checksums.sha256` has been written. Repeated campaign
executions are same-author deterministic repeatability checks.
