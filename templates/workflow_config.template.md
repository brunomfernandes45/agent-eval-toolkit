# Workflow Config Template

This file maps canonical JSONL fields into the toolkit's normalized telemetry model.

Keep `capture_raw_fields` small. Only include fields required to audit a metric or reproduce a mapping decision.

Use `allowed_metadata_fields` as the canonical allowlist for normalized event metadata. `metadata_fields` is treated as a legacy/backward-compatible alias and should not be used for new evaluations.

Place sensitive fields in both `restricted_fields` and `redact_raw_fields`. The toolkit rejects copied restricted fields that are not protected by redaction.

Declare `run_boundaries` explicitly. Only signals listed in `telemetry_capabilities` may be interpreted as observed zeros; undeclared counters remain unsupported (`null`).

Use `anchor_hash_only` when no value snapshots may be retained; it clears both captured evidence and normalized metadata. `anchor_hash_redacted_snapshot` retains only explicit allowlists and masks every declared restricted field.
