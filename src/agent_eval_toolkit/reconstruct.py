from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from statistics import mean
from typing import Any

from .exceptions import DataValidationError
from .models import NormalizedEvent, WorkflowRun

DEFAULT_RUN_BOUNDARIES = {
    "start_event_type": "run_started",
    "completed_event_type": "run_completed",
    "failed_event_type": "run_failed",
}

COUNT_CAPABILITIES = {
    "retry_markers": "retry_count",
    "intervention_markers": "intervention_count",
    "tool_call_trace": "tool_call_count",
    "handoff_targets": "handoff_count",
    "routing_decisions": "routing_decision_count",
}

VALUE_CAPABILITIES = {
    "business_success",
    "human_effort_seconds",
    "observed_cost_usd",
    "input_tokens",
    "output_tokens",
    "quality_score",
}

LEGACY_VALUE_CAPABILITIES = VALUE_CAPABILITIES - {"business_success", "human_effort_seconds"}
AGGREGATED_SIGNALS = {"observed_cost_usd", "input_tokens", "output_tokens"}


def reconstruct_runs(
    events: list[NormalizedEvent], workflow_config: dict[str, Any] | None = None
) -> list[WorkflowRun]:
    """Reconstruct workflow runs from validated normalized events.

    Passing ``workflow_config`` enables the explicit capability contract: a telemetry
    metric is populated only when its capability is explicitly declared. Omitting
    the config retains the legacy behavior for callers that already normalized
    canonical events themselves.
    """

    if not events:
        return []

    _validate_event_identity(events)
    boundaries = _run_boundaries(workflow_config)
    capabilities = _telemetry_capabilities(workflow_config, events)
    aggregation_semantics = _aggregation_semantics(workflow_config, capabilities)

    grouped: dict[str, list[NormalizedEvent]] = defaultdict(list)
    for event in events:
        if not event.run_id:
            raise DataValidationError(
                f"Run reconstruction failed: event `{event.event_id}` is missing a run ID"
            )
        if event.event_type == "unknown":
            raise DataValidationError(
                f"Run reconstruction failed: event `{event.event_id}` has an unknown event type"
            )
        grouped[event.run_id].append(event)

    runs: list[WorkflowRun] = []
    for run_id, grouped_events in sorted(grouped.items()):
        start_event, terminal_event = _validate_run_boundaries(run_id, grouped_events, boundaries)
        run_events = _ordered_events(grouped_events)

        missing_telemetry: set[str] = set()
        for event in run_events:
            missing_telemetry.update(event.missing_fields)

        observed_costs = [
            event.observed_cost_usd for event in run_events if event.observed_cost_usd is not None
        ]
        input_tokens = [
            event.input_tokens for event in run_events if event.input_tokens is not None
        ]
        output_tokens = [
            event.output_tokens for event in run_events if event.output_tokens is not None
        ]
        quality_scores = [
            event.quality_score for event in run_events if event.quality_score is not None
        ]
        business_success_values = [
            event.business_success for event in run_events if event.business_success is not None
        ]
        human_effort_values = [
            event.human_effort_seconds
            for event in run_events
            if event.human_effort_seconds is not None
        ]

        agent_configuration_ids = sorted(
            {event.agent_configuration_id for event in run_events if event.agent_configuration_id}
        )
        agent_versions = sorted(
            {event.agent_version for event in run_events if event.agent_version}
        )
        configuration_hashes = sorted(
            {event.configuration_hash for event in run_events if event.configuration_hash}
        )
        if any(
            len(values) > 1
            for values in (agent_configuration_ids, agent_versions, configuration_hashes)
        ):
            missing_telemetry.add("configuration_drift")

        tracked_capabilities = set(COUNT_CAPABILITIES) | LEGACY_VALUE_CAPABILITIES
        if workflow_config is not None:
            tracked_capabilities.update(
                capability
                for capability in ("business_success", "human_effort_seconds")
                if capability in capabilities
                or workflow_config.get(f"{capability}_field") is not None
            )
        unsupported_telemetry = sorted(
            capability for capability in tracked_capabilities if capability not in capabilities
        )
        observed_cost = _optional_decimal_sum(
            observed_costs, "observed_cost_usd", capabilities, digits=6
        )
        summed_input_tokens = _optional_sum(input_tokens, "input_tokens", capabilities)
        summed_output_tokens = _optional_sum(output_tokens, "output_tokens", capabilities)
        summed_human_effort = _optional_decimal_sum(
            human_effort_values, "human_effort_seconds", capabilities, digits=3
        )
        avg_quality_score = (
            round(mean(quality_scores), 3)
            if "quality_score" in capabilities and quality_scores
            else None
        )
        business_success: bool | None = None
        if "business_success" in capabilities and business_success_values:
            distinct_business_values = set(business_success_values)
            if len(distinct_business_values) == 1:
                business_success = business_success_values[0]
            else:
                missing_telemetry.add("business_success_conflict")

        for capability, values in (
            ("observed_cost_usd", observed_costs),
            ("input_tokens", input_tokens),
            ("output_tokens", output_tokens),
            ("quality_score", quality_scores),
            ("business_success", business_success_values),
            ("human_effort_seconds", human_effort_values),
        ):
            if capability in capabilities and not values:
                missing_telemetry.add(capability)

        if terminal_event.event_type == boundaries["completed_event_type"]:
            status = "completed"
        else:
            status = "failed"
        runs.append(
            WorkflowRun(
                run_id=run_id,
                workflow_family=start_event.workflow_family,
                workflow_definition_id=start_event.workflow_definition_id,
                started_at=start_event.timestamp,
                ended_at=terminal_event.timestamp,
                status=status,
                completion_state="completed" if status == "completed" else "not_completed",
                duration_seconds=round(
                    (terminal_event.timestamp_dt() - start_event.timestamp_dt()).total_seconds(),
                    3,
                ),
                retry_count=(
                    _count_retries(run_events) if "retry_markers" in capabilities else None
                ),
                intervention_count=(
                    sum(
                        1
                        for event in run_events
                        if event.event_type == "intervention" or event.human_intervention
                    )
                    if "intervention_markers" in capabilities
                    else None
                ),
                observed_cost_usd=observed_cost,
                input_tokens=(
                    int(summed_input_tokens) if summed_input_tokens is not None else None
                ),
                output_tokens=(
                    int(summed_output_tokens) if summed_output_tokens is not None else None
                ),
                agent_configuration_ids=agent_configuration_ids,
                agent_versions=agent_versions,
                configuration_hashes=configuration_hashes,
                model_names=sorted({event.model_name for event in run_events if event.model_name}),
                prompt_versions=sorted(
                    {event.prompt_version for event in run_events if event.prompt_version}
                ),
                tool_call_count=(
                    _count_tool_calls(run_events) if "tool_call_trace" in capabilities else None
                ),
                handoff_count=(
                    sum(
                        1
                        for event in run_events
                        if event.event_type == "handoff" or event.handoff_target
                    )
                    if "handoff_targets" in capabilities
                    else None
                ),
                routing_decision_count=(
                    sum(
                        1
                        for event in run_events
                        if event.event_type == "routing_decision" or event.routing_decision
                    )
                    if "routing_decisions" in capabilities
                    else None
                ),
                failure_modes=sorted(
                    {event.failure_mode for event in run_events if event.failure_mode}
                ),
                avg_quality_score=avg_quality_score,
                step_count=len({event.step_name for event in run_events if event.step_name}),
                trace_ids=sorted({event.trace_id for event in run_events if event.trace_id}),
                event_ids=[event.event_id for event in run_events],
                evidence=[event.evidence for event in run_events if event.evidence],
                missing_telemetry=sorted(missing_telemetry),
                unsupported_telemetry=unsupported_telemetry,
                events=run_events,
                business_success=business_success,
                human_effort_seconds=summed_human_effort,
                aggregation_semantics={
                    signal: semantic
                    for signal, semantic in aggregation_semantics.items()
                    if signal in AGGREGATED_SIGNALS
                },
            )
        )

    return runs


