from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from statistics import mean
from typing import Any

from .exceptions import ConfigurationError
from .models import PilotScorecard, RunScorecard, WorkflowRun
from .validation import validate_baseline_context, validate_evaluation_manifest

DESIGN_CLAIM_CEILING = {
    "monitoring": "descriptive",
    "before_after": "associational",
    "ab_test": "causal_if_valid",
    "staged_rollout": "causal_if_valid",
    "scenario_suite": "conformance",
}

MONEY_QUANTUM = Decimal("0.001")
OBSERVED_COST_QUANTUM = Decimal("0.000001")


def _safe_mean(values: list[float]) -> float | None:
    return round(mean(values), 3) if values else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (len(ordered) - 1) * percentile
    lower_index = int(rank)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    weight = rank - lower_index
    return round(ordered[lower_index] * (1 - weight) + ordered[upper_index] * weight, 3)


def _decimal(value: int | float | str | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _rounded_money(value: Decimal, *, observed_cost: bool = False) -> float:
    quantum = OBSERVED_COST_QUANTUM if observed_cost else MONEY_QUANTUM
    return float(value.quantize(quantum, rounding=ROUND_HALF_UP))


def _decimal_sum(values: list[float]) -> Decimal:
    return sum((_decimal(value) for value in values), start=Decimal("0"))


def _money_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return _rounded_money(_decimal_sum(values) / len(values), observed_cost=True)


def _money_percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(_decimal(value) for value in values)
    if len(ordered) == 1:
        return _rounded_money(ordered[0], observed_cost=True)
    rank = Decimal(len(ordered) - 1) * _decimal(percentile)
    lower_index = int(rank)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    weight = rank - lower_index
    value = ordered[lower_index] * (Decimal("1") - weight) + ordered[upper_index] * weight
    return _rounded_money(value, observed_cost=True)


def _primary_or_unknown(values: list[str]) -> str:
    return values[0] if values else "unknown"


def _coverage(values: list[Any], denominator: int) -> dict[str, Any]:
    observed = len(values)
    return {
        "observed_run_count": observed,
        "run_denominator": denominator,
        "coverage": round(observed / denominator, 3) if denominator else None,
        "complete": denominator > 0 and observed == denominator,
    }


def _estimate_cycle_time_delta(
    duration_seconds: float, baseline_cycle_time_seconds: float
) -> float:
    """Return a positive value when the observed run has a shorter cycle time."""

    return baseline_cycle_time_seconds - duration_seconds


def _estimate_human_effort_delta(
    agent_human_effort_seconds: float, manual_human_effort_seconds: float
) -> float:
    """Return a positive value when the agent-assisted run uses less human effort."""

    return manual_human_effort_seconds - agent_human_effort_seconds


def _estimate_labor_value(
    human_effort_saved_seconds: float, labor_rate_usd_per_hour: float
) -> Decimal:
    return (
        _decimal(human_effort_saved_seconds) * _decimal(labor_rate_usd_per_hour) / Decimal("3600")
    )


def _estimate_net_value(
    benefit_value_usd: Decimal,
    observed_cost_usd: float,
    overhead_cost_usd_per_run: float,
    tooling_cost_usd_per_run: float,
) -> Decimal:
    return (
        benefit_value_usd
        - _decimal(observed_cost_usd)
        - _decimal(overhead_cost_usd_per_run)
        - _decimal(tooling_cost_usd_per_run)
    )


def _quality_gate(manifest: dict[str, Any]) -> dict[str, Any] | None:
    gate = manifest.get("quality_gate")
    return gate if isinstance(gate, dict) else None


def _benefit_eligibility(
    run: WorkflowRun, evaluation_manifest: dict[str, Any]
) -> tuple[bool, dict[str, Any], str | None]:
    if run.status != "completed":
        return False, {"basis": "none", "run_completed": False}, "run_not_completed"

    if run.business_success is True:
        return (
            True,
            {
                "basis": "observed_business_success",
                "run_completed": True,
                "business_success": True,
            },
            None,
        )

    gate = _quality_gate(evaluation_manifest)
    telemetry = evaluation_manifest["telemetry_requirements"]
    quality_declared = "quality_score" in telemetry["required"] + telemetry["optional"]
    gate_approved = bool(gate and gate.get("approved") is True)
    threshold = gate.get("minimum_score") if gate else None
    threshold_value = (
        float(threshold)
        if isinstance(threshold, int | float | Decimal) and not isinstance(threshold, bool)
        else None
    )
    quality_passed = (
        quality_declared
        and gate_approved
        and threshold_value is not None
        and run.avg_quality_score is not None
        and run.avg_quality_score >= threshold_value
    )
    if quality_passed:
        return (
            True,
            {
                "basis": "approved_quality_gate",
                "run_completed": True,
                "quality_score": run.avg_quality_score,
                "minimum_score": threshold_value,
                "quality_signal_declared": True,
                "quality_gate_approved": True,
            },
            None,
        )

    evidence = {
        "basis": "none",
        "run_completed": True,
        "business_success": run.business_success,
        "quality_score": run.avg_quality_score,
        "quality_signal_declared": quality_declared,
        "quality_gate_approved": gate_approved,
        "minimum_score": threshold,
    }
    if gate_approved and quality_declared and run.avg_quality_score is not None:
        return False, evidence, "approved_quality_threshold_not_met"
    return False, evidence, "business_success_or_approved_quality_evidence_unavailable"


def _is_synthetic_or_benchmark(manifest: dict[str, Any]) -> bool:
    evidence_context = manifest.get("evidence_context")
    if isinstance(evidence_context, dict):
        source_kind = evidence_context.get("source_kind")
        if source_kind in {"synthetic_fixture", "benchmark"}:
            return True
        if source_kind in {"enterprise_operational", "canonical_prevalidated"}:
            return False
    explicit_scope = manifest.get("evidence_scope", manifest.get("data_provenance", ""))
    taxonomy = manifest.get("taxonomy", {})
    taxonomy_values = list(taxonomy.values()) if isinstance(taxonomy, dict) else []
    searchable = [
        explicit_scope,
        manifest.get("evaluation_id", ""),
        manifest.get("configuration", {}),
        *taxonomy_values,
        *manifest.get("interpretation_boundaries", []),
    ]
    rendered = " ".join(str(value).lower() for value in searchable)
    return "synthetic" in rendered or "benchmark" in rendered


def _claim_assessment(
    manifest: dict[str, Any], telemetry_validation: dict[str, Any] | None
) -> dict[str, Any]:
    design = manifest["evaluation_design"]
    ceiling = DESIGN_CLAIM_CEILING[design]
    telemetry_passed = telemetry_validation.get("passed") if telemetry_validation else None
    config_result = (
        telemetry_validation.get("configuration_consistency", {}) if telemetry_validation else {}
    )
    configuration_passed = config_result.get("passed")
    validity = manifest.get("design_validity", {})
    causal_validity_approved = bool(isinstance(validity, dict) and validity.get("approved") is True)
    synthetic = _is_synthetic_or_benchmark(manifest)

    withheld_reasons: list[str] = []
    if telemetry_passed is False:
        withheld_reasons.append("required_telemetry_gate_failed")
    elif telemetry_passed is None:
        withheld_reasons.append("required_telemetry_gate_not_evaluated")
    if configuration_passed is False:
        withheld_reasons.append("configuration_identity_gate_failed")
    elif configuration_passed is None:
        withheld_reasons.append("configuration_identity_gate_not_evaluated")
    if design in {"ab_test", "staged_rollout"} and not causal_validity_approved:
        withheld_reasons.append("causal_validity_gate_not_approved")
    if synthetic:
        withheld_reasons.append(
            "synthetic_or_benchmark_evidence_caps_external_and_causal_claims_at_conformance_readiness"
        )

    if telemetry_passed is False or configuration_passed is False:
        attained = "insufficient_evidence"
    elif telemetry_passed is None or configuration_passed is None:
        attained = "not_assessed"
    elif synthetic:
        attained = "conformance_readiness"
    elif design == "monitoring":
        attained = "descriptive"
    elif design == "before_after":
        attained = "associational"
    elif design == "scenario_suite":
        attained = "conformance"
    elif causal_validity_approved:
        attained = "causal"
    else:
        attained = "comparative"

    return {
        "design_claim_ceiling": ceiling,
        "design_evidence_status": {
            "telemetry_gate": (
                "passed"
                if telemetry_passed is True
                else "failed" if telemetry_passed is False else "not_evaluated"
            ),
            "configuration_identity_gate": (
                "passed"
                if configuration_passed is True
                else "failed" if configuration_passed is False else "not_evaluated"
            ),
            "causal_validity_gate": (
                "approved"
                if causal_validity_approved
                else "not_approved" if design in {"ab_test", "staged_rollout"} else "not_applicable"
            ),
            "synthetic_or_benchmark": synthetic,
        },
        "attained_claim_strength": attained,
        "withheld_reasons": withheld_reasons,
    }


def _config_metric_summary(config_runs: list[WorkflowRun]) -> dict[str, Any]:
    denominator = len(config_runs)
    completed = [run for run in config_runs if run.status == "completed"]
    failed = [run for run in config_runs if run.status in {"failed", "abandoned"}]
    durations = [run.duration_seconds for run in config_runs if run.duration_seconds is not None]
    costs = [run.observed_cost_usd for run in config_runs if run.observed_cost_usd is not None]
    inputs = [float(run.input_tokens) for run in config_runs if run.input_tokens is not None]
    outputs = [float(run.output_tokens) for run in config_runs if run.output_tokens is not None]
    retries = [float(run.retry_count) for run in config_runs if run.retry_count is not None]
    interventions = [
        float(run.intervention_count) for run in config_runs if run.intervention_count is not None
    ]
    tool_calls = [
        float(run.tool_call_count) for run in config_runs if run.tool_call_count is not None
    ]
    handoffs = [float(run.handoff_count) for run in config_runs if run.handoff_count is not None]
    routes = [
        float(run.routing_decision_count)
        for run in config_runs
        if run.routing_decision_count is not None
    ]
    quality = [run.avg_quality_score for run in config_runs if run.avg_quality_score is not None]
    human_effort = [
        run.human_effort_seconds for run in config_runs if run.human_effort_seconds is not None
    ]

    coverage_by_metric = {
        "duration_seconds": _coverage(durations, denominator),
        "observed_cost_usd": _coverage(costs, denominator),
        "input_tokens": _coverage(inputs, denominator),
        "output_tokens": _coverage(outputs, denominator),
        "retry_count": _coverage(retries, denominator),
        "intervention_count": _coverage(interventions, denominator),
        "tool_call_count": _coverage(tool_calls, denominator),
        "handoff_count": _coverage(handoffs, denominator),
        "routing_decision_count": _coverage(routes, denominator),
        "quality_score": _coverage(quality, denominator),
        "human_effort_seconds": _coverage(human_effort, denominator),
    }
    cost_partial = _rounded_money(_decimal_sum(costs), observed_cost=True) if costs else None
    return {
        "run_count": denominator,
        "completion_rate": round(len(completed) / denominator, 3),
        "failure_rate": round(len(failed) / denominator, 3),
        "avg_duration_seconds": _safe_mean(durations),
        "p50_duration_seconds": _percentile(durations, 0.5),
        "p90_duration_seconds": _percentile(durations, 0.9),
        "avg_observed_cost_usd": _money_mean(costs),
        "p50_observed_cost_usd": _money_percentile(costs, 0.5),
        "p90_observed_cost_usd": _money_percentile(costs, 0.9),
        "total_observed_cost_usd": cost_partial if len(costs) == denominator else None,
        "partial_observed_cost_usd": cost_partial,
        "avg_input_tokens": _safe_mean(inputs),
        "total_input_tokens": int(sum(inputs)) if len(inputs) == denominator else None,
        "partial_input_tokens": int(sum(inputs)) if inputs else None,
        "avg_output_tokens": _safe_mean(outputs),
        "total_output_tokens": int(sum(outputs)) if len(outputs) == denominator else None,
        "partial_output_tokens": int(sum(outputs)) if outputs else None,
        "avg_retry_count": _safe_mean(retries),
        "avg_intervention_count": _safe_mean(interventions),
        "avg_tool_call_count": _safe_mean(tool_calls),
        "avg_handoff_count": _safe_mean(handoffs),
        "avg_routing_decision_count": _safe_mean(routes),
        "avg_quality_score": _safe_mean(quality),
        "avg_human_effort_seconds": _safe_mean(human_effort),
        "coverage_by_metric": coverage_by_metric,
    }


DELTA_COVERAGE_SIGNAL = {
    "avg_duration_seconds": "duration_seconds",
    "p50_duration_seconds": "duration_seconds",
    "p90_duration_seconds": "duration_seconds",
    "avg_observed_cost_usd": "observed_cost_usd",
    "p50_observed_cost_usd": "observed_cost_usd",
    "p90_observed_cost_usd": "observed_cost_usd",
    "total_observed_cost_usd": "observed_cost_usd",
    "avg_input_tokens": "input_tokens",
    "total_input_tokens": "input_tokens",
    "avg_output_tokens": "output_tokens",
    "total_output_tokens": "output_tokens",
    "avg_retry_count": "retry_count",
    "avg_intervention_count": "intervention_count",
    "avg_tool_call_count": "tool_call_count",
    "avg_handoff_count": "handoff_count",
    "avg_routing_decision_count": "routing_decision_count",
    "avg_quality_score": "quality_score",
    "avg_human_effort_seconds": "human_effort_seconds",
}


def _build_comparator_deltas(
    by_agent_configuration: dict[str, dict[str, Any]], evaluation_manifest: dict[str, Any]
) -> dict[str, Any]:
    if evaluation_manifest["evaluation_design"] == "monitoring":
        return {}
    baseline_config = evaluation_manifest["baseline"].get("agent_configuration_id")
    comparator_config = evaluation_manifest["comparator"].get("agent_configuration_id")
    if not baseline_config or not comparator_config:
        return {}
    if (
        baseline_config not in by_agent_configuration
        or comparator_config not in by_agent_configuration
    ):
        return {}

    baseline = by_agent_configuration[baseline_config]
    comparator = by_agent_configuration[comparator_config]
    deltas: dict[str, Any] = {}
    relative_deltas: dict[str, Any] = {}
    withheld_metrics: dict[str, str] = {}
    metric_names = [
        "run_count",
        "completion_rate",
        "failure_rate",
        *DELTA_COVERAGE_SIGNAL,
    ]
    for metric_name in metric_names:
        baseline_value = baseline.get(metric_name)
        comparator_value = comparator.get(metric_name)
        coverage_signal = DELTA_COVERAGE_SIGNAL.get(metric_name)
        if coverage_signal and not (
            baseline["coverage_by_metric"][coverage_signal]["complete"]
            and comparator["coverage_by_metric"][coverage_signal]["complete"]
        ):
            withheld_metrics[metric_name] = "incomplete_coverage"
            continue
        if (
            isinstance(comparator_value, int | float)
            and not isinstance(comparator_value, bool)
            and isinstance(baseline_value, int | float)
            and not isinstance(baseline_value, bool)
        ):
            if "usd" in metric_name:
                delta = _rounded_money(_decimal(comparator_value) - _decimal(baseline_value))
            else:
                delta = round(comparator_value - baseline_value, 3)
            deltas[f"{metric_name}_delta"] = delta
            if baseline_value != 0:
                relative_deltas[f"{metric_name}_relative_delta"] = round(
                    (comparator_value - baseline_value) / abs(baseline_value), 6
                )
    return {
        "baseline_agent_configuration_id": baseline_config,
        "comparator_agent_configuration_id": comparator_config,
        "values": deltas,
        "relative_values": relative_deltas,
        "withheld_metrics": withheld_metrics,
    }


def _build_comparator_summary(
    runs: list[WorkflowRun],
    evaluation_manifest: dict[str, Any],
    claim_assessment: dict[str, Any],
) -> dict[str, Any]:
    grouped: dict[str, list[WorkflowRun]] = {}
    for run in runs:
        grouped.setdefault(_primary_or_unknown(run.agent_configuration_ids), []).append(run)
    by_configuration = {
        config_id: _config_metric_summary(config_runs)
        for config_id, config_runs in sorted(grouped.items())
    }
    deltas = (
        {}
        if claim_assessment["attained_claim_strength"] in {"insufficient_evidence", "not_assessed"}
        or claim_assessment["design_evidence_status"].get("comparator_metric_gate") == "withheld"
        else _build_comparator_deltas(by_configuration, evaluation_manifest)
    )
    return {
        "evaluation_design": evaluation_manifest["evaluation_design"],
        **claim_assessment,
        "baseline": evaluation_manifest["baseline"],
        "comparator": evaluation_manifest["comparator"],
        "measurement_window": evaluation_manifest["measurement_window"],
        "by_agent_configuration": by_configuration,
        "deltas": deltas,
    }


def _run_estimate(
    run: WorkflowRun,
    evaluation_manifest: dict[str, Any],
    assumptions: dict[str, Any],
) -> dict[str, Any]:
    benefit_eligible, benefit_evidence, ineligibility_reason = _benefit_eligibility(
        run, evaluation_manifest
    )
    values: dict[str, Any] = {}
    assumptions_used: dict[str, Any] = {}
    withheld_reasons: list[str] = []

    cycle_baseline_key = (
        "manual_baseline_cycle_time_seconds"
        if assumptions.get("manual_baseline_cycle_time_seconds") is not None
        else "manual_baseline_duration_seconds"
    )
    baseline_cycle_time = assumptions.get(cycle_baseline_key)
    manual_human_effort = assumptions.get("manual_baseline_human_effort_seconds")
    assumed_agent_effort = assumptions.get("agent_human_effort_seconds")
    agent_human_effort = (
        run.human_effort_seconds if run.human_effort_seconds is not None else assumed_agent_effort
    )
    labor_rate = assumptions.get("loaded_labor_rate_usd_per_hour")
    overhead = assumptions.get("overhead_cost_usd_per_run", 0.0)
    tooling = assumptions.get("tooling_cost_usd_per_run", 0.0)

    if benefit_eligible and run.duration_seconds is not None and baseline_cycle_time is not None:
        values["estimated_cycle_time_difference"] = round(
            _estimate_cycle_time_delta(run.duration_seconds, baseline_cycle_time), 3
        )
        assumptions_used[cycle_baseline_key] = baseline_cycle_time
    elif benefit_eligible and run.duration_seconds is None:
        withheld_reasons.append("cycle_time_delta_withheld_duration_unavailable")
    elif benefit_eligible and baseline_cycle_time is None:
        withheld_reasons.append("cycle_time_delta_withheld_manual_cycle_time_unavailable")

    benefit_value: Decimal | None
    if not benefit_eligible:
        benefit_value = Decimal("0")
        values["estimated_labor_value_delta_usd"] = 0.0
    elif manual_human_effort is None:
        benefit_value = None
        withheld_reasons.append("labor_value_withheld_manual_human_effort_unavailable")
    elif agent_human_effort is None:
        benefit_value = None
        withheld_reasons.append("labor_value_withheld_agent_human_effort_unavailable")
    elif labor_rate is None:
        benefit_value = None
        withheld_reasons.append("labor_value_withheld_loaded_labor_rate_unavailable")
    else:
        effort_delta = _estimate_human_effort_delta(agent_human_effort, manual_human_effort)
        values["estimated_human_effort_saved_seconds_vs_manual_baseline"] = round(effort_delta, 3)
        benefit_value = _estimate_labor_value(effort_delta, labor_rate)
        values["estimated_labor_value_delta_usd"] = _rounded_money(benefit_value)
        assumptions_used["manual_baseline_human_effort_seconds"] = manual_human_effort
        assumptions_used["loaded_labor_rate_usd_per_hour"] = labor_rate
        if run.human_effort_seconds is None:
            assumptions_used["agent_human_effort_seconds"] = assumed_agent_effort

    assumptions_used["overhead_cost_usd_per_run"] = overhead
    assumptions_used["tooling_cost_usd_per_run"] = tooling
    if run.observed_cost_usd is None:
        withheld_reasons.append("net_value_withheld_observed_cost_unavailable")
    elif benefit_value is None:
        withheld_reasons.append("net_value_withheld_benefit_value_unavailable")
    else:
        values["estimated_net_value_delta_usd"] = _rounded_money(
            _estimate_net_value(benefit_value, run.observed_cost_usd, overhead, tooling)
        )

    if ineligibility_reason:
        withheld_reasons.insert(0, ineligibility_reason)
    return {
        "eligible": benefit_eligible,
        "benefit_eligible": benefit_eligible,
        "ineligibility_reason": ineligibility_reason,
        "benefit_evidence": benefit_evidence,
        "values": values,
        "assumptions_used": assumptions_used,
        "withheld_reasons": list(dict.fromkeys(withheld_reasons)),
    }


def _build_sensitivity_summary(
    runs: list[WorkflowRun],
    estimates: list[dict[str, Any]],
    assumptions: dict[str, Any],
) -> dict[str, Any]:
    sensitivity = assumptions.get("sensitivity", {})
    if not sensitivity:
        return {}
    eligible_pairs = [
        (run, estimate)
        for run, estimate in zip(runs, estimates, strict=True)
        if estimate["benefit_eligible"]
    ]
    result: dict[str, Any] = {}

    cycle_key = (
        "manual_baseline_cycle_time_seconds"
        if assumptions.get("manual_baseline_cycle_time_seconds") is not None
        else "manual_baseline_duration_seconds"
    )
    cycle_bounds = sensitivity.get(cycle_key)
    eligible_durations = [
        run.duration_seconds for run, _ in eligible_pairs if run.duration_seconds is not None
    ]
    if cycle_bounds and eligible_pairs and len(eligible_durations) == len(eligible_pairs):
        avg_duration = mean(eligible_durations)
        result["avg_estimated_cycle_time_difference"] = {
            "low": round(cycle_bounds["low"] - avg_duration, 3),
            "high": round(cycle_bounds["high"] - avg_duration, 3),
        }

    effort_bounds = sensitivity.get("manual_baseline_human_effort_seconds")
    rate_bounds = sensitivity.get("loaded_labor_rate_usd_per_hour")
    manual_effort = assumptions.get("manual_baseline_human_effort_seconds")
    rate = assumptions.get("loaded_labor_rate_usd_per_hour")
    if manual_effort is None or rate is None:
        return result
    low_effort = effort_bounds["low"] if effort_bounds else manual_effort
    high_effort = effort_bounds["high"] if effort_bounds else manual_effort
    low_rate = rate_bounds["low"] if rate_bounds else rate
    high_rate = rate_bounds["high"] if rate_bounds else rate
    agent_efforts = [
        (
            run.human_effort_seconds
            if run.human_effort_seconds is not None
            else assumptions.get("agent_human_effort_seconds")
        )
        for run, _ in eligible_pairs
    ]
    if eligible_pairs and all(value is not None for value in agent_efforts):
        low_total = sum(
            (
                _estimate_labor_value(
                    _estimate_human_effort_delta(float(value), low_effort), low_rate
                )
                for value in agent_efforts
                if value is not None
            ),
            start=Decimal("0"),
        )
        high_total = sum(
            (
                _estimate_labor_value(
                    _estimate_human_effort_delta(float(value), high_effort), high_rate
                )
                for value in agent_efforts
                if value is not None
            ),
            start=Decimal("0"),
        )
        result["total_estimated_labor_value_delta_usd"] = {
            "low": _rounded_money(low_total),
            "high": _rounded_money(high_total),
        }
        observed_costs = [
            run.observed_cost_usd for run in runs if run.observed_cost_usd is not None
        ]
        if len(observed_costs) == len(runs):
            fixed_costs = (
                _decimal_sum(observed_costs)
                + _decimal(assumptions.get("overhead_cost_usd_per_run", 0.0)) * len(runs)
                + _decimal(assumptions.get("tooling_cost_usd_per_run", 0.0)) * len(runs)
            )
            result["total_estimated_net_value_delta_usd"] = {
                "low": _rounded_money(low_total - fixed_costs),
                "high": _rounded_money(high_total - fixed_costs),
            }
    return result


def build_scorecard(
    runs: list[WorkflowRun],
    evaluation_manifest: dict[str, Any],
    baseline_context: dict[str, Any] | None = None,
    telemetry_validation: dict[str, Any] | None = None,
    measurement_window_summary: dict[str, Any] | None = None,
    evaluation_readiness: dict[str, Any] | None = None,
) -> PilotScorecard:
    if not runs:
        raise ValueError("No workflow runs available for scorecard generation")
    workflow_families = {run.workflow_family for run in runs}
    workflow_definition_ids = {run.workflow_definition_id for run in runs}
    if len(workflow_families) != 1 or len(workflow_definition_ids) != 1:
        raise ConfigurationError(
            "All runs in one scorecard must belong to the same workflow family and definition"
        )

    manifest = validate_evaluation_manifest(evaluation_manifest)
    assumptions = dict(manifest.get("assumptions", {}))
    assumptions.update(validate_baseline_context(baseline_context))
    assumptions = validate_baseline_context(assumptions)
    claim_assessment = _claim_assessment(manifest, telemetry_validation)
    readiness_metric_statuses = (evaluation_readiness or {}).get("metric_statuses", {})
    attained_tier = (evaluation_readiness or {}).get("attained_tier", "not_assessed")
    claim_assessment["design_evidence_status"]["evidence_readiness_tier"] = attained_tier
    comparator_status = readiness_metric_statuses.get("comparator", {}).get("status")
    claim_assessment["design_evidence_status"]["comparator_metric_gate"] = (
        comparator_status or "not_applicable"
    )
    if comparator_status == "withheld":
        claim_assessment["withheld_reasons"].append("comparator_metric_preconditions_failed")
    if (evaluation_readiness or {}).get("quarantined_run_count", 0):
        claim_assessment["withheld_reasons"].append(
            "claim_scope_excludes_one_or_more_quarantined_source_runs"
        )
    estimates = [_run_estimate(run, manifest, assumptions) for run in runs]

    completed_runs = [run for run in runs if run.status == "completed"]
    failed_runs = [run for run in runs if run.status in {"failed", "abandoned"}]
    durations = [run.duration_seconds for run in runs if run.duration_seconds is not None]
    costs = [run.observed_cost_usd for run in runs if run.observed_cost_usd is not None]
    inputs = [run.input_tokens for run in runs if run.input_tokens is not None]
    outputs = [run.output_tokens for run in runs if run.output_tokens is not None]
    retries = [run.retry_count for run in runs if run.retry_count is not None]
    interventions = [run.intervention_count for run in runs if run.intervention_count is not None]
    tool_calls = [run.tool_call_count for run in runs if run.tool_call_count is not None]
    handoffs = [run.handoff_count for run in runs if run.handoff_count is not None]
    routes = [run.routing_decision_count for run in runs if run.routing_decision_count is not None]
    quality = [run.avg_quality_score for run in runs if run.avg_quality_score is not None]
    human_effort = [
        run.human_effort_seconds for run in runs if run.human_effort_seconds is not None
    ]
    business_success = [run.business_success for run in runs if run.business_success is not None]
    denominator = len(runs)

    cost_partial = _rounded_money(_decimal_sum(costs), observed_cost=True) if costs else None
    aggregate_coverage = {
        "observed_cost_usd": {
            **_coverage(costs, denominator),
            "partial_sum": cost_partial,
        },
        "input_tokens": {
            **_coverage(inputs, denominator),
            "partial_sum": sum(inputs) if inputs else None,
        },
        "output_tokens": {
            **_coverage(outputs, denominator),
            "partial_sum": sum(outputs) if outputs else None,
        },
    }
    if len(inputs) == denominator:
        total_input_tokens = sum(inputs)
    else:
        total_input_tokens = None
    if len(outputs) == denominator:
        total_output_tokens = sum(outputs)
    else:
        total_output_tokens = None

    observed_summary = {
        "completion_rate": round(len(completed_runs) / denominator, 3),
        "failure_rate": round(len(failed_runs) / denominator, 3),
        "run_count_completed": len(completed_runs),
        "run_count_failed": len(failed_runs),
        "run_count_total": denominator,
        "avg_duration_seconds": _safe_mean(durations),
        "p50_duration_seconds": _percentile(durations, 0.5),
        "p90_duration_seconds": _percentile(durations, 0.9),
        "avg_retry_count": _safe_mean([float(value) for value in retries]),
        "avg_intervention_count": _safe_mean([float(value) for value in interventions]),
        "avg_tool_call_count": _safe_mean([float(value) for value in tool_calls]),
        "avg_handoff_count": _safe_mean([float(value) for value in handoffs]),
        "avg_routing_decision_count": _safe_mean([float(value) for value in routes]),
        "avg_quality_score": _safe_mean(quality),
        "avg_human_effort_seconds": _safe_mean(human_effort),
        "total_observed_cost_usd": cost_partial if len(costs) == denominator else None,
        "partial_observed_cost_usd": cost_partial,
        "p50_observed_cost_usd": _money_percentile(costs, 0.5),
        "p90_observed_cost_usd": _money_percentile(costs, 0.9),
        "total_input_tokens": total_input_tokens,
        "partial_input_tokens": sum(inputs) if inputs else None,
        "total_output_tokens": total_output_tokens,
        "partial_output_tokens": sum(outputs) if outputs else None,
        "runs_with_observed_cost": len(costs),
        "runs_with_quality_score": len(quality),
        "runs_with_business_success": len(business_success),
        "runs_with_human_effort_seconds": len(human_effort),
        "runs_with_retry_count": len(retries),
        "runs_with_intervention_count": len(interventions),
        "runs_with_tool_call_count": len(tool_calls),
        "runs_with_handoff_count": len(handoffs),
        "runs_with_routing_decision_count": len(routes),
        "runs_with_input_tokens": len(inputs),
        "runs_with_output_tokens": len(outputs),
        "aggregate_coverage": aggregate_coverage,
        "percentile_method": "linear_type_7",
    }

    run_scorecards: list[RunScorecard] = []
    for run, estimate in zip(runs, estimates, strict=True):
        run_scorecards.append(
            RunScorecard(
                run_id=run.run_id,
                observed_metrics={
                    "status": run.status,
                    "completion_state": run.completion_state,
                    "duration_seconds": run.duration_seconds,
                    "business_success": run.business_success,
                    "human_effort_seconds": run.human_effort_seconds,
                    "retry_count": run.retry_count,
                    "intervention_count": run.intervention_count,
                    "observed_cost_usd": run.observed_cost_usd,
                    "input_tokens": run.input_tokens,
                    "output_tokens": run.output_tokens,
                    "aggregation_semantics": run.aggregation_semantics,
                    "agent_configuration_ids": run.agent_configuration_ids,
                    "agent_versions": run.agent_versions,
                    "configuration_hashes": run.configuration_hashes,
                    "model_names": run.model_names,
                    "prompt_versions": run.prompt_versions,
                    "tool_call_count": run.tool_call_count,
                    "handoff_count": run.handoff_count,
                    "routing_decision_count": run.routing_decision_count,
                    "failure_modes": run.failure_modes,
                    "avg_quality_score": run.avg_quality_score,
                    "step_count": run.step_count,
                    "unsupported_telemetry": run.unsupported_telemetry,
                },
                assumption_based_estimates=estimate,
                missing_telemetry=run.missing_telemetry,
                evidence={
                    "event_ids": run.event_ids,
                    "trace_ids": run.trace_ids,
                    "configuration": {
                        "agent_configuration_ids": run.agent_configuration_ids,
                        "agent_versions": run.agent_versions,
                        "configuration_hashes": run.configuration_hashes,
                    },
                    "source_refs": run.evidence,
                },
            )
        )

    benefit_eligible_count = sum(estimate["benefit_eligible"] for estimate in estimates)
    cycle_values = [
        estimate["values"]["estimated_cycle_time_difference"]
        for estimate in estimates
        if "estimated_cycle_time_difference" in estimate["values"]
    ]
    effort_values = [
        estimate["values"]["estimated_human_effort_saved_seconds_vs_manual_baseline"]
        for estimate in estimates
        if "estimated_human_effort_saved_seconds_vs_manual_baseline" in estimate["values"]
    ]
    labor_values = [
        estimate["values"]["estimated_labor_value_delta_usd"]
        for estimate in estimates
        if "estimated_labor_value_delta_usd" in estimate["values"]
    ]
    net_values = [
        estimate["values"]["estimated_net_value_delta_usd"]
        for estimate in estimates
        if "estimated_net_value_delta_usd" in estimate["values"]
    ]
    summary_values: dict[str, Any] = {}
    partial_values: dict[str, Any] = {}
    if cycle_values:
        partial_values["partial_estimated_cycle_time_difference"] = round(sum(cycle_values), 3)
        if len(cycle_values) == benefit_eligible_count:
            summary_values["avg_estimated_cycle_time_difference"] = round(mean(cycle_values), 3)
            summary_values["total_estimated_cycle_time_difference"] = round(sum(cycle_values), 3)
    if effort_values:
        partial_values["partial_estimated_human_effort_saved_seconds"] = round(
            sum(effort_values), 3
        )
        if len(effort_values) == benefit_eligible_count:
            summary_values["avg_estimated_human_effort_saved_seconds_vs_manual_baseline"] = round(
                mean(effort_values), 3
            )
            summary_values["total_estimated_human_effort_saved_seconds_vs_manual_baseline"] = round(
                sum(effort_values), 3
            )
    if labor_values:
        labor_total = _decimal_sum(labor_values)
        partial_values["partial_estimated_labor_value_delta_usd"] = _rounded_money(labor_total)
        if len(labor_values) == denominator:
            summary_values["avg_estimated_labor_value_delta_usd"] = _rounded_money(
                labor_total / denominator
            )
            summary_values["total_estimated_labor_value_delta_usd"] = _rounded_money(labor_total)
    if net_values:
        net_total = _decimal_sum(net_values)
        if len(net_values) == denominator:
            summary_values["avg_estimated_net_value_delta_usd"] = _rounded_money(
                net_total / denominator
            )
            summary_values["total_estimated_net_value_delta_usd"] = _rounded_money(net_total)

    summary_withheld = list(
        dict.fromkeys(
            reason
            for estimate in estimates
            for reason in estimate["withheld_reasons"]
            if reason.startswith(("labor_value_withheld", "net_value_withheld"))
        )
    )
    assumption_based_summary = {
        "values": summary_values,
        "partial_values": partial_values,
        "assumptions_used": {
            key: value
            for estimate in estimates
            for key, value in estimate["assumptions_used"].items()
        },
        "sensitivity": _build_sensitivity_summary(runs, estimates, assumptions),
        "eligible_run_count": benefit_eligible_count,
        "ineligible_run_count": denominator - benefit_eligible_count,
        "eligibility_rule": (
            "completed_and_observed_business_success_or_declared_approved_quality_gate"
        ),
        "coverage": {
            "cycle_time_delta": _coverage(cycle_values, benefit_eligible_count),
            "human_effort_delta": _coverage(effort_values, benefit_eligible_count),
            "labor_value": _coverage(labor_values, denominator),
            "net_value": _coverage(net_values, denominator),
            "observed_cost": _coverage(costs, denominator),
        },
        "withheld_reasons": summary_withheld,
    }
    economic_status = readiness_metric_statuses.get("economic_estimates", {}).get("status")
    if economic_status == "withheld":
        assumption_based_summary["values"] = {}
        assumption_based_summary["sensitivity_analysis"] = {}
        assumption_based_summary["withheld_reasons"] = list(
            dict.fromkeys(
                [
                    *assumption_based_summary["withheld_reasons"],
                    *readiness_metric_statuses["economic_estimates"].get("withheld_reasons", []),
                ]
            )
        )

    missing_telemetry_counts: dict[str, int] = {}
    for run in runs:
        for field_name in run.missing_telemetry:
            missing_telemetry_counts[field_name] = missing_telemetry_counts.get(field_name, 0) + 1

    evaluation = {
        "evaluation_id": manifest["evaluation_id"],
        "evaluation_design": manifest["evaluation_design"],
        **claim_assessment,
        "measurement_window": manifest["measurement_window"],
        "baseline": manifest["baseline"],
        "comparator": manifest["comparator"],
        "configuration": manifest["configuration"],
        "expected_configurations": manifest.get("expected_configurations", []),
        "taxonomy": manifest["taxonomy"],
        "inclusion_rules": manifest.get("inclusion_rules", []),
        "exclusion_rules": manifest.get("exclusion_rules", []),
        "interpretation_boundaries": manifest["interpretation_boundaries"],
        "concurrent_changes": manifest.get("concurrent_changes", []),
    }
    return PilotScorecard(
        evaluation=evaluation,
        workflow_family=runs[0].workflow_family,
        workflow_definition_id=runs[0].workflow_definition_id,
        run_count=denominator,
        measurement_window_summary=measurement_window_summary
        or {
            "included_run_count": denominator,
            "excluded_run_count": 0,
            "total_reconstructed_run_count": denominator,
            "run_coverage": 1.0,
            "exclusion_reasons": {},
        },
        observed_summary=observed_summary,
        assumption_based_summary=assumption_based_summary,
        comparator_summary=_build_comparator_summary(runs, manifest, claim_assessment),
        telemetry_validation=telemetry_validation or {},
        missing_telemetry_summary=missing_telemetry_counts,
        evaluation_readiness=evaluation_readiness or {},
        metric_statuses=(evaluation_readiness or {}).get("metric_statuses", {}),
        runs=run_scorecards,
    )
