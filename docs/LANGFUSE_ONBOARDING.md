# Offline Langfuse Onboarding

The toolkit reads exported files only. It does not connect to Langfuse, accept API credentials, or retain the source export in generated result folders.

## Supported input

`prepare-langfuse` accepts:

- JSON arrays of observation rows
- JSONL with one observation per line
- gzip-compressed JSON or JSONL
- directories containing supported files
- complete saved Observations API responses with `data` and a null/missing pagination cursor

CamelCase API fields and snake_case enriched-export fields are normalized to the same contract. A non-null API cursor is rejected because the capture is incomplete.

The command also emits the common vendor-neutral `source_profile.json`, editable `mapping_spec.json`, `mapping_assessment.json`, mapping-friction profile, issue register, and readiness report. Supply `--activity-log` to include directly recorded onboarding effort and decision activity.

## Mapping contract

The mapping records:

- `workflow_family` and `workflow_definition_id`
- metadata paths for business status, configuration ID/version/hash, retry, intervention, handoff, routing, and quality
- an explicitly approved mapping from source status values to `completed` and `failed`

Business success is never inferred from the absence of an error. One Langfuse trace is only a candidate workflow run until the generated `trace_audit_sample.csv` is reviewed.

## Data protection

Langfuse `input` and `output` values are fully masked. By default, evidence retains only `[REDACTED]`, JSON type, and character length; it retains neither plaintext nor a raw-value hash. A keyed HMAC-SHA-256 descriptor is available only when explicitly configured with a key supplied through the environment. Raw exports and HMAC keys must remain outside the repository and follow the applicable enterprise retention policy.

## Approval and tier gate

The generated monitoring manifest remains intentionally blocked. After reviewing the trace audit:

1. Set `onboarding.trace_as_workflow_run_approved` to `true`.
2. Set `onboarding.enterprise_evaluation_gate` to `approved`.
3. Record `onboarding.reviewer` and `onboarding.review_evidence`.
4. Add structured `run_exclusions` for rejected traces.
5. Confirm the provisional run-identity mapping and workflow-boundary approval in `mapping_spec.json`.
6. Re-run the common `assess-mapping` command and supply the resulting assessment to `validate` and `run`.
7. Apply the manifest minimum tier: mapping-only outputs remain available for unresolved safe mapping work, partial analytics require explicit denominators, and complete analytics require all requested evidence.

Monitoring output is descriptive. It must not be interpreted as causal productivity or ROI evidence.
