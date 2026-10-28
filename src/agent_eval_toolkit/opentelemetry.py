from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .diagnostics import diagnostic_document
from .exceptions import ConfigurationError, DataValidationError
from .io_utils import read_json
from .mapping import (
    build_mapping_assessment_payload,
    load_mapping_activity,
    render_mapping_issue_register,
    render_mapping_readiness_report,
)
from .validation import validate_evaluation_manifest, validate_workflow_config

ARTIFACT_SCHEMA_VERSION = "1.0.0"
GENERATED_ARTIFACTS = (
    "canonical_events.jsonl",
    "workflow_config.json",
    "source_profile.json",
    "mapping_spec.json",
    "mapping_assessment.json",
    "mapping_friction_summary.json",
    "mapping_issue_register.csv",
    "mapping_readiness_report.md",
    "redaction_report.json",
    "input_fingerprints.json",
    "diagnostics.json",
)

_CANONICAL_FIELDS = {
    "event_id",
    "workflow_run_id",
    "status",
    "step_name",
    "agent_config_id",
    "agent_version",
    "config_hash",
    "attempt",
    "actor_type",
    "human_intervention",
    "observed_cost_usd",
    "input_tokens",
    "output_tokens",
    "model_name",
    "prompt_version",
    "tool_name",
    "tool_call_id",
    "routing_decision",
    "handoff_target",
    "failure_mode",
    "quality_score",
    "business_success",
    "human_effort_seconds",
}

_NORMALIZED_EVENT_TYPES = {
    "run_started",
    "step_started",
    "step_completed",
    "tool_call",
    "routing_decision",
    "handoff",
    "retry",
    "intervention",
    "quality_check",
    "run_completed",
    "run_failed",
}

_CAPABILITIES = {
    "human_intervention": "intervention_markers",
    "observed_cost_usd": "observed_cost_usd",
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "tool_name": "tool_call_trace",
    "routing_decision": "routing_decisions",
    "handoff_target": "handoff_targets",
    "failure_mode": "failure_modes",
    "quality_score": "quality_score",
    "business_success": "business_success",
    "human_effort_seconds": "human_effort_seconds",
}

_SIGNAL_NAMES = {
    "workflow_run_id": "run_id",
    "agent_config_id": "agent_configuration_id",
    "config_hash": "configuration_hash",
}


