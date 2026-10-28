# Enterprise Evidence Onboarding

## 1. Establish the source and decision boundary

Before canonicalization, identify the candidate workflow-run key, workflow boundaries, business completion and failure semantics, requested metric families, restricted fields, storage constraints, and approvers. A trace or session identifier is only a candidate run key until reviewed.

## 2. Build value-free onboarding inputs

Export OpenTelemetry LogRecords as an offline OTLP/JSON `ExportLogsServiceRequest`. Create:

- `source_profile.json`, containing logical source identity, formats, record counts, and observed field paths/types without raw values;
- `mapping_spec.json`, containing canonical mappings, transformation classes, semantic status, approvals, issues, version, and evidence references;
- optional `mapping_activity.jsonl`, containing role, activity, timestamp, active-effort minutes, mapping version, issue reference, and outcome.

The standard OpenTelemetry fields provide timestamp, event name, trace and span correlation,
resource, scope, and attributes. The mapping must still establish workflow-run identity and
business-status semantics. Use `prepare-opentelemetry` to generate the value-free profile,
canonical events, workflow configuration, mapping assessment, redaction report, and fingerprints.

## 3. Assess mapping readiness and friction

```bash
agent-eval-toolkit assess-mapping \
  --source-profile source_profile.json \
  --mapping-spec mapping_spec.json \
  --evaluation-manifest evaluation_manifest.json \
  --activity-log mapping_activity.jsonl \
  --output-dir mapping-assessment
```

The result reports signal progress, transformations, iterations, decisions, approvals, issues, review volume, source heterogeneity, elapsed cycle time, and directly recorded effort. Omit `--activity-log` when no complete record exists; effort then remains `null/not_recorded`. A partial activity log produces only a denominated observed sum.

## 4. Validate the analytical inputs

```bash
agent-eval-toolkit validate \
  --workflow-config workflow_config.json \
  --raw-logs canonical_events.jsonl \
  --evaluation-manifest evaluation_manifest.json \
  --mapping-assessment mapping-assessment/mapping_assessment.json \
  --minimum-tier partial
```

Resolve bundle blockers. Safe incompleteness can remain as mapping-only or partial evidence when the manifest permits that tier.

## 5. Run and review

```bash
agent-eval-toolkit run \
  --workflow-config workflow_config.json \
  --raw-logs canonical_events.jsonl \
  --evaluation-manifest evaluation_manifest.json \
  --mapping-assessment mapping-assessment/mapping_assessment.json \
  --output-dir outputs
```

Review `evaluation_readiness.json`, `diagnostics.json`, `quarantined_runs.json`, per-metric denominators, redaction evidence, claim strength, and the evaluation package. Mapping-only results contain no analytical scorecard. Blocked results contain only diagnostics and readiness.

## 6. Interpret within the evidence boundary

Mapping assessment establishes evaluability under the declared contract. It does not establish typical enterprise mapping effort, organisational adoption utility, causal impact, productivity, or realised return on investment.
