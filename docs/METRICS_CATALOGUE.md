# Metrics Catalogue

Metrics are split into observed metrics and assumption-based estimates.

## Observed Metrics

- `completion_rate`: completed runs divided by included runs.
- `failure_rate`: failed or abandoned runs divided by included runs.
- `avg_duration_seconds`: mean run duration.
- `p50_duration_seconds`, `p90_duration_seconds`: duration percentiles.
- `avg_retry_count`: mean retry events per run.
- `avg_intervention_count`: mean intervention events per run.
- `avg_handoff_count`: mean handoff events per run.
- `avg_tool_call_count`: mean tool calls per run.
- `total_input_tokens`, `total_output_tokens`: aggregate token usage.
- `total_observed_cost_usd`: aggregate observed cost when present.
- `avg_quality_score`: mean quality score when supplied.

## Comparator Metrics

When the manifest names baseline and comparator agent configuration IDs, the toolkit reports deltas for shared numeric metrics. Deltas are comparator minus baseline. A negative duration delta means the comparator was faster.

## Assumption-Based Estimates

- `estimated_cycle_time_difference`: manual baseline elapsed cycle time minus the agent run's elapsed cycle time, in seconds.
- `estimated_human_effort_saved_seconds_vs_manual_baseline`: manual active human effort minus active human effort consumed by the agent run, in seconds.
- `estimated_labor_value_delta_usd`
- `estimated_net_value_delta_usd`

These estimates require explicit assumptions in the manifest. They are not observed facts. Cycle time and active human effort are separate constructs: `manual_baseline_cycle_time_seconds` is used only for elapsed-time comparison, while `manual_baseline_human_effort_seconds` is the labour-value baseline. Agent-side effort should be observed as per-run `human_effort_seconds`; `agent_human_effort_seconds` is an explicit fallback assumption and is disclosed when used. The deprecated `manual_baseline_duration_seconds` input remains accepted as a cycle-time alias only and is never a labour-effort proxy.

## Interpretation Limits

Do not report causal impact unless the evaluation design supports it. Monitoring evidence is descriptive unless paired with a defensible comparator and bounded confounders.
