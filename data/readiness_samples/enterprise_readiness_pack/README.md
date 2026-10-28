# Enterprise Readiness Sample Pack

These samples are synthetic and illustrative. They are included to exercise the enterprise-data-ready workflow before real telemetry access.

Recommended checks:

```bash
agent-eval-toolkit validate \
  --workflow-config data/sample_inputs/pilot_workflow.json \
  --raw-logs data/sample_inputs/pilot_logs.jsonl \
  --evaluation-manifest data/sample_inputs/evaluation_manifest.json
```

Sample intent:
- `complete_telemetry.jsonl`: all core signals present.
- `missing_telemetry.jsonl`: intentionally missing trace/config/token fields; validation should fail if used with strict requirements.
- `comparator_telemetry.jsonl`: two agent configuration IDs for comparator-delta testing.
- `redaction_telemetry.jsonl`: contains restricted text fields that must be redacted by workflow config.
