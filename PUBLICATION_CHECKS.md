# Dissertation publication checks - 28 October 2026

Toolkit version: 0.4.0. The post-defence work corrects the evidence-tier diagram
and its explanatory text, documents public distribution and reproduction, adds
software citation metadata, and enforces locked dependency installation in CI.
Toolkit analytical behaviour was not changed by these corrections.

Checks actually executed against the clean publication copy:

- Locked development environment installed successfully with Python 3.13.7.
- All 177 tests passed both against the built wheel and in the locked checkout.
- The documented sample pipeline and package-integrity verification passed.
- Standalone fresh-wheel readiness: 28/28 scenarios, 191/191 metric checks,
  and 45/45 governance checks passed.
- The complete verification campaign passed all 13 gates: 58 requirements,
  97 bidirectional case mappings, 557 schema checks, 11 metamorphic relations,
  and 21/21 targeted critical mutants killed.
- Frozen-core line coverage: 93.200%; branch coverage: 86.061%; critical anchors: 100%.
- Package verification: all 168 baseline/tamper checks passed.
- Fixed-epoch source and wheel builds matched across two build roots.
- Cross-root comparison: all 15 compared outputs matched.
- Derived-bundle privacy/path scan: zero restricted-canary or forbidden-path matches.
- Black, Ruff, mypy, and all 10 documentation-language audit checks passed.

The full campaign records, historical bundle, and prebuilt formula workbook are
retained locally by the author, outside the public source-and-documentation release.
The results above describe the executed implementation checks before the final
publication-scope documentation edit. `REPRODUCIBILITY.md` defines the self-contained
public reproduction path: sample pipeline, integrity verification, unit and metamorphic
tests, structural coverage, critical branches, targeted mutation, and fresh-wheel
readiness. These public checks do not depend on the retained workbook or archives.

These executions are same-author conformance and deterministic repeatability
evidence over synthetic fixtures. They do not establish independent reproduction,
compatibility with all telemetry exporters, enterprise effectiveness, causal impact,
or realised return on investment. GitHub-hosted CI results must be checked separately
after upload; the local checks do not assert that a hosted job has already passed.
