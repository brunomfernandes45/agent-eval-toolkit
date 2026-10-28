# Frozen Verification and Enterprise-Onboarding Protocol

## 1. Purpose and reclassified validation mandate

This protocol evaluates the thesis artefact as measurement and evidence-governance
software. It separates three objectives that must not be collapsed into a single
claim of “framework validation”:

1. **Toolkit conformance verification:** requirements-based public-interface
   conformance testing augmented by structural coverage, mutation, and metamorphic
   testing, with synthetic fixtures whose ground truth is known before execution.
2. **Enterprise-data evaluability assessment:** verification that the artefact can
   profile a source, expose source-to-canonical mapping gaps, measure recorded
   onboarding friction, classify evidence readiness, and produce actionable
   diagnostics without retaining source values.
3. **Deferred naturalistic validation:** a future enterprise pilot with approved
   operational data and real organisational participants.

The present dissertation addresses the first objective and verifies the calculation
mechanics of the second. It does **not** establish representative enterprise mapping
burden, organisational adoption utility, realised productivity, causal impact,
operational scalability, or external validity. In FEDS terms, this is an artificial
ex-post evaluation chosen for control and exact ground truth. The natural next
evaluation episode is a controlled enterprise pilot that measures both framework
utility and actual onboarding friction.

## 2. Freeze rule

The confirmatory run is valid only when the following are content-addressed before
results are interpreted:

- source archive and source-tree digest;
- built wheel and source distribution;
- dependency lock;
- workflow configurations, manifests, source profiles, mapping specifications,
  activity logs, canonical fixtures, and Langfuse fixtures;
- this protocol and the bidirectional requirements matrix;
- the implementation-independent oracle workbook and formula export;
- schemas, mutation declarations, critical-branch declarations, and commands.

Development runs preceding the freeze are not confirmatory evidence. Any change to a
frozen input, rule, oracle, or executable invalidates the earlier bundle digest and
requires a new campaign. Repeated executions in this protocol are performed by the
same author and support deterministic repeatability only.

## 3. Evaluation objects and conceptual boundary

The evaluation boundary begins before canonical telemetry. The objects are:

1. raw-value-free source profiling;
2. source-to-canonical mapping specification and semantic approvals;
3. mapping-friction measurement and issue registration;
4. canonical-row validation;
5. workflow-run reconstruction and run-local quarantine;
6. telemetry capability, coverage, and missingness handling;
7. per-metric availability and evidence-tier classification;
8. aggregate and comparator calculations;
9. assumption-based economic eligibility;
10. evidence minimization and redaction;
11. Langfuse offline preparation through the common onboarding model;
12. deterministic packaging and package verification; and
13. design-manifest validation and evidence-claim capping.

The onboarding layer spans the framework’s data-mapping and governance work without
renumbering the four framework modules. It makes source heterogeneity, transformations,
semantic decisions, approvals, iterations, issues, manual review, elapsed cycle time,
and recorded active effort visible rather than treating the adapter as a frictionless
boundary.

The normative rules are identified in `requirements_matrix.csv`. Every rule has a
positive case and a negative or boundary case, an exact result or diagnostic, and a
retained evidence artefact. The matrix is bidirectional: cases cite requirements and
requirements cite evidence.

## 4. Evidence tiers and hybrid safety floor

Readiness and claim strength are independent. A complete telemetry tier does not turn
monitoring into causal evidence, and synthetic or benchmark evidence remains capped at
conformance/readiness regardless of its tier.

The attained evidence tiers are:

- **blocked:** privacy, integrity, identity, aggregation, pagination, configuration,
  or population-assignment safety is not trustworthy;
- **mapping_only:** safe profiling is possible, but mapping or workflow semantics are
  not sufficiently established for analytics;
- **partial:** at least one requested metric family is valid for an identified
  denominator, while another requested output is unavailable; and
- **complete:** every requested required signal and design-specific analytical
  precondition is satisfied, required mapping is verified, and no source run is
  quarantined.

Tiering does not weaken the hard controls. Unsafe restricted-field retention, canary
leakage, duplicate identities, unresolved placeholders, unsupported aggregation,
incomplete pagination, package-hash failure, or rows that cannot be assigned safely to
a run remain bundle-wide blockers. Once identity is trustworthy, a local boundary,
configuration, or missing-signal anomaly quarantines the affected run. Quarantined
runs remain visible in diagnostics and source-population denominators but cannot
contribute to analytical values.

Every metric family is independently `available`, `partial`, `withheld`, or
`unsupported`. Partial values expose covered, eligible, and population denominators.
Missing values are never converted to zero. Comparator, economic, quality, cost,
duration, and reliability gates operate independently.

## 5. Mapping-friction oracle

Mapping friction is a multidimensional profile, not a universal weighted score. The
synthetic mapping oracle verifies:

- required signals mapped, observed, transformation-tested, and semantically approved;
- counts by transformation class;
- mapping versions and iterations;
- semantic decisions, approvals, stakeholder dependencies, and issue states;
- records requiring manual review;
- source format and structural heterogeneity;
- elapsed onboarding cycle time; and
- active mapping effort with observed and expected activity denominators.

Active effort is a complete total only when every retained activity entry records a
value. A partial activity log yields an observed partial sum and `n/N`; it is never
extrapolated. No activity log yields `null/not_recorded`. These synthetic values verify
mechanics only and are not estimates of enterprise burden.

