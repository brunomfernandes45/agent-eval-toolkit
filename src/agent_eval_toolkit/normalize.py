from __future__ import annotations

from typing import Any

from .exceptions import DataValidationError
from .io_utils import RawJsonlRow, unpack_jsonl_row
from .models import EvidenceRef, NormalizedEvent
from .traceability import hash_payload, hash_raw_line
from .validation import (
    coerce_optional_bool,
    coerce_optional_float,
    coerce_optional_int,
    coerce_optional_quality_score,
    coerce_optional_string,
    normalize_timestamp,
    validate_workflow_config,
)

REDACTED_VALUE = "[REDACTED]"


def _first_present(payload: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _optional_string(payload: dict[str, Any], field_name: str | None, event_id: str) -> str | None:
    if not field_name:
        return None
    value = payload.get(field_name)
    if value in (None, ""):
        return None
    return coerce_optional_string(value, field_name, event_id)


def _capture_fields(
    payload: dict[str, Any], field_names: list[str], redacted_fields: set[str]
) -> tuple[dict[str, Any], list[str]]:
    captured: dict[str, Any] = {}
    redacted: list[str] = []
    for key in field_names:
        if key in redacted_fields and payload.get(key) not in (None, ""):
            captured[key] = REDACTED_VALUE
            redacted.append(key)
        else:
            captured[key] = payload.get(key)
    return captured, sorted(redacted)


def normalize_logs(
    raw_rows: list[RawJsonlRow],
    workflow_config: dict[str, Any],
    source_file: str,
) -> list[NormalizedEvent]:
    validate_workflow_config(workflow_config)
    workflow_family = workflow_config["workflow_family"]
    workflow_definition_id = workflow_config["workflow_definition_id"]
    source_schema = workflow_config["source_schema"]
    event_type_map = workflow_config["event_type_map"]
    capture_fields = workflow_config.get("capture_raw_fields", [])
    evidence_mode = workflow_config.get("evidence_mode", "anchor_hash_redacted_snapshot")
    metadata_fields = workflow_config.get(
        "allowed_metadata_fields", workflow_config.get("metadata_fields", [])
    )
    redacted_fields = set(workflow_config.get("redact_raw_fields", []))
    run_id_keys = workflow_config["run_id_keys"]
    trace_id_keys = workflow_config.get("trace_id_keys", [])
    step_name_keys = workflow_config.get("step_name_keys", [])
    cost_field = workflow_config.get("cost_field")
    input_tokens_field = workflow_config.get("input_tokens_field")
    output_tokens_field = workflow_config.get("output_tokens_field")
    status_field = workflow_config.get("status_field")
    attempt_field = workflow_config.get("attempt_field")
    actor_field = workflow_config.get("actor_field")
    intervention_flag_field = workflow_config.get("intervention_flag_field")
    agent_configuration_id_field = workflow_config.get("agent_configuration_id_field")
    agent_version_field = workflow_config.get("agent_version_field")
    configuration_hash_field = workflow_config.get("configuration_hash_field")
    model_field = workflow_config.get("model_field")
    prompt_version_field = workflow_config.get("prompt_version_field")
    tool_name_field = workflow_config.get("tool_name_field")
    tool_call_id_field = workflow_config.get("tool_call_id_field")
    routing_decision_field = workflow_config.get("routing_decision_field")
    handoff_target_field = workflow_config.get("handoff_target_field")
    failure_mode_field = workflow_config.get("failure_mode_field")
    quality_score_field = workflow_config.get("quality_score_field")
    business_success_field = workflow_config.get("business_success_field")
    human_effort_seconds_field = workflow_config.get("human_effort_seconds_field")

    normalized: list[NormalizedEvent] = []

    for row in raw_rows:
        line_number, payload, raw_line = unpack_jsonl_row(row)
        if not isinstance(payload, dict):
            raise DataValidationError(
                f"Raw row {line_number} from {source_file} must be a JSON object"
            )

        missing_fields: list[str] = []

        raw_timestamp = payload.get(source_schema["timestamp_field"])
        timestamp = None
        if raw_timestamp in (None, ""):
            missing_fields.append(source_schema["timestamp_field"])
        else:
            timestamp = normalize_timestamp(raw_timestamp, source_schema["timestamp_field"])

        raw_type = payload.get(source_schema["event_type_field"])
        normalized_raw_type = coerce_optional_string(
            raw_type,
            source_schema["event_type_field"],
            str(payload.get(source_schema["event_id_field"], f"line-{line_number}")),
        )
        event_type = event_type_map.get(normalized_raw_type, "unknown")
        if raw_type is None:
            missing_fields.append(source_schema["event_type_field"])
        elif event_type == "unknown":
            missing_fields.append("unknown_event_type_mapping")

        raw_event_id = payload.get(source_schema["event_id_field"])
        event_id = (
            f"line-{line_number}"
            if raw_event_id in (None, "")
            else coerce_optional_string(
                raw_event_id, source_schema["event_id_field"], f"line-{line_number}"
            )
        )
        if event_id is None:  # Defensive: the fallback above makes this unreachable.
            event_id = f"line-{line_number}"
        run_id = _first_present(payload, run_id_keys)
        trace_id = _first_present(payload, trace_id_keys)
        step_name = _first_present(payload, step_name_keys)
        status = payload.get(status_field) if status_field else None
        actor_type = payload.get(actor_field) if actor_field else None
        attempt_index = payload.get(attempt_field) if attempt_field else None
        observed_cost_usd = payload.get(cost_field) if cost_field else None
        input_tokens = payload.get(input_tokens_field) if input_tokens_field else None
        output_tokens = payload.get(output_tokens_field) if output_tokens_field else None
        quality_score = payload.get(quality_score_field) if quality_score_field else None
        business_success = payload.get(business_success_field) if business_success_field else None
        human_effort_seconds = (
            payload.get(human_effort_seconds_field) if human_effort_seconds_field else None
        )
        human_intervention_value = (
            payload.get(intervention_flag_field) if intervention_flag_field else None
        )
        human_intervention = (
            coerce_optional_bool(
                human_intervention_value,
                intervention_flag_field or "human_intervention",
                event_id,
            )
            or False
        )

        if run_id is None:
            missing_fields.append("run_id")
        if step_name is None and event_type in {
            "step_started",
            "step_completed",
            "retry",
            "intervention",
        }:
            missing_fields.append("step_name")

        captured_fields: dict[str, Any]
        redacted_capture_fields: list[str]
        metadata: dict[str, Any]
        redacted_metadata_fields: list[str]
        if evidence_mode == "anchor_hash_only":
            captured_fields, redacted_capture_fields = {}, []
            metadata, redacted_metadata_fields = {}, []
        else:
            captured_fields, redacted_capture_fields = _capture_fields(
                payload, capture_fields, redacted_fields
            )
            metadata, redacted_metadata_fields = _capture_fields(
                payload, metadata_fields, redacted_fields
            )

        evidence = EvidenceRef(
            source_file=source_file,
            source_line=line_number,
            raw_event_id=(
                payload.get(source_schema["event_id_field"])
                if isinstance(payload.get(source_schema["event_id_field"]), str)
                else None
            ),
            raw_event_hash=hash_payload(payload),
            captured_fields=captured_fields,
            raw_line_hash=hash_raw_line(raw_line) if raw_line is not None else None,
            redacted_fields=sorted(set(redacted_capture_fields + redacted_metadata_fields)),
        )

        normalized.append(
            NormalizedEvent(
                event_id=event_id,
                workflow_family=workflow_family,
                workflow_definition_id=workflow_definition_id,
                timestamp=timestamp,
                run_id=coerce_optional_string(
                    run_id, run_id_keys[0] if run_id_keys else "run_id", event_id
                ),
                trace_id=coerce_optional_string(
                    trace_id, trace_id_keys[0] if trace_id_keys else "trace_id", event_id
                ),
                event_type=event_type,
                status=coerce_optional_string(status, status_field or "status", event_id),
                step_name=coerce_optional_string(
                    step_name, step_name_keys[0] if step_name_keys else "step_name", event_id
                ),
                actor_type=coerce_optional_string(
                    actor_type, actor_field or "actor_type", event_id
                ),
                attempt_index=coerce_optional_int(
                    attempt_index, attempt_field or "attempt_index", event_id
                ),
                human_intervention=human_intervention,
                observed_cost_usd=coerce_optional_float(
                    observed_cost_usd, cost_field or "observed_cost_usd", event_id
                ),
                input_tokens=coerce_optional_int(
                    input_tokens, input_tokens_field or "input_tokens", event_id
                ),
                output_tokens=coerce_optional_int(
                    output_tokens, output_tokens_field or "output_tokens", event_id
                ),
                agent_configuration_id=_optional_string(
                    payload, agent_configuration_id_field, event_id
                ),
                agent_version=_optional_string(payload, agent_version_field, event_id),
                configuration_hash=_optional_string(payload, configuration_hash_field, event_id),
                model_name=_optional_string(payload, model_field, event_id),
                prompt_version=_optional_string(payload, prompt_version_field, event_id),
                tool_name=_optional_string(payload, tool_name_field, event_id),
                tool_call_id=_optional_string(payload, tool_call_id_field, event_id),
                routing_decision=_optional_string(payload, routing_decision_field, event_id),
                handoff_target=_optional_string(payload, handoff_target_field, event_id),
                failure_mode=_optional_string(payload, failure_mode_field, event_id),
                quality_score=coerce_optional_quality_score(
                    quality_score, quality_score_field or "quality_score", event_id
                ),
                business_success=coerce_optional_bool(
                    business_success,
                    business_success_field or "business_success",
                    event_id,
                ),
                human_effort_seconds=coerce_optional_float(
                    human_effort_seconds,
                    human_effort_seconds_field or "human_effort_seconds",
                    event_id,
                ),
                metadata=metadata,
                evidence=evidence,
                missing_fields=sorted(set(missing_fields)),
            )
        )

    normalized.sort(
        key=lambda event: (
            event.run_id or "zzzz-missing-run-id",
            event.timestamp or "",
            event.event_id,
        )
    )
    return normalized