def prepare_opentelemetry(
    input_path: str | Path,
    mapping_path: str | Path,
    evaluation_manifest_path: str | Path,
    output_dir: str | Path,
    *,
    activity_log_path: str | Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Prepare an offline OTLP/JSON log export for the canonical evaluation pipeline."""

    source_path = Path(input_path)
    mapping = _validate_mapping(read_json(mapping_path))
    manifest = validate_evaluation_manifest(read_json(evaluation_manifest_path))
    records = _load_log_records(source_path)
    if not records:
        raise DataValidationError("The OTLP/JSON export contains no LogRecords")

    events = [_canonical_event(record, mapping, index) for index, record in enumerate(records, 1)]
    _validate_unique_event_ids(events)
    profile = _source_profile(records, mapping)
    mapping_spec = _mapping_spec(mapping)
    activity_rows = load_mapping_activity(activity_log_path)
    assessment, friction, diagnostics = build_mapping_assessment_payload(
        profile,
        mapping_spec,
        manifest,
        activity_rows=activity_rows,
    )
    workflow_config = _workflow_config(mapping)
    validate_workflow_config(workflow_config)

    redaction_report = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "source_format": "otlp_json_logs",
        "policy": "omit_log_body_and_unmapped_attributes",
        "log_record_count": len(records),
        "log_bodies_present": sum(record["body_present"] for record in records),
        "log_bodies_retained": 0,
        "unmapped_attributes_retained": 0,
        "mapped_attribute_names": sorted(set(mapping["attribute_map"].values())),
    }
    fingerprints = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "hash_algorithm": "sha256",
        "inputs": [
            _fingerprint(source_path, "otlp_json_export"),
            _fingerprint(Path(mapping_path), "opentelemetry_mapping"),
            _fingerprint(Path(evaluation_manifest_path), "evaluation_manifest"),
        ],
    }
    diagnostics_document = diagnostic_document(
        diagnostics,
        stage="opentelemetry_preparation",
        passed=assessment["mapping_readiness"] == "verified",
    )
    artifacts = {
        "canonical_events.jsonl": _jsonl_text(events),
        "workflow_config.json": _json_text(workflow_config),
        "source_profile.json": _json_text(profile),
        "mapping_spec.json": _json_text(mapping_spec),
        "mapping_assessment.json": _json_text(assessment),
        "mapping_friction_summary.json": _json_text(friction),
        "mapping_issue_register.csv": render_mapping_issue_register(mapping_spec, diagnostics),
        "mapping_readiness_report.md": render_mapping_readiness_report(
            assessment, friction, diagnostics
        ),
        "redaction_report.json": _json_text(redaction_report),
        "input_fingerprints.json": _json_text(fingerprints),
        "diagnostics.json": _json_text(diagnostics_document),
    }
    _publish(Path(output_dir), artifacts, overwrite=overwrite)
    return {
        "source_format": "otlp_json_logs",
        "log_record_count": len(records),
        "canonical_event_count": len(events),
        "mapping_readiness": assessment["mapping_readiness"],
        "artifacts": list(GENERATED_ARTIFACTS),
    }


def _validate_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError("The OpenTelemetry mapping must be a JSON object")
    required_strings = (
        "workflow_family",
        "workflow_definition_id",
        "logical_source_id",
        "mapping_id",
        "mapping_version",
    )
    for field in required_strings:
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ConfigurationError(f"`{field}` must be a non-empty string")

    attribute_map = value.get("attribute_map")
    if not isinstance(attribute_map, dict):
        raise ConfigurationError("`attribute_map` must be an object")
    unknown_fields = sorted(set(attribute_map) - _CANONICAL_FIELDS)
    if unknown_fields:
        raise ConfigurationError(
            f"Unsupported canonical fields in `attribute_map`: {unknown_fields}"
        )
    for field, attribute in attribute_map.items():
        if not isinstance(attribute, str) or not attribute.strip():
            raise ConfigurationError(f"`attribute_map.{field}` must be a non-empty string")

    event_name_map = value.get("event_name_map")
    if not isinstance(event_name_map, dict) or not event_name_map:
        raise ConfigurationError("`event_name_map` must be a non-empty object")
    for source_name, event_type in event_name_map.items():
        if not isinstance(source_name, str) or not source_name:
            raise ConfigurationError("OpenTelemetry event names must be non-empty strings")
        if event_type not in _NORMALIZED_EVENT_TYPES:
            raise ConfigurationError(f"Unsupported normalized event type `{event_type}`")
    for boundary in ("run_started", "run_completed", "run_failed"):
        if boundary not in event_name_map.values():
            raise ConfigurationError(f"`event_name_map` must define `{boundary}`")

    status_map = value.get("status_map")
    if not isinstance(status_map, dict) or not status_map:
        raise ConfigurationError("`status_map` must be a non-empty object")
    normalized_status_map: dict[str, str] = {}
    for status, source_values in status_map.items():
        if status not in {"in_progress", "completed", "failed"}:
            raise ConfigurationError(f"Unsupported normalized status `{status}`")
        if not isinstance(source_values, list) or not source_values:
            raise ConfigurationError(f"`status_map.{status}` must be a non-empty list")
        for source_value in source_values:
            if not isinstance(source_value, str) or not source_value:
                raise ConfigurationError(f"`status_map.{status}` contains an invalid value")
            key = source_value.strip().lower()
            if key in normalized_status_map:
                raise ConfigurationError(f"Duplicate OpenTelemetry status mapping `{key}`")
            normalized_status_map[key] = status

    approvals = {
        field: _approval(value.get(field), field)
        for field in (
            "mapping_approval",
            "workflow_boundary_approval",
            "business_status_approval",
            "privacy_approval",
        )
    }
    trace_fallback = value.get("trace_as_workflow_run_approved", False)
    if not isinstance(trace_fallback, bool):
        raise ConfigurationError("`trace_as_workflow_run_approved` must be boolean")
    if "workflow_run_id" not in attribute_map and not trace_fallback:
        raise ConfigurationError(
            "Map `workflow_run_id` or explicitly approve traceId as the workflow-run key"
        )
    return {
        **{field: value[field] for field in required_strings},
        "attribute_map": dict(sorted(attribute_map.items())),
        "event_name_map": dict(sorted(event_name_map.items())),
        "status_map": normalized_status_map,
        "trace_as_workflow_run_approved": trace_fallback,
        **approvals,
    }


def _approval(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"`{field}` must be an object")
    status = value.get("status")
    if status not in {"approved", "provisional", "unresolved"}:
        raise ConfigurationError(f"`{field}.status` is unsupported")
    reviewer_role = value.get("reviewer_role")
    evidence_ref = value.get("evidence_ref")
    if status == "approved" and not all(
        isinstance(item, str) and item.strip() for item in (reviewer_role, evidence_ref)
    ):
        raise ConfigurationError(f"`{field}` requires reviewer_role and evidence_ref when approved")
    return {
        "status": status,
        "reviewer_role": reviewer_role,
        "evidence_ref": evidence_ref,
    }


def _load_log_records(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("resourceLogs"), list):
        raise DataValidationError("Expected an OTLP/JSON ExportLogsServiceRequest")
    records: list[dict[str, Any]] = []
    for resource_logs in payload["resourceLogs"]:
        if not isinstance(resource_logs, dict):
            raise DataValidationError("Each `resourceLogs` entry must be an object")
        resource_attributes = _attributes((resource_logs.get("resource") or {}).get("attributes"))
        scope_logs = resource_logs.get("scopeLogs", [])
        if not isinstance(scope_logs, list):
            raise DataValidationError("`scopeLogs` must be a list")
        for scope_entry in scope_logs:
            if not isinstance(scope_entry, dict):
                raise DataValidationError("Each `scopeLogs` entry must be an object")
            scope = scope_entry.get("scope") or {}
            for log_record in scope_entry.get("logRecords", []):
                if not isinstance(log_record, dict):
                    raise DataValidationError("Each OTLP LogRecord must be an object")
                records.append(
                    {
                        "record": log_record,
                        "attributes": _attributes(log_record.get("attributes")),
                        "resource": resource_attributes,
                        "scope_name": scope.get("name") if isinstance(scope, dict) else None,
                        "body_present": "body" in log_record,
                    }
                )
    return records


def _attributes(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, list):
        raise DataValidationError("OTLP attributes must be a list of key/value objects")
    result: dict[str, Any] = {}
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            raise DataValidationError("OTLP attribute entries require a string key")
        key = item["key"]
        if key in result:
            raise DataValidationError(f"Duplicate OTLP attribute `{key}`")
        result[key] = _any_value(item.get("value"))
    return result


def _any_value(value: Any) -> Any:
    if not isinstance(value, dict) or len(value) != 1:
        raise DataValidationError("OTLP AnyValue must contain exactly one value field")
    kind, payload = next(iter(value.items()))
    if kind in {"stringValue", "boolValue", "intValue", "doubleValue", "bytesValue"}:
        if kind == "intValue":
            try:
                return int(payload)
            except (TypeError, ValueError) as exc:
                raise DataValidationError("Invalid OTLP integer attribute") from exc
        return payload
    if kind == "arrayValue":
        values = payload.get("values", []) if isinstance(payload, dict) else None
        if not isinstance(values, list):
            raise DataValidationError("Invalid OTLP array attribute")
        return [_any_value(item) for item in values]
    if kind == "kvlistValue":
        values = payload.get("values", []) if isinstance(payload, dict) else None
        return _attributes(values)
    raise DataValidationError(f"Unsupported OTLP AnyValue kind `{kind}`")


def _canonical_event(record: dict[str, Any], mapping: dict[str, Any], index: int) -> dict[str, Any]:
    raw = record["record"]
    attributes = record["attributes"]
    raw_event_name = raw.get("eventName")
    if not isinstance(raw_event_name, str) or not raw_event_name:
        raise DataValidationError(f"OTLP LogRecord {index} has no `eventName`")
    event_type = mapping["event_name_map"].get(raw_event_name)
    if event_type is None:
        raise DataValidationError(f"OTLP event `{raw_event_name}` has no approved mapping")
    timestamp = _timestamp(raw.get("timeUnixNano") or raw.get("observedTimeUnixNano"), index)
    trace_id = raw.get("traceId")
    if trace_id is not None and (not isinstance(trace_id, str) or not trace_id):
        raise DataValidationError(f"OTLP LogRecord {index} has an invalid traceId")

    mapped = {
        canonical: attributes.get(attribute)
        for canonical, attribute in mapping["attribute_map"].items()
    }
    run_id = mapped.get("workflow_run_id")
    if run_id in (None, "") and mapping["trace_as_workflow_run_approved"]:
        run_id = trace_id
    if run_id in (None, ""):
        raise DataValidationError(f"OTLP LogRecord {index} has no approved workflow-run key")
    raw_status = mapped.get("status")
    if not isinstance(raw_status, str):
        raise DataValidationError(f"OTLP LogRecord {index} has no mapped business status")
    status = mapping["status_map"].get(raw_status.strip().lower())
    if status is None:
        raise DataValidationError(
            f"OTLP business status `{raw_status}` has no approved normalized value"
        )
    event_id = mapped.get("event_id")
    if event_id in (None, ""):
        stable = f"{trace_id or 'no-trace'}:{raw.get('spanId') or 'no-span'}:{timestamp}:{index}"
        event_id = f"otel:{hashlib.sha256(stable.encode('utf-8')).hexdigest()[:24]}"

    event: dict[str, Any] = {
        "event_id": str(event_id),
        "timestamp": timestamp,
        "event_name": event_type,
        "workflow_run_id": str(run_id),
        "trace_id": trace_id,
        "workflow_definition_id": mapping["workflow_definition_id"],
        "status": status,
        "step_name": mapped.get("step_name") or raw_event_name,
        "otel_span_id": raw.get("spanId"),
        "otel_scope_name": record["scope_name"],
        "source_record_hash": _safe_record_hash(raw),
    }
    for field in _CANONICAL_FIELDS - {"event_id", "workflow_run_id", "status", "step_name"}:
        value = mapped.get(field)
        if value is not None:
            event[field] = value
    return {key: value for key, value in event.items() if value is not None}


def _timestamp(value: Any, index: int) -> str:
    try:
        nanoseconds = int(value)
    except (TypeError, ValueError) as exc:
        raise DataValidationError(f"OTLP LogRecord {index} has no valid timestamp") from exc
    if nanoseconds < 0:
        raise DataValidationError(f"OTLP LogRecord {index} has a negative timestamp")
    seconds, nanos = divmod(nanoseconds, 1_000_000_000)
    parsed = datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=nanos // 1000)
    return parsed.isoformat().replace("+00:00", "Z")


def _safe_record_hash(record: dict[str, Any]) -> str:
    safe_record = {key: value for key, value in record.items() if key != "body"}
    return hashlib.sha256(_canonical_bytes(safe_record)).hexdigest()


def _validate_unique_event_ids(events: list[dict[str, Any]]) -> None:
    counts = Counter(str(event["event_id"]) for event in events)
    duplicates = sorted(event_id for event_id, count in counts.items() if count > 1)
    if duplicates:
        raise DataValidationError(f"Duplicate canonical event ids: {duplicates}")


def _source_profile(records: list[dict[str, Any]], mapping: dict[str, Any]) -> dict[str, Any]:
    inventory: dict[str, list[Any]] = {
        "eventName": [record["record"].get("eventName") for record in records],
        "timeUnixNano": [record["record"].get("timeUnixNano") for record in records],
        "observedTimeUnixNano": [
            record["record"].get("observedTimeUnixNano") for record in records
        ],
        "traceId": [record["record"].get("traceId") for record in records],
        "spanId": [record["record"].get("spanId") for record in records],
    }
    attribute_names = sorted({name for record in records for name in record["attributes"]})
    for name in attribute_names:
        inventory[f"attributes.{name}"] = [record["attributes"].get(name) for record in records]
    fields = []
    for path, values in inventory.items():
        observed = [value for value in values if value is not None]
        fields.append(
            {
                "path": path,
                "observed_count": len(observed),
                "record_denominator": len(records),
                "coverage": round(len(observed) / len(records), 3),
                "value_types": sorted({_json_type(value) for value in observed}),
            }
        )
    return {
        "source_profile_schema_version": ARTIFACT_SCHEMA_VERSION,
        "logical_source_id": mapping["logical_source_id"],
        "source_kind": "opentelemetry_otlp_json_logs",
        "file_count": 1,
        "record_count": len(records),
        "formats": ["otlp_json"],
        "field_inventory": fields,
        "manual_review_record_count": 0,
    }


def _mapping_spec(mapping: dict[str, Any]) -> dict[str, Any]:
    approval = mapping["mapping_approval"]
    semantic_status = "confirmed" if approval["status"] == "approved" else "provisional"
    rows = [
        _mapping_row(
            "timestamp",
            ["timeUnixNano", "observedTimeUnixNano"],
            "coercion",
            semantic_status,
            approval,
        ),
        _mapping_row("event_type", ["eventName"], "lookup", semantic_status, approval),
        _mapping_row("trace_id", ["traceId"], "direct", semantic_status, approval),
        _mapping_row(
            "workflow_definition_id",
            ["mapping.workflow_definition_id"],
            "lookup",
            semantic_status,
            approval,
        ),
    ]
    for field, attribute in mapping["attribute_map"].items():
        signal = _SIGNAL_NAMES.get(field, field)
        transformation = "lookup" if field == "status" else "direct"
        rows.append(
            _mapping_row(
                signal,
                [f"attributes.{attribute}"],
                transformation,
                semantic_status,
                approval,
            )
        )
    if "workflow_run_id" not in mapping["attribute_map"]:
        rows.append(_mapping_row("run_id", ["traceId"], "derivation", semantic_status, approval))
    return {
        "mapping_spec_schema_version": ARTIFACT_SCHEMA_VERSION,
        "mapping_id": mapping["mapping_id"],
        "mapping_version": mapping["mapping_version"],
        "logical_source_id": mapping["logical_source_id"],
        "mappings": rows,
        "workflow_boundary_approval": mapping["workflow_boundary_approval"],
        "business_status_approval": mapping["business_status_approval"],
        "privacy_approval": mapping["privacy_approval"],
        "issues": [],
    }


def _mapping_row(
    signal: str,
    paths: list[str],
    transformation: str,
    semantic_status: str,
    approval: dict[str, Any],
) -> dict[str, Any]:
    return {
        "canonical_signal": signal,
        "source_paths": paths,
        "transformation": transformation,
        "semantic_status": semantic_status,
        "transformation_tested": True,
        "approval": approval,
    }


def _workflow_config(mapping: dict[str, Any]) -> dict[str, Any]:
    mapped_fields = set(mapping["attribute_map"])
    capabilities = {_CAPABILITIES[field] for field in mapped_fields if field in _CAPABILITIES}
    mapped_event_types = set(mapping["event_name_map"].values())
    if "retry" in mapped_event_types:
        capabilities.add("retry_markers")
    if "intervention" in mapped_event_types:
        capabilities.add("intervention_markers")
    if "tool_call" in mapped_event_types:
        capabilities.add("tool_call_trace")
    if "handoff" in mapped_event_types:
        capabilities.add("handoff_targets")
    if "routing_decision" in mapped_event_types:
        capabilities.add("routing_decisions")

    config: dict[str, Any] = {
        "workflow_family": mapping["workflow_family"],
        "workflow_definition_id": mapping["workflow_definition_id"],
        "source_schema": {
            "event_id_field": "event_id",
            "timestamp_field": "timestamp",
            "event_type_field": "event_name",
        },
        "event_type_map": {
            event_type: event_type for event_type in sorted(_NORMALIZED_EVENT_TYPES)
        },
        "run_boundaries": {
            "start_event_type": "run_started",
            "completed_event_type": "run_completed",
            "failed_event_type": "run_failed",
        },
        "telemetry_capabilities": sorted(capabilities),
        "run_id_keys": ["workflow_run_id"],
        "trace_id_keys": ["trace_id"],
        "step_name_keys": ["step_name"],
        "status_field": "status",
        "attempt_field": "attempt",
        "actor_field": "actor_type",
        "intervention_flag_field": "human_intervention",
        "cost_field": "observed_cost_usd",
        "input_tokens_field": "input_tokens",
        "output_tokens_field": "output_tokens",
        "aggregation_semantics": {
            "observed_cost_usd": "per_event_increment",
            "input_tokens": "per_event_increment",
            "output_tokens": "per_event_increment",
        },
        "agent_configuration_id_field": "agent_config_id",
        "agent_version_field": "agent_version",
        "configuration_hash_field": "config_hash",
        "model_field": "model_name",
        "prompt_version_field": "prompt_version",
        "tool_name_field": "tool_name",
        "tool_call_id_field": "tool_call_id",
        "routing_decision_field": "routing_decision",
        "handoff_target_field": "handoff_target",
        "failure_mode_field": "failure_mode",
        "quality_score_field": "quality_score",
        "business_success_field": "business_success",
        "human_effort_seconds_field": "human_effort_seconds",
        "capture_raw_fields": sorted(
            {
                "event_name",
                "workflow_run_id",
                "trace_id",
                "step_name",
                "status",
                *mapped_fields,
            }
        ),
        "metadata_fields": [],
        "allowed_metadata_fields": [],
        "restricted_fields": [],
        "redact_raw_fields": [],
        "evidence_mode": "anchor_hash_redacted_snapshot",
    }
    return config


def _fingerprint(path: Path, logical_name: str) -> dict[str, Any]:
    return {
        "logical_name": logical_name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
    }


def _publish(output: Path, artifacts: dict[str, str], *, overwrite: bool) -> None:
    if output.exists() and any(output.iterdir()) and not overwrite:
        raise ConfigurationError(f"Output directory `{output}` is not empty; use --overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".otel-stage-", dir=str(output.parent)))
    try:
        for name, content in artifacts.items():
            (stage / name).write_text(content, encoding="utf-8")
        if output.exists():
            shutil.rmtree(output)
        stage.replace(output)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "null"


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _jsonl_text(values: Iterable[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        for value in values
    )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