## 6. Implementation-independent oracle

The oracle workbook imports no toolkit module and uses visible spreadsheet formulas.
It reconstructs run durations, completion and failure rates, R-7 percentiles,
coverage, token and cost totals, comparator arithmetic, economic eligibility,
mapping-friction dimensions, effort missingness, quarantine denominators, metric
statuses, and evidence tiers. Raw fixture values are isolated on input sheets;
calculated cells reference those sheets rather than hard-code obtained outputs.

Exact integer and count results must match. Rounded floating-point results may differ
by at most 0.001. Monetary calculations use decimal arithmetic and round only at the
declared presentation boundary.

## 7. Confirmatory execution

Run from the repository root with a clean Python environment:

```text
python -m build
python experiments/run_verification_campaign.py --wheel <built-wheel> --output-dir <bundle>
```

The campaign performs:

1. input and output schema validation, including onboarding and tier artefacts;
2. Black, Ruff, and mypy checks;
3. unit and integration tests with line and branch measurement;
4. deterministic metamorphic tests;
5. targeted mutation tests for thesis-critical rules;
6. fresh-wheel CLI, mapping-assessment, Langfuse, and readiness execution;
7. package verification for every complete or partial package;
8. deterministic repeatability across output directories and workspace roots;
9. two fixed-epoch distribution builds and hash comparison;
10. restricted-canary and absolute-path scanning across derived bundle files and
    retained command output, excluding frozen source inputs; and
11. requirements coverage, bundle indexing, and SHA-256 checksums.

Commands, exit codes, standard output, and standard error are retained only after
sanitisation and privacy scanning. Source locations in analytical artefacts are
logical identifiers rather than absolute filesystem paths.

## 8. Metamorphic relations

The declared deterministic relations are:

- unambiguous canonical-row permutation preserves analytical metrics;
- timezone equivalence and a uniform timestamp shift preserve durations;
- splitting per-event token or cost increments preserves supported totals;
- exclusions match direct recomputation on retained runs;
- a failed run changes reliability and cost but cannot create benefit;
- a missing capability propagates to `null`, never zero;
- reversing comparator groups reverses delta signs;
- supported Langfuse casing and container formats normalize equivalently;
- irrelevant source metadata does not change analytical results;
- mapping-row and activity-row permutation preserve the friction profile; and
- splitting a recorded mapping activity preserves observed active-effort totals.

## 9. Targeted mutation policy

The campaign introduces one predeclared non-equivalent change at a time in the
thesis-critical rules for economics, missingness, boundaries, terminal semantics,
deduplication, configuration matching, redaction, evidence-tier classification,
mapping-only gating, privacy blocker scope, run-local quarantine, comparator
withholding, signal approval, effort missingness, pagination, Langfuse body evidence,
package verification, canary scanning, synthetic claim capping, and aggregation
semantics. A mutant is killed only when its declared public-interface test fails. Equivalent
mutants are documented and excluded; no non-equivalent critical mutant may survive.

## 10. Tier-specific publication contract

- `blocked` publishes privacy-scanned `diagnostics.json` and
  `evaluation_readiness.json` only.
- `mapping_only` publishes onboarding and readiness artefacts but no analytical
  scorecard.
- `partial` and `complete` publish readiness, diagnostics, quarantine register,
  per-metric status and denominators, a prominently tier-labelled scorecard and report,
  and a verifiable package.

All sets publish transactionally. A rerun that degrades or blocks cannot leave stale
complete artefacts. The command succeeds only when the attained tier meets the
manifest’s or command’s minimum tier. Legacy manifests without a publication policy
retain the complete minimum.

## 11. Acceptance criteria

The frozen bundle is accepted only if:

- 100% of declared requirements link bidirectionally to passing evidence;
- 100% of declared thesis-critical decision branches execute;
- frozen-core structural coverage, as delimited in `coverage_scope.json`, is at least
  90% statement and 85% branch; the optional OpenTelemetry adapter and command-boundary
  setup excluded from that denominator remain exercised by module and installed-interface
  tests;
- zero non-equivalent critical mutants survive;
- Black, Ruff, mypy, schemas, unit, integration, build, installation, and CLI gates pass;
- every complete or partial package verifies and every tamper probe fails verification;
- analytical artefacts are byte-identical across different output and workspace roots;
- two fixed-epoch builds have identical distribution hashes;
- zero configured restricted canaries or forbidden absolute paths occur in the derived
  bundle; and
- all tier-specific artefact sets match their declared publication contract.

If a criterion fails, the evidence bundle records the failure and the dissertation
does not report the affected result as achieved.

## 12. Interpretation limits

Passing supports implementation conformance under the tested synthetic contract and
the capability to assess enterprise-data and mapping readiness. It does not establish
representative enterprise mapping effort or organisational adoption utility. Mapping
activity effort may be self-reported and shaped by arbitrary organisational workflows.
Synthetic source shapes cannot represent all enterprise structures. Evidence tiers
are ordinal publication states, not statistical confidence levels.

Hashes support identity and integrity; they do not prove authenticity, authorship,
anonymity, or semantic correctness. Privacy tests cover declared fields and adversarial
canaries, not every unknown sensitive field. Langfuse fixtures cover supported
documented structures, not every production export. The required next step is a
naturalistic enterprise pilot evaluating both framework utility and actual onboarding
friction.