def reconstruct_runs_with_quarantine(
    events: list[NormalizedEvent], workflow_config: dict[str, Any]
) -> tuple[list[WorkflowRun], list[dict[str, Any]]]:
    """Reconstruct trustworthy run groups and quarantine local semantic failures.

    Duplicate event identities and rows without a usable run identity remain
    bundle-wide blockers. Once identity is trustworthy, boundary failures are
    isolated to the affected run so that the evaluator can publish bounded
    readiness and unaffected descriptive evidence.
    """

    if not events:
        return [], []
    _validate_event_identity(events)
    _aggregation_semantics(workflow_config, _telemetry_capabilities(workflow_config, events))
    grouped: dict[str, list[NormalizedEvent]] = defaultdict(list)
    for event in events:
        if not event.run_id:
            raise DataValidationError(
                f"Run reconstruction failed: event `{event.event_id}` is missing a run ID"
            )
        if event.event_type == "unknown":
            raise DataValidationError(
                f"Run reconstruction failed: event `{event.event_id}` has an unknown event type"
            )
        grouped[event.run_id].append(event)

    runs: list[WorkflowRun] = []
    quarantined: list[dict[str, Any]] = []
    for run_id, run_events in sorted(grouped.items()):
        try:
            runs.extend(reconstruct_runs(run_events, workflow_config))
        except DataValidationError as exc:
            quarantined.append(
                {
                    "run_id": run_id,
                    "reason": str(exc),
                    "event_ids": sorted(event.event_id for event in run_events),
                    "trace_ids": sorted(
                        {event.trace_id for event in run_events if event.trace_id is not None}
                    ),
                    "quarantine_stage": "reconstruction",
                }
            )
    return runs, quarantined


