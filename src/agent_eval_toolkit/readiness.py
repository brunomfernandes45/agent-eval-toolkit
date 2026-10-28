from __future__ import annotations

from typing import Any

READINESS_SCHEMA_VERSION = "1.0.0"
TIER_ORDER = {"blocked": 0, "mapping_only": 1, "partial": 2, "complete": 3}
MINIMUM_TIERS = {"mapping_only", "partial", "complete"}

METRIC_SIGNALS: dict[str, tuple[str, ...]] = {
    "completion": ("run_id", "status", "event_type"),
    "reliability": ("run_id", "status", "event_type"),
    "duration": ("timestamp", "duration"),
    "retry": ("retry_markers",),
    "intervention": ("intervention_markers",),
    "handoff": ("handoff_targets",),
    "routing": ("routing_decisions",),
    "tool_calls": ("tool_call_trace",),
    "tokens": ("input_tokens", "output_tokens"),
    "observed_cost": ("observed_cost_usd",),
    "quality": ("quality_score",),
    "human_effort": ("human_effort_seconds",),
    "business_success": ("business_success",),
    "configuration": (
        "agent_configuration_id",
        "agent_version",
        "configuration_hash",
    ),
}


def resolve_minimum_tier(manifest: dict[str, Any], override: str | None = None) -> str:
    if override is not None:
        if override not in MINIMUM_TIERS:
            raise ValueError(f"Unsupported minimum evidence tier `{override}`")
        return override
    policy = manifest.get("publication_policy", {})
    value = policy.get("minimum_evidence_tier", "complete")
    if value not in MINIMUM_TIERS:
        raise ValueError(f"Unsupported minimum evidence tier `{value}`")
    return str(value)


def build_evaluation_readiness(
    telemetry_validation: dict[str, Any],
    manifest: dict[str, Any],
    *,
    run_count: int,
    quarantined_run_count: int,
    mapping_assessment: dict[str, Any] | None,
    governance_safe: bool,
    minimum_tier: str,
) -> dict[str, Any]:
    mapping_required = bool(
        manifest.get("evidence_context", {}).get("mapping_assessment_required", False)
    )
    if not mapping_required:
        mapping_readiness = "not_applicable"
    elif mapping_assessment is None:
        mapping_readiness = "not_assessed"
    else:
        mapping_readiness = str(mapping_assessment.get("mapping_readiness", "not_assessed"))
    mapping_governance_safe = not (
        mapping_required
        and mapping_assessment is not None
        and mapping_assessment.get("approvals", {}).get("privacy_approval") != "approved"
    )

    metric_statuses = _metric_statuses(
        telemetry_validation,
        manifest,
        run_count,
        quarantined_run_count,
    )
    usable_metric_count = sum(
        item["status"] in {"available", "partial"} for item in metric_statuses.values()
    )
    if not governance_safe or not mapping_governance_safe:
        attained = "blocked"
        analytical_readiness = "blocked"
    elif mapping_required and mapping_readiness != "verified":
        attained = "mapping_only"
        analytical_readiness = "mapping_only"
    elif run_count == 0 or usable_metric_count == 0:
        attained = "mapping_only"
        analytical_readiness = "mapping_only"
    elif telemetry_validation.get("passed") is True and quarantined_run_count == 0:
        attained = "complete"
        analytical_readiness = "complete"
    else:
        attained = "partial"
        analytical_readiness = "partial"

    reasons: list[str] = []
    if not governance_safe:
        reasons.append("governance_safety_gate_failed")
    if not mapping_governance_safe:
        reasons.append("mapping_privacy_approval_failed")
    if mapping_required and mapping_readiness != "verified":
        reasons.append(f"mapping_readiness_{mapping_readiness}")
    if run_count == 0:
        reasons.append("no_analytically_eligible_runs")
    if quarantined_run_count:
        reasons.append("one_or_more_runs_quarantined")
    if telemetry_validation.get("passed") is not True:
        reasons.append("one_or_more_requested_telemetry_requirements_unmet")
    minimum_met = TIER_ORDER[attained] >= TIER_ORDER[minimum_tier]
    return {
        "readiness_schema_version": READINESS_SCHEMA_VERSION,
        "attained_tier": attained,
        "minimum_tier": minimum_tier,
        "minimum_tier_met": minimum_met,
        "mapping_readiness": mapping_readiness,
        "analytical_readiness": analytical_readiness,
        "governance_safety": (
            "passed" if governance_safe and mapping_governance_safe else "failed"
        ),
        "metric_statuses": metric_statuses,
        "quarantined_run_count": quarantined_run_count,
        "withheld_reasons": list(dict.fromkeys(reasons)),
    }


