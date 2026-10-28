# Canonical Event Example

Use this JSONL shape as the staging target for enterprise exports. Each line is one telemetry event.

Required for most evaluations:
- `event_id`
- `timestamp`
- `event_type`
- `run_id`
- `trace_id`
- `status`
- `step_name`

Required for the case study when available:
- `agent_configuration_id`
- `agent_version`
- `configuration_hash`
- `input_tokens`
- `output_tokens`
- tool-call or intervention markers

Do not place raw prompt bodies, ticket text, user text, credentials, or unrestricted operational payloads in this staging file unless the local data approval explicitly permits it.