def _run_boundaries(workflow_config: dict[str, Any] | None) -> dict[str, str]:
    if workflow_config is None:
        return dict(DEFAULT_RUN_BOUNDARIES)
    configured = workflow_config.get("run_boundaries")
    if not isinstance(configured, dict):
        return dict(DEFAULT_RUN_BOUNDARIES)
    return {
        key: str(configured.get(key, default_value))
        for key, default_value in DEFAULT_RUN_BOUNDARIES.items()
    }


def _telemetry_capabilities(
    workflow_config: dict[str, Any] | None, events: list[NormalizedEvent]
) -> set[str]:
    if workflow_config is None:
        inferred = set(COUNT_CAPABILITIES) | LEGACY_VALUE_CAPABILITIES
        if any(event.business_success is not None for event in events):
            inferred.add("business_success")
        if any(event.human_effort_seconds is not None for event in events):
            inferred.add("human_effort_seconds")
        return inferred
    configured = workflow_config.get("telemetry_capabilities", [])
    if isinstance(configured, dict):
        return {str(name) for name, enabled in configured.items() if enabled is True}
    if isinstance(configured, list):
        return {str(name) for name in configured}
    return set()


def _aggregation_semantics(
    workflow_config: dict[str, Any] | None, capabilities: set[str]
) -> dict[str, str]:
    if workflow_config is None:
        return {
            signal: "per_event_increment" for signal in AGGREGATED_SIGNALS if signal in capabilities
        }
    raw_semantics = workflow_config.get("aggregation_semantics", {})
    if not isinstance(raw_semantics, dict):
        raise DataValidationError("`aggregation_semantics` must be an object")
    semantics: dict[str, str] = {}
    mapped_fields = {
        "observed_cost_usd": workflow_config.get("cost_field"),
        "input_tokens": workflow_config.get("input_tokens_field"),
        "output_tokens": workflow_config.get("output_tokens_field"),
    }
    for signal, field_name in mapped_fields.items():
        if not field_name:
            continue
        semantic = raw_semantics.get(signal)
        if semantic != "per_event_increment":
            raise DataValidationError(
                f"Run reconstruction requires `aggregation_semantics.{signal}` to be "
                "`per_event_increment`"
            )
        semantics[signal] = semantic
    return semantics


def _ordered_events(events: list[NormalizedEvent]) -> list[NormalizedEvent]:
    fallback_positions = {id(event): index for index, event in enumerate(events)}

    def order_key(event: NormalizedEvent) -> tuple[Any, ...]:
        fallback = fallback_positions[id(event)]
        if event.evidence is not None:
            sequence = (0, event.evidence.source_file, event.evidence.source_line, event.event_id)
        else:
            sequence = (1, "", fallback, event.event_id)
        return (event.timestamp_dt(), *sequence)

    return sorted(events, key=order_key)


def _validate_event_identity(events: list[NormalizedEvent]) -> None:
    lines_by_event_id: dict[str, list[int | None]] = defaultdict(list)
    for event in events:
        source_line = event.evidence.source_line if event.evidence else None
        lines_by_event_id[event.event_id].append(source_line)
    duplicates = {
        event_id: lines for event_id, lines in lines_by_event_id.items() if len(lines) > 1
    }
    if duplicates:
        duplicate_ids = ", ".join(f"`{event_id}`" for event_id in sorted(duplicates))
        raise DataValidationError(
            f"Run reconstruction failed: duplicate event IDs detected: {duplicate_ids}"
        )


