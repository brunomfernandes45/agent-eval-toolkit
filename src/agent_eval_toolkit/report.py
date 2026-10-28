from __future__ import annotations

from .models import PilotScorecard, WorkflowRun


def _display_values(values: list[str]) -> str:
    return ", ".join(values) if values else "unknown"


def render_markdown_report(scorecard: PilotScorecard, runs: list[WorkflowRun]) -> str:
    lines: list[str] = []
    lines.append("# Workflow Evaluation Report")
    lines.append("")
    evaluation = scorecard.evaluation
    lines.append(f"- Evaluation id: `{evaluation['evaluation_id']}`")
    readiness = scorecard.evaluation_readiness
    lines.append(f"- Evidence readiness tier: `{readiness.get('attained_tier', 'not_assessed')}`")
    lines.append(f"- Configured minimum tier: `{readiness.get('minimum_tier', 'complete')}`")
    lines.append(f"- Minimum tier met: `{readiness.get('minimum_tier_met', False)}`")
    lines.append(f"- Evaluation design: `{evaluation['evaluation_design']}`")
    lines.append(f"- Design claim ceiling: `{evaluation['design_claim_ceiling']}`")
    lines.append(f"- Attained claim strength: `{evaluation['attained_claim_strength']}`")
    lines.append("- Design evidence status: " f"`{evaluation['design_evidence_status']}`")
    if evaluation["withheld_reasons"]:
        lines.append("- Withheld claim reasons:")
        for reason in evaluation["withheld_reasons"]:
            lines.append(f"  - `{reason}`")
    lines.append(f"- Workflow family: `{scorecard.workflow_family}`")
    lines.append(f"- Workflow definition: `{scorecard.workflow_definition_id}`")
    lines.append(f"- Workflow runs analyzed: `{scorecard.run_count}`")
    lines.append("- Data status: interpret only within the declared boundaries below.")
    lines.append("")
    lines.append("## Metric Availability")
    for metric, details in sorted(scorecard.metric_statuses.items()):
        lines.append(
            f"- {metric}: status=`{details['status']}`, "
            f"source coverage=`{details['covered_run_count']}/"
            f"{details['population_denominator']}`, "
            f"analytically eligible=`{details['eligible_run_count']}`"
        )
        for reason in details.get("withheld_reasons", []):
            lines.append(f"  - `{reason}`")
    lines.append("")
    lines.append("## Evaluation Design")
    measurement_window = evaluation["measurement_window"]
    lines.append(
        "- Measurement window: "
        f"`{measurement_window['start']}` to `{measurement_window['end']}` "
        f"({measurement_window['timezone']})"
    )
    lines.append(f"- Baseline: `{evaluation['baseline']['name']}`")
    lines.append(f"- Baseline definition: {evaluation['baseline']['definition']}")
    lines.append(f"- Comparator: `{evaluation['comparator']['name']}`")
    lines.append(f"- Comparator definition: {evaluation['comparator']['definition']}")
    configuration = evaluation["configuration"]
    lines.append(
        "- Configuration evidence: "
        f"`{configuration['agent_configuration_id']}` "
        f"version=`{configuration['agent_version']}` "
        f"hash=`{configuration['configuration_hash']}`"
    )
    if evaluation.get("interpretation_boundaries"):
        lines.append("- Interpretation boundaries:")
        for boundary in evaluation["interpretation_boundaries"]:
            lines.append(f"  - {boundary}")
    lines.append("")
    lines.append("## Taxonomy Classification")
    for key, value in sorted(evaluation.get("taxonomy", {}).items()):
        if isinstance(value, list):
            lines.append(f"- {key}: `{', '.join(value) if value else 'none'}`")
        else:
            lines.append(f"- {key}: `{value}`")
    lines.append("")
    lines.append("## Measurement Window Coverage")
    for key, value in sorted(scorecard.measurement_window_summary.items()):
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    lines.append("## Telemetry Coverage")
    telemetry_validation = scorecard.telemetry_validation
    if telemetry_validation:
        lines.append(f"- Feasibility gate passed: `{telemetry_validation['passed']}`")
        lines.append(
            "- Required signals: " f"`{', '.join(telemetry_validation['required_signals'])}`"
        )
        if telemetry_validation["missing_required_summary"]:
            for key, value in sorted(telemetry_validation["missing_required_summary"].items()):
                lines.append(f"- missing_required.{key}: `{value}`")
        else:
            lines.append("- Missing required signals: `none`")
        if telemetry_validation["missing_optional_summary"]:
            for key, value in sorted(telemetry_validation["missing_optional_summary"].items()):
                lines.append(f"- missing_optional.{key}: `{value}`")
    else:
        lines.append("- No telemetry coverage validation was provided.")
    lines.append("")
    lines.append("## Observed Summary")
    for key, value in sorted(scorecard.observed_summary.items()):
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    lines.append("## Comparator Summary")
    comparator_summary = scorecard.comparator_summary
    lines.append(f"- Design: `{comparator_summary['evaluation_design']}`")
    lines.append("- Design claim ceiling: " f"`{comparator_summary['design_claim_ceiling']}`")
    lines.append("- Attained claim strength: " f"`{comparator_summary['attained_claim_strength']}`")
    if comparator_summary["withheld_reasons"]:
        lines.append("- Withheld claim reasons:")
        for reason in comparator_summary["withheld_reasons"]:
            lines.append(f"  - `{reason}`")
    for config_id, values in sorted(comparator_summary["by_agent_configuration"].items()):
        lines.append(
            f"- `{config_id}`: run_count=`{values['run_count']}`, "
            f"completion_rate=`{values['completion_rate']}`, "
            f"avg_duration_seconds=`{values['avg_duration_seconds']}`"
        )
    if comparator_summary["deltas"]:
        lines.append("- Comparator deltas:")
        for key, value in sorted(comparator_summary["deltas"]["values"].items()):
            lines.append(f"  - {key}: `{value}`")
        if comparator_summary["deltas"].get("relative_values"):
            lines.append("- Relative comparator deltas:")
            for key, value in sorted(comparator_summary["deltas"]["relative_values"].items()):
                lines.append(f"  - {key}: `{value}`")
    lines.append("")
    lines.append("## Assumption-Based Summary")
    lines.append(
        "- eligible_run_count: " f"`{scorecard.assumption_based_summary['eligible_run_count']}`"
    )
    lines.append(
        "- ineligible_run_count: " f"`{scorecard.assumption_based_summary['ineligible_run_count']}`"
    )
    lines.append(
        "- eligibility_rule: " f"`{scorecard.assumption_based_summary['eligibility_rule']}`"
    )
    if scorecard.assumption_based_summary["values"]:
        for key, value in sorted(scorecard.assumption_based_summary["values"].items()):
            lines.append(f"- {key}: `{value}`")
        for key, value in sorted(scorecard.assumption_based_summary["assumptions_used"].items()):
            lines.append(f"- assumption.{key}: `{value}`")
        if scorecard.assumption_based_summary["sensitivity"]:
            lines.append("- Sensitivity ranges:")
            for key, bounds in sorted(scorecard.assumption_based_summary["sensitivity"].items()):
                lines.append(f"  - {key}: low=`{bounds['low']}`, high=`{bounds['high']}`")
    else:
        lines.append("- No assumption-based estimates were generated.")
    if scorecard.assumption_based_summary.get("partial_values"):
        lines.append("- Partial assumption-based observations (not population totals):")
        for key, value in sorted(scorecard.assumption_based_summary["partial_values"].items()):
            lines.append(f"  - {key}: `{value}`")
    if scorecard.assumption_based_summary.get("coverage"):
        lines.append("- Assumption-based coverage:")
        for key, value in sorted(scorecard.assumption_based_summary["coverage"].items()):
            lines.append(f"  - {key}: `{value}`")
    if scorecard.assumption_based_summary.get("withheld_reasons"):
        lines.append("- Assumption-based values withheld because:")
        for reason in scorecard.assumption_based_summary["withheld_reasons"]:
            lines.append(f"  - `{reason}`")
    lines.append("")
    lines.append("## Missing Telemetry Summary")
    if scorecard.missing_telemetry_summary:
        for key, value in sorted(scorecard.missing_telemetry_summary.items()):
            lines.append(f"- {key}: `{value}` runs")
    else:
        lines.append("- No missing telemetry flags were raised.")
    lines.append("")
    lines.append("## Run Detail")
    for run in runs:
        lines.append(f"### Run `{run.run_id}`")
        lines.append(f"- status: `{run.status}`")
        lines.append(f"- duration_seconds: `{run.duration_seconds}`")
        lines.append(f"- retry_count: `{run.retry_count}`")
        lines.append(f"- intervention_count: `{run.intervention_count}`")
        lines.append(f"- tool_call_count: `{run.tool_call_count}`")
        lines.append(f"- avg_quality_score: `{run.avg_quality_score}`")
        lines.append(f"- observed_cost_usd: `{run.observed_cost_usd}`")
        lines.append(f"- configuration: `{_display_values(run.agent_configuration_ids)}`")
        if run.failure_modes:
            lines.append(f"- failure_modes: `{', '.join(run.failure_modes)}`")
        if run.missing_telemetry:
            lines.append(f"- missing_telemetry: `{', '.join(run.missing_telemetry)}`")
        else:
            lines.append("- missing_telemetry: `none`")
        if run.unsupported_telemetry:
            lines.append(f"- unsupported_telemetry: `{', '.join(run.unsupported_telemetry)}`")
        else:
            lines.append("- unsupported_telemetry: `none`")
        if run.evidence:
            first_ref = run.evidence[0]
            lines.append(
                "- evidence_anchor: "
                f"`{first_ref.source_file}:{first_ref.source_line}` "
                f"raw_event_id=`{first_ref.raw_event_id}`"
            )
        lines.append("")
    lines.append("## Notes")
    lines.append("- Observed metrics are derived directly from normalized telemetry.")
    lines.append(
        "- Assumption-based estimates are separated and retain their assumptions explicitly."
    )
    lines.append("- Missing telemetry is reported rather than backfilled.")
    lines.append(
        "- Evidence references intentionally avoid retaining unrestricted raw payload fields."
    )
    lines.append(
        "- Impact claims require enterprise data, approved baselines, and an evaluation design "
        "that supports the intended claim strength."
    )
    lines.append("")
    return "\n".join(lines)