def blocked_readiness(minimum_tier: str, reason: str) -> dict[str, Any]:
    return {
        "readiness_schema_version": READINESS_SCHEMA_VERSION,
        "attained_tier": "blocked",
        "minimum_tier": minimum_tier,
        "minimum_tier_met": False,
        "mapping_readiness": "not_assessed",
        "analytical_readiness": "blocked",
        "governance_safety": "not_assessed",
        "metric_statuses": {},
        "quarantined_run_count": 0,
        "withheld_reasons": [reason],
    }


def _metric_statuses(
    telemetry_validation: dict[str, Any],
    manifest: dict[str, Any],
    run_count: int,
    quarantined_run_count: int,
) -> dict[str, dict[str, Any]]:
    coverage = telemetry_validation.get("coverage_by_signal", {})
    required = set(manifest["telemetry_requirements"]["required"])
    optional = set(manifest["telemetry_requirements"]["optional"])
    requested = required | optional
    statuses: dict[str, dict[str, Any]] = {}
    for metric, signals in METRIC_SIGNALS.items():
        relevant = [
            signal
            for signal in signals
            if signal in requested or metric in {"completion", "reliability", "duration"}
        ]
        if not relevant:
            continue
        entries = [coverage.get(signal) for signal in relevant if coverage.get(signal) is not None]
        unsupported = [item for item in entries if item.get("supported") is False]
        observed = [int(item.get("runs_with_signal", 0)) for item in entries]
        eligible_denominator = max(
            [int(item.get("run_denominator", run_count)) for item in entries] or [run_count]
        )
        population_denominator = eligible_denominator + quarantined_run_count
        reasons: list[str] = []
        if entries and len(unsupported) == len(entries):
            status = "unsupported"
            covered = 0
            reasons.append("instrumentation_capability_not_declared")
        elif not entries or not observed or min(observed) == 0:
            status = "withheld"
            covered = min(observed) if observed else 0
            reasons.append("required_metric_evidence_unavailable")
        elif population_denominator > 0 and min(observed) == population_denominator:
            status = "available"
            covered = population_denominator
        else:
            status = "partial"
            covered = min(observed)
            reasons.append("incomplete_metric_coverage")
            if quarantined_run_count:
                reasons.append("one_or_more_source_runs_quarantined")
        statuses[metric] = {
            "status": status,
            "covered_run_count": covered,
            "eligible_run_count": eligible_denominator,
            "population_denominator": population_denominator,
            "run_denominator": population_denominator,
            "withheld_reasons": reasons,
        }

    design = manifest["evaluation_design"]
    if design in {"ab_test", "before_after", "scenario_suite", "staged_rollout"}:
        consistency = telemetry_validation.get("configuration_consistency", {})
        passed = consistency.get("passed") is True and quarantined_run_count == 0
        statuses["comparator"] = {
            "status": "available" if passed else "withheld",
            "covered_run_count": run_count if passed else 0,
            "eligible_run_count": run_count,
            "population_denominator": run_count + quarantined_run_count,
            "run_denominator": run_count + quarantined_run_count,
            "withheld_reasons": (
                []
                if passed
                else [
                    (
                        "one_or_more_source_runs_quarantined"
                        if quarantined_run_count
                        else "configuration_comparison_gate_failed"
                    )
                ]
            ),
        }
    assumptions = manifest.get("assumptions", {})
    if assumptions:
        precondition_metrics = ["duration", "observed_cost", "human_effort"]
        success_metric = (
            "business_success"
            if "business_success" in statuses
            else (
                "quality"
                if "quality" in statuses
                and manifest.get("quality_gate", {}).get("approved") is True
                else None
            )
        )
        precondition_statuses = [
            statuses[name]["status"] for name in precondition_metrics if name in statuses
        ]
        missing_preconditions = [name for name in precondition_metrics if name not in statuses]
        if success_metric is None:
            missing_preconditions.append("business_success_or_approved_quality")
        else:
            precondition_statuses.append(statuses[success_metric]["status"])
        economic_available = (
            not missing_preconditions
            and precondition_statuses
            and all(status == "available" for status in precondition_statuses)
            and quarantined_run_count == 0
        )
        covered_counts = [
            statuses[name]["covered_run_count"]
            for name in [*precondition_metrics, success_metric]
            if name is not None and name in statuses
        ]
        economic_reasons: list[str] = []
        if quarantined_run_count:
            economic_reasons.append("one_or_more_source_runs_quarantined")
        if missing_preconditions:
            economic_reasons.append("economic_preconditions_not_fully_declared")
        if any(status != "available" for status in precondition_statuses):
            economic_reasons.append("economic_precondition_coverage_incomplete")
        statuses["economic_estimates"] = {
            "status": "available" if economic_available else "withheld",
            "covered_run_count": min(covered_counts) if economic_available else 0,
            "eligible_run_count": run_count,
            "population_denominator": run_count + quarantined_run_count,
            "run_denominator": run_count + quarantined_run_count,
            "withheld_reasons": [] if economic_available else economic_reasons,
        }
    return dict(sorted(statuses.items()))