def _validate_run_boundaries(
    run_id: str,
    events: list[NormalizedEvent],
    boundaries: dict[str, str],
) -> tuple[NormalizedEvent, NormalizedEvent]:
    starts = [event for event in events if event.event_type == boundaries["start_event_type"]]
    completions = [
        event for event in events if event.event_type == boundaries["completed_event_type"]
    ]
    failures = [event for event in events if event.event_type == boundaries["failed_event_type"]]

    if not starts:
        raise DataValidationError(
            f"Run `{run_id}` is missing its `{boundaries['start_event_type']}` boundary"
        )
    if len(starts) > 1:
        raise DataValidationError(
            f"Run `{run_id}` has duplicate `{boundaries['start_event_type']}` boundaries"
        )
    if completions and failures:
        raise DataValidationError(
            f"Run `{run_id}` has conflicting completed and failed terminal boundaries"
        )
    terminal_events = completions or failures
    if not terminal_events:
        raise DataValidationError(f"Run `{run_id}` is missing a terminal boundary")
    if len(terminal_events) > 1:
        raise DataValidationError(f"Run `{run_id}` has duplicate terminal boundaries")

    start_event = starts[0]
    terminal_event = terminal_events[0]
    raw_terminal_status = (terminal_event.status or "").strip().lower()
    if (
        terminal_event.event_type == boundaries["completed_event_type"]
        and raw_terminal_status in {"failed", "abandoned"}
    ) or (
        terminal_event.event_type == boundaries["failed_event_type"]
        and raw_terminal_status == "completed"
    ):
        raise DataValidationError(
            f"Run `{run_id}` has a terminal event/status conflict: "
            f"`{terminal_event.event_type}` with status `{terminal_event.status}`"
        )
    if start_event.timestamp is None or terminal_event.timestamp is None:
        raise DataValidationError(f"Run `{run_id}` has a boundary with a missing timestamp")
    try:
        started_at = start_event.timestamp_dt()
        ended_at = terminal_event.timestamp_dt()
    except ValueError as exc:
        raise DataValidationError(f"Run `{run_id}` has an invalid boundary timestamp") from exc
    if ended_at < started_at:
        raise DataValidationError(f"Run `{run_id}` has out-of-order run boundaries")

    for event in events:
        if event.timestamp is None:
            raise DataValidationError(
                f"Run `{run_id}` event `{event.event_id}` is missing a timestamp"
            )
        try:
            event_timestamp = event.timestamp_dt()
        except ValueError as exc:
            raise DataValidationError(
                f"Run `{run_id}` event `{event.event_id}` has an invalid timestamp"
            ) from exc
        if event_timestamp < started_at:
            raise DataValidationError(
                f"Run `{run_id}` event `{event.event_id}` occurs before the start boundary"
            )
        if event_timestamp > ended_at:
            raise DataValidationError(
                f"Run `{run_id}` event `{event.event_id}` occurs after the terminal boundary"
            )

    ordered_events = _ordered_events(events)
    if ordered_events[0] is not start_event:
        first_event = ordered_events[0]
        if first_event.timestamp_dt() == started_at:
            raise DataValidationError(
                f"Run `{run_id}` start boundary must be first among events with the same timestamp"
            )
        raise DataValidationError(f"Run `{run_id}` start boundary is not the first event")
    if ordered_events[-1] is not terminal_event:
        last_event = ordered_events[-1]
        if last_event.timestamp_dt() == ended_at:
            raise DataValidationError(
                f"Run `{run_id}` terminal boundary must be last among events "
                "with the same timestamp"
            )
        raise DataValidationError(f"Run `{run_id}` terminal boundary is not the last event")

    return start_event, terminal_event


def _count_retries(events: list[NormalizedEvent]) -> int:
    retry_keys: set[tuple[str, str | int]] = set()
    for event in events:
        if event.event_type != "retry":
            continue
        if event.step_name is not None and event.attempt_index is not None:
            retry_keys.add((event.step_name, event.attempt_index))
        else:
            retry_keys.add(("event_id", event.event_id))
    return len(retry_keys)


def _count_tool_calls(events: list[NormalizedEvent]) -> int:
    call_keys: set[str] = set()
    for event in events:
        if not (event.event_type == "tool_call" or event.tool_name or event.tool_call_id):
            continue
        call_key = event.tool_call_id or f"event::{event.event_id}"
        if call_key not in call_keys:
            call_keys.add(call_key)
    return len(call_keys)


def _optional_sum(
    values: list[int] | list[float],
    capability: str,
    capabilities: set[str],
    digits: int | None = None,
) -> int | float | None:
    if capability not in capabilities or not values:
        return None
    value = sum(values)
    return round(value, digits) if digits is not None else value


def _optional_decimal_sum(
    values: list[float],
    capability: str,
    capabilities: set[str],
    digits: int,
) -> float | None:
    if capability not in capabilities or not values:
        return None
    total = sum((Decimal(str(value)) for value in values), start=Decimal("0"))
    quantum = Decimal("1").scaleb(-digits)
    return float(total.quantize(quantum, rounding=ROUND_HALF_UP))
