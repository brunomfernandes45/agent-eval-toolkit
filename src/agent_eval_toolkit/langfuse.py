from __future__ import annotations

import csv
import gzip
import hashlib
import hmac
import io
import json
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .diagnostics import diagnostic_document
from .exceptions import ConfigurationError, DataValidationError
from .mapping import (
    build_mapping_assessment_payload,
    load_mapping_activity,
    render_mapping_issue_register,
    render_mapping_readiness_report,
)

REDACTED_VALUE = "[REDACTED]"
LANGFUSE_ARTIFACT_SCHEMA_VERSION = "2.0.0"
MAX_AUDIT_TRACES = 30

GENERATED_ARTIFACTS = (
    "canonical_events.jsonl",
    "langfuse_source_profile.json",
    "telemetry_coverage.json",
    "redaction_report.json",
    "trace_audit_sample.csv",
    "input_fingerprints.json",
    "evaluation_manifest.monitoring.json",
    "workflow_config.json",
    "source_profile.json",
    "mapping_spec.json",
    "mapping_assessment.json",
    "mapping_friction_summary.json",
    "mapping_issue_register.csv",
    "mapping_readiness_report.md",
    "diagnostics.json",
)

_SUPPORTED_SUFFIXES = (".json", ".jsonl", ".json.gz", ".jsonl.gz")
_REQUIRED_PATHS = (
    "status",
    "agent_configuration_id",
    "agent_version",
    "configuration_hash",
    "retry",
    "intervention",
    "handoff",
    "routing",
    "quality",
)


@dataclass(frozen=True, slots=True)
class _SourceRecord:
    payload: dict[str, Any]
    source_name: str
    source_line: int | None


@dataclass(frozen=True, slots=True)
class _Observation:
    observation_id: str
    trace_id: str
    start_time: str
    start_dt: datetime
    end_time: str | None
    end_dt: datetime | None
    observation_type: str
    name: str | None
    trace_name: str | None
    environment: str | None
    parent_observation_id: str | None
    model_name: str | None
    prompt_version: str | None
    input_tokens: int | None
    output_tokens: int | None
    observed_cost_usd: float | None
    business_status: str | None
    agent_configuration_id: str | None
    agent_version: str | None
    configuration_hash: str | None
    retry_value: Any
    retry_field_present: bool
    intervention_value: Any
    intervention_field_present: bool
    handoff_target: str | None
    handoff_field_present: bool
    routing_decision: str | None
    routing_field_present: bool
    quality_score: float | None
    tool_names: tuple[str, ...]
    tool_call_field_present: bool
    input_evidence: dict[str, Any] | None
    output_evidence: dict[str, Any] | None
    source_name: str
    source_line: int | None
    source_observation_hash: str
    alias_style: str


@dataclass(frozen=True, slots=True)
class _Trace:
    trace_id: str
    observations: tuple[_Observation, ...]
    start_time: str
    start_dt: datetime
    end_time: str | None
    end_dt: datetime | None
    outcome: str | None
    boundary_order_valid: bool
    trace_name: str | None
    environment: str | None
    configuration_ids: tuple[str, ...]
    agent_versions: tuple[str, ...]
    configuration_hashes: tuple[str, ...]


def prepare_langfuse(
    input_path: str | Path,
    mapping_path: str | Path | dict[str, Any],
    output_dir: str | Path,
    *,
    activity_log_path: str | Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Prepare an offline Langfuse export for the evaluation pipeline.

    The function performs no network access. It validates all source rows before
    writing and never carries Langfuse input/output bodies into an output artifact.
    """

    source_root = Path(input_path)
    output_root = Path(output_dir)
    mapping, mapping_fingerprint = _load_mapping(mapping_path)
    normalized_mapping = _validate_mapping(mapping)
    source_files = _discover_source_files(source_root, output_root)
    file_fingerprints = [_fingerprint_file(path, source_root) for path in source_files]

    source_records: list[_SourceRecord] = []
    source_formats: Counter[str] = Counter()
    skipped_non_observation_rows = 0
    for path in source_files:
        records, skipped = _read_source_file(path, _logical_source_name(path, source_root))
        source_records.extend(records)
        skipped_non_observation_rows += skipped
        source_formats[_source_format(path)] += 1

    if not source_records:
        raise DataValidationError("No Langfuse observation rows were found in the input")

    observations = _normalize_observations(source_records, normalized_mapping)
    traces = _build_traces(observations)
    canonical_events = _build_canonical_events(traces)
    coverage = _build_coverage(traces, normalized_mapping)
    audit_csv, audit_count, audit_mode = _build_audit_csv(traces)
    source_profile = _build_source_profile(
        observations,
        traces,
        source_formats,
        len(source_files),
        skipped_non_observation_rows,
        audit_count,
        audit_mode,
    )
    redaction_report = _build_redaction_report(observations, normalized_mapping["body_evidence"])
    fingerprints = _build_input_fingerprints(file_fingerprints, mapping_fingerprint)
    workflow_config = _build_workflow_config(normalized_mapping, observations)
    starter_manifest = _build_starter_manifest(
        traces, normalized_mapping, coverage, fingerprints["combined_sha256"]
    )
    enterprise_source_profile = _build_enterprise_source_profile(
        traces,
        normalized_mapping,
        source_formats,
        len(source_files),
        audit_count,
        coverage,
        fingerprints["combined_sha256"],
    )
    enterprise_mapping_spec = _build_enterprise_mapping_spec(
        normalized_mapping,
        starter_manifest,
        enterprise_source_profile,
    )
    assessment_manifest = dict(starter_manifest)
    assessment_manifest.pop("onboarding", None)
    mapping_assessment, mapping_friction, mapping_diagnostics = build_mapping_assessment_payload(
        enterprise_source_profile,
        enterprise_mapping_spec,
        assessment_manifest,
        activity_rows=load_mapping_activity(activity_log_path),
    )
    mapping_diagnostic_document = diagnostic_document(
        mapping_diagnostics,
        stage="mapping_assessment",
        passed=mapping_assessment["mapping_readiness"] == "verified",
    )

    artifact_text = {
        "canonical_events.jsonl": _jsonl_text(canonical_events),
        "langfuse_source_profile.json": _json_text(source_profile),
        "telemetry_coverage.json": _json_text(coverage),
        "redaction_report.json": _json_text(redaction_report),
        "trace_audit_sample.csv": audit_csv,
        "input_fingerprints.json": _json_text(fingerprints),
        "evaluation_manifest.monitoring.json": _json_text(starter_manifest),
        "workflow_config.json": _json_text(workflow_config),
        "source_profile.json": _json_text(enterprise_source_profile),
        "mapping_spec.json": _json_text(enterprise_mapping_spec),
        "mapping_assessment.json": _json_text(mapping_assessment),
        "mapping_friction_summary.json": _json_text(mapping_friction),
        "mapping_issue_register.csv": render_mapping_issue_register(
            enterprise_mapping_spec, mapping_diagnostics
        ),
        "mapping_readiness_report.md": render_mapping_readiness_report(
            mapping_assessment, mapping_friction, mapping_diagnostics
        ),
        "diagnostics.json": _json_text(mapping_diagnostic_document),
    }
    _write_artifacts(output_root, artifact_text, overwrite=overwrite)

    return {
        "observation_count": len(observations),
        "trace_count": len(traces),
        "canonical_event_count": len(canonical_events),
        "preparation_gate_passed": coverage["preparation_gate"]["passed"],
        "enterprise_evaluation_gate_passed": coverage["enterprise_evaluation_gate"]["passed"],
        "mapping_readiness": mapping_assessment["mapping_readiness"],
        "artifacts": list(GENERATED_ARTIFACTS),
    }


def _load_mapping(
    mapping_path: str | Path | dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(mapping_path, dict):
        mapping = mapping_path
        encoded = _canonical_json_bytes(mapping)
        return mapping, {
            "path": "inline-langfuse-mapping.json",
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "size_bytes": len(encoded),
        }

    target = Path(mapping_path)
    try:
        raw_bytes = target.read_bytes()
    except FileNotFoundError as exc:
        raise DataValidationError(f"Langfuse mapping file not found: {target}") from exc
    try:
        mapping = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError("Langfuse mapping must be valid UTF-8 JSON") from exc
    if not isinstance(mapping, dict):
        raise ConfigurationError("Langfuse mapping must be a JSON object")
    return mapping, {
        "path": target.name,
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "size_bytes": len(raw_bytes),
    }


def _validate_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    workflow_family = _required_string(mapping.get("workflow_family"), "workflow_family")
    workflow_definition_id = _required_string(
        mapping.get("workflow_definition_id"), "workflow_definition_id"
    )
    paths = mapping.get("metadata_paths")
    if not isinstance(paths, dict):
        raise ConfigurationError("`metadata_paths` must be a JSON object")

    normalized_paths: dict[str, str] = {}
    aliases = {
        "configuration_id": "agent_configuration_id",
        "configuration_version": "agent_version",
    }
    for original_key, value in paths.items():
        path_name = _required_string(original_key, "metadata_paths key")
        canonical_key = aliases.get(path_name, path_name)
        normalized_paths[canonical_key] = _required_string(value, f"metadata_paths.{path_name}")
    for name in _REQUIRED_PATHS:
        normalized_paths[name] = _required_string(
            normalized_paths.get(name), f"metadata_paths.{name}"
        )

    status_mapping = mapping.get("status_mapping")
    if not isinstance(status_mapping, dict):
        raise ConfigurationError("`status_mapping` must be a JSON object")
    if status_mapping.get("approved") is not True:
        raise ConfigurationError(
            "`status_mapping.approved` must be true before business status is derived"
        )
    completed_values = _status_values(status_mapping.get("completed"), "status_mapping.completed")
    failed_values = _status_values(status_mapping.get("failed"), "status_mapping.failed")
    overlap = sorted(set(completed_values) & set(failed_values))
    if overlap:
        raise ConfigurationError(
            "Completed and failed status mappings must be disjoint: " + ", ".join(overlap)
        )

    body_evidence = _validate_body_evidence(mapping.get("body_evidence"))

    return {
        "workflow_family": workflow_family,
        "workflow_definition_id": workflow_definition_id,
        "metadata_paths": normalized_paths,
        "status_mapping": {
            "approved": True,
            "completed": completed_values,
            "failed": failed_values,
        },
        "body_evidence": body_evidence,
    }


def _validate_body_evidence(value: Any) -> dict[str, Any]:
    if value is None:
        return {"mode": "omit"}
    if not isinstance(value, dict):
        raise ConfigurationError("`body_evidence` must be a JSON object")
    if "key" in value or "hmac_key" in value:
        raise ConfigurationError(
            "Langfuse HMAC keys must be supplied through an environment variable"
        )
    extra_fields = sorted(set(value) - {"mode", "hmac_key_env", "key_env", "key_id"})
    if extra_fields:
        raise ConfigurationError(
            "`body_evidence` has unsupported fields: " + ", ".join(extra_fields)
        )
    if "hmac_key_env" in value and "key_env" in value:
        raise ConfigurationError(
            "Configure only one of `body_evidence.hmac_key_env` or `body_evidence.key_env`"
        )
    mode = value.get("mode", "omit")
    if mode not in {"omit", "hmac_sha256"}:
        raise ConfigurationError("`body_evidence.mode` must be `omit` or `hmac_sha256`")
    key_env = value.get("hmac_key_env", value.get("key_env"))
    key_id = value.get("key_id")
    if mode == "omit":
        if key_env is not None or key_id is not None:
            raise ConfigurationError(
                "Langfuse body HMAC settings require `body_evidence.mode` to be `hmac_sha256`"
            )
        return {"mode": "omit"}

    key_env_name = _required_string(key_env, "body_evidence.hmac_key_env")
    key_identifier = _required_string(key_id, "body_evidence.key_id")
    secret = os.environ.get(key_env_name)
    if not secret:
        raise ConfigurationError(
            f"Langfuse body HMAC environment variable `{key_env_name}` is not set"
        )
    return {
        "mode": "hmac_sha256",
        "key_id": key_identifier,
        "_key": secret.encode("utf-8"),
    }


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"`{field_name}` must be a non-empty string")
    return value.strip()


def _status_values(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ConfigurationError(f"`{field_name}` must be a non-empty list")
    normalized: list[str] = []
    for index, item in enumerate(value):
        if isinstance(item, (dict, list)) or item is None:
            raise ConfigurationError(f"`{field_name}[{index}]` must be a scalar value")
        normalized_value = _normalize_status_value(item)
        if not normalized_value:
            raise ConfigurationError(f"`{field_name}[{index}]` must not be empty")
        normalized.append(normalized_value)
    return sorted(set(normalized))


def _discover_source_files(source_root: Path, output_root: Path) -> list[Path]:
    if not source_root.exists():
        raise DataValidationError(f"Langfuse input not found: {source_root}")
    if source_root.is_file():
        if not _is_supported_source(source_root):
            raise DataValidationError("Langfuse input must be JSON, JSONL, JSON.GZ, or JSONL.GZ")
        return [source_root]
    if not source_root.is_dir():
        raise DataValidationError(
            f"Langfuse input is not a regular file or directory: {source_root}"
        )

    output_resolved = output_root.resolve()
    files = []
    for path in source_root.rglob("*"):
        if not path.is_file() or not _is_supported_source(path):
            continue
        resolved = path.resolve()
        if resolved == output_resolved or output_resolved in resolved.parents:
            continue
        files.append(path)
    files.sort(key=lambda path: path.relative_to(source_root).as_posix())
    if not files:
        raise DataValidationError("Langfuse input directory contains no supported source files")
    return files


def _is_supported_source(path: Path) -> bool:
    lower_name = path.name.lower()
    return any(lower_name.endswith(suffix) for suffix in _SUPPORTED_SUFFIXES)


def _source_format(path: Path) -> str:
    lower_name = path.name.lower()
    if lower_name.endswith(".jsonl.gz"):
        return "jsonl.gz"
    if lower_name.endswith(".json.gz"):
        return "json.gz"
    if lower_name.endswith(".jsonl"):
        return "jsonl"
    return "json"


def _logical_source_name(path: Path, source_root: Path) -> str:
    if source_root.is_file():
        return source_root.name
    return path.relative_to(source_root).as_posix()


def _open_text(path: Path) -> Any:
    if path.name.lower().endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def _read_source_file(path: Path, source_name: str) -> tuple[list[_SourceRecord], int]:
    try:
        with _open_text(path) as handle:
            if _source_format(path).startswith("jsonl"):
                return _read_jsonl(handle, source_name)
            payload = json.load(handle)
    except (OSError, UnicodeDecodeError) as exc:
        raise DataValidationError(f"Could not read Langfuse source `{source_name}`") from exc
    except json.JSONDecodeError as exc:
        raise DataValidationError(
            f"Invalid JSON in Langfuse source `{source_name}` at line {exc.lineno}"
        ) from exc
    records = _records_from_payload(payload, source_name, None)
    return _filter_observation_records(records, source_name)


def _read_jsonl(handle: Any, source_name: str) -> tuple[list[_SourceRecord], int]:
    records: list[_SourceRecord] = []
    for line_number, line in enumerate(handle, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DataValidationError(
                f"Invalid JSONL in Langfuse source `{source_name}` at line {line_number}"
            ) from exc
        records.extend(_records_from_payload(payload, source_name, line_number))
    return _filter_observation_records(records, source_name)


def _records_from_payload(
    payload: Any, source_name: str, source_line: int | None
) -> list[_SourceRecord]:
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict) and "data" in payload:
        meta = payload.get("meta", {})
        if meta is not None and not isinstance(meta, dict):
            raise DataValidationError(f"API `meta` in `{source_name}` must be an object")
        if isinstance(meta, dict):
            for cursor_name in ("cursor", "nextCursor", "next_cursor"):
                if cursor_name in meta and meta[cursor_name] is not None:
                    raise DataValidationError(
                        f"Incomplete Langfuse API capture in `{source_name}`: "
                        f"meta.{cursor_name} is non-null"
                    )
        values = payload["data"]
        if not isinstance(values, list):
            raise DataValidationError(f"API `data` in `{source_name}` must be a list")
    elif isinstance(payload, dict) and "observations" in payload:
        values = payload["observations"]
        if not isinstance(values, list):
            raise DataValidationError(f"`observations` in `{source_name}` must be a list")
    elif isinstance(payload, dict):
        values = [payload]
    else:
        raise DataValidationError(f"Langfuse payload in `{source_name}` must be an object or list")

    records: list[_SourceRecord] = []
    for value in values:
        if not isinstance(value, dict):
            raise DataValidationError(f"Langfuse rows in `{source_name}` must be JSON objects")
        records.append(_SourceRecord(value, source_name, source_line))
    return records


def _filter_observation_records(
    records: list[_SourceRecord], source_name: str
) -> tuple[list[_SourceRecord], int]:
    observations: list[_SourceRecord] = []
    skipped = 0
    observation_source = "observation" in source_name.lower()
    for record in records:
        payload = record.payload
        has_id = _has_alias(payload, ("id", "observationId", "observation_id"))
        has_trace = _has_alias(payload, ("traceId", "trace_id"))
        has_start = _has_alias(payload, ("startTime", "start_time"))
        if has_id and has_trace and has_start:
            observations.append(record)
        elif (
            observation_source or has_trace and _has_alias(payload, ("type", "endTime", "end_time"))
        ):
            raise DataValidationError(
                f"Malformed Langfuse observation row in `{source_name}`; id, trace_id, "
                "and start_time are required"
            )
        else:
            skipped += 1
    return observations, skipped


def _normalize_observations(
    records: list[_SourceRecord], mapping: dict[str, Any]
) -> list[_Observation]:
    observations: list[_Observation] = []
    seen_ids: dict[str, str] = {}
    for record in records:
        payload = dict(record.payload)
        metadata = _parse_mapping_object(_alias_value(payload, ("metadata",)), "metadata", record)
        if metadata is not None:
            payload["metadata"] = metadata

        observation_id = _identifier(
            _alias_value(payload, ("id", "observationId", "observation_id")),
            "observation id",
            record,
        )
        if observation_id in seen_ids:
            raise DataValidationError(
                f"Duplicate Langfuse observation id `{observation_id}` in "
                f"`{seen_ids[observation_id]}` and `{record.source_name}`"
            )
        seen_ids[observation_id] = record.source_name
        trace_id = _identifier(_alias_value(payload, ("traceId", "trace_id")), "trace id", record)
        start_time, start_dt = _timestamp(
            _alias_value(payload, ("startTime", "start_time")), "start time", observation_id
        )
        raw_end = _alias_value(payload, ("endTime", "end_time"))
        if raw_end in (None, ""):
            end_time = None
            end_dt = None
        else:
            end_time, end_dt = _timestamp(raw_end, "end time", observation_id)
            if end_dt < start_dt:
                raise DataValidationError(
                    f"Langfuse observation `{observation_id}` ends before it starts"
                )

        observation_type = _optional_string(_alias_value(payload, ("type",))) or "SPAN"
        name = _optional_string(_alias_value(payload, ("name",)))
        trace_name = _optional_string(_alias_value(payload, ("traceName", "trace_name")))
        environment = _optional_string(_alias_value(payload, ("environment",)))
        parent_id = _optional_string(
            _alias_value(payload, ("parentObservationId", "parent_observation_id"))
        )
        model_name = _optional_string(
            _alias_value(payload, ("providedModelName", "provided_model_name"))
        )
        prompt_version = _optional_string(
            _alias_value(payload, ("promptVersion", "prompt_version"))
        )

        usage_details = _parse_mapping_object(
            _alias_value(payload, ("usageDetails", "usage_details")),
            "usage details",
            record,
        )
        cost_details = _parse_mapping_object(
            _alias_value(payload, ("costDetails", "cost_details")),
            "cost details",
            record,
        )
        input_tokens = _usage_value(
            payload,
            usage_details,
            ("inputUsage", "input_usage"),
            ("input", "input_tokens", "promptTokens", "prompt_tokens"),
            "input tokens",
            observation_id,
        )
        output_tokens = _usage_value(
            payload,
            usage_details,
            ("outputUsage", "output_usage"),
            ("output", "output_tokens", "completionTokens", "completion_tokens"),
            "output tokens",
            observation_id,
        )
        observed_cost = _cost_value(payload, cost_details, observation_id)

        paths = mapping["metadata_paths"]
        raw_status = _path_value(payload, paths["status"])
        business_status = _map_business_status(raw_status, mapping["status_mapping"])
        quality_raw = _path_value(payload, paths["quality"])
        quality_score = (
            None
            if quality_raw in (None, "")
            else _nonnegative_float(quality_raw, "quality", observation_id)
        )
        retry_present, retry_value = _path_entry(payload, paths["retry"])
        intervention_present, intervention_value = _path_entry(payload, paths["intervention"])
        handoff_present, handoff_value = _path_entry(payload, paths["handoff"])
        routing_present, routing_value = _path_entry(payload, paths["routing"])

        input_present, input_value = _alias_entry(payload, ("input",))
        output_present, output_value = _alias_entry(payload, ("output",))
        input_evidence = (
            _redacted_descriptor(input_value, mapping["body_evidence"]) if input_present else None
        )
        output_evidence = (
            _redacted_descriptor(output_value, mapping["body_evidence"]) if output_present else None
        )

        tool_call_field_present, raw_tool_names = _alias_entry(
            payload, ("toolCallNames", "tool_call_names")
        )
        tool_names = _tool_names(raw_tool_names, observation_id)
        raw_hash = _body_safe_observation_hash(record.payload)

        observations.append(
            _Observation(
                observation_id=observation_id,
                trace_id=trace_id,
                start_time=start_time,
                start_dt=start_dt,
                end_time=end_time,
                end_dt=end_dt,
                observation_type=observation_type.upper(),
                name=name,
                trace_name=trace_name,
                environment=environment,
                parent_observation_id=parent_id,
                model_name=model_name,
                prompt_version=prompt_version,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                observed_cost_usd=observed_cost,
                business_status=business_status,
                agent_configuration_id=_optional_string(
                    _path_value(payload, paths["agent_configuration_id"])
                ),
                agent_version=_optional_string(_path_value(payload, paths["agent_version"])),
                configuration_hash=_optional_string(
                    _path_value(payload, paths["configuration_hash"])
                ),
                retry_value=retry_value,
                retry_field_present=retry_present,
                intervention_value=intervention_value,
                intervention_field_present=intervention_present,
                handoff_target=_optional_string(handoff_value),
                handoff_field_present=handoff_present,
                routing_decision=_optional_string(routing_value),
                routing_field_present=routing_present,
                quality_score=quality_score,
                tool_names=tool_names,
                tool_call_field_present=tool_call_field_present,
                input_evidence=input_evidence,
                output_evidence=output_evidence,
                source_name=record.source_name,
                source_line=record.source_line,
                source_observation_hash=raw_hash,
                alias_style=_alias_style(record.payload),
            )
        )

    observations.sort(key=lambda item: (item.trace_id, item.start_dt, item.observation_id))
    return observations


def _parse_mapping_object(
    value: Any, field_name: str, record: _SourceRecord
) -> dict[str, Any] | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DataValidationError(
                f"Langfuse {field_name} in `{record.source_name}` must be a JSON object"
            ) from exc
        if isinstance(parsed, dict):
            return parsed
    raise DataValidationError(f"Langfuse {field_name} in `{record.source_name}` must be an object")


def _identifier(value: Any, field_name: str, record: _SourceRecord) -> str:
    result = _optional_string(value)
    if result is None:
        location = (
            f"line {record.source_line} of `{record.source_name}`"
            if record.source_line is not None
            else f"`{record.source_name}`"
        )
        raise DataValidationError(f"Langfuse {field_name} is missing at {location}")
    return result


def _timestamp(value: Any, field_name: str, observation_id: str) -> tuple[str, datetime]:
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError(
            f"Langfuse {field_name} must be a timestamp for observation `{observation_id}`"
        )
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DataValidationError(
            f"Invalid Langfuse {field_name} for observation `{observation_id}`"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    normalized = parsed.isoformat().replace("+00:00", "Z")
    return normalized, parsed


def _usage_value(
    payload: dict[str, Any],
    details: dict[str, Any] | None,
    direct_aliases: tuple[str, ...],
    detail_aliases: tuple[str, ...],
    field_name: str,
    observation_id: str,
) -> int | None:
    direct = _alias_value(payload, direct_aliases)
    detail = _alias_value(details or {}, detail_aliases)
    direct_number = (
        None if direct in (None, "") else _nonnegative_int(direct, field_name, observation_id)
    )
    detail_number = (
        None if detail in (None, "") else _nonnegative_int(detail, field_name, observation_id)
    )
    if direct_number is not None and detail_number is not None and direct_number != detail_number:
        raise DataValidationError(
            f"Conflicting direct and detailed {field_name} for observation `{observation_id}`"
        )
    return direct_number if direct_number is not None else detail_number


def _cost_value(
    payload: dict[str, Any], details: dict[str, Any] | None, observation_id: str
) -> float | None:
    direct = _alias_value(payload, ("totalCost", "total_cost"))
    detail_total = _alias_value(details or {}, ("total", "total_cost"))
    direct_number = (
        None if direct in (None, "") else _nonnegative_float(direct, "total cost", observation_id)
    )
    detail_number = (
        None
        if detail_total in (None, "")
        else _nonnegative_float(detail_total, "total cost", observation_id)
    )
    if detail_number is None and details:
        input_cost = _alias_value(details, ("input", "input_cost"))
        output_cost = _alias_value(details, ("output", "output_cost"))
        components = [
            _nonnegative_float(value, "cost component", observation_id)
            for value in (input_cost, output_cost)
            if value not in (None, "")
        ]
        if components:
            detail_number = sum(components)
    if direct_number is not None and detail_number is not None:
        if abs(direct_number - detail_number) > 1e-12:
            raise DataValidationError(
                f"Conflicting direct and detailed total cost for observation `{observation_id}`"
            )
    return direct_number if direct_number is not None else detail_number


def _nonnegative_int(value: Any, field_name: str, observation_id: str) -> int:
    if isinstance(value, bool):
        raise DataValidationError(
            f"Langfuse {field_name} must be an integer for observation `{observation_id}`"
        )
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise DataValidationError(
            f"Langfuse {field_name} must be an integer for observation `{observation_id}`"
        ) from exc
    try:
        if float(value) != number:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise DataValidationError(
            f"Langfuse {field_name} must be an integer for observation `{observation_id}`"
        ) from exc
    if number < 0:
        raise DataValidationError(
            f"Langfuse {field_name} cannot be negative for observation `{observation_id}`"
        )
    return number


def _nonnegative_float(value: Any, field_name: str, observation_id: str) -> float:
    if isinstance(value, bool):
        raise DataValidationError(
            f"Langfuse {field_name} must be numeric for observation `{observation_id}`"
        )
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DataValidationError(
            f"Langfuse {field_name} must be numeric for observation `{observation_id}`"
        ) from exc
    if number < 0 or number != number or number in (float("inf"), float("-inf")):
        raise DataValidationError(
            f"Langfuse {field_name} must be finite and non-negative for "
            f"observation `{observation_id}`"
        )
    return number


def _tool_names(value: Any, observation_id: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [value]
        value = parsed
    if not isinstance(value, list):
        raise DataValidationError(
            f"Langfuse tool-call names must be a list for observation `{observation_id}`"
        )
    result = []
    for item in value:
        name = _optional_string(item)
        if name is not None:
            result.append(name)
    return tuple(result)


def _redacted_descriptor(value: Any, body_evidence: dict[str, Any]) -> dict[str, Any]:
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )
    descriptor = {
        "value": REDACTED_VALUE,
        "type": _json_type(value),
        "length": len(text),
    }
    if body_evidence["mode"] == "hmac_sha256":
        descriptor["hmac_sha256"] = hmac.new(
            body_evidence["_key"], text.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        descriptor["key_id"] = body_evidence["key_id"]
    return descriptor


def _body_safe_observation_hash(payload: dict[str, Any]) -> str:
    body_free = {
        key: value for key, value in payload.items() if key.casefold() not in {"input", "output"}
    }
    return hashlib.sha256(_canonical_json_bytes(body_free)).hexdigest()


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _build_traces(observations: list[_Observation]) -> list[_Trace]:
    grouped: dict[str, list[_Observation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.trace_id].append(observation)

    traces: list[_Trace] = []
    for trace_id, values in sorted(grouped.items()):
        values.sort(key=lambda item: (item.start_dt, item.observation_id))
        outcomes = {item.business_status for item in values if item.business_status is not None}
        if len(outcomes) > 1:
            raise DataValidationError(
                f"Trace `{trace_id}` contains conflicting explicitly mapped business statuses"
            )
        outcome = next(iter(outcomes)) if outcomes else None
        start_observation = min(values, key=lambda item: (item.start_dt, item.observation_id))
        ended = [item for item in values if item.end_dt is not None]
        end_observation = (
            max(ended, key=lambda item: (item.end_dt, item.observation_id)) if ended else None
        )
        last_start = max(item.start_dt for item in values)
        boundary_order_valid = False
        if end_observation is not None:
            assert end_observation.end_dt is not None
            boundary_order_valid = end_observation.end_dt >= last_start
        traces.append(
            _Trace(
                trace_id=trace_id,
                observations=tuple(values),
                start_time=start_observation.start_time,
                start_dt=start_observation.start_dt,
                end_time=end_observation.end_time if end_observation else None,
                end_dt=end_observation.end_dt if end_observation else None,
                outcome=outcome,
                boundary_order_valid=boundary_order_valid,
                trace_name=_consistent_value(item.trace_name for item in values),
                environment=_consistent_value(item.environment for item in values),
                configuration_ids=_unique_values(item.agent_configuration_id for item in values),
                agent_versions=_unique_values(item.agent_version for item in values),
                configuration_hashes=_unique_values(item.configuration_hash for item in values),
            )
        )
    return traces


def _build_canonical_events(traces: list[_Trace]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for trace in traces:
        trace_config = _trace_config(trace)
        events.append(
            _event(
                event_id=f"langfuse:{trace.trace_id}:run-started",
                timestamp=trace.start_time,
                event_name="workflow_started",
                trace=trace,
                step_name="workflow",
                status="in_progress",
                config=trace_config,
                sort_order=0,
            )
        )
        for observation in trace.observations:
            config = {
                "agent_config_id": observation.agent_configuration_id,
                "agent_version": observation.agent_version,
                "config_hash": observation.configuration_hash,
            }
            timestamp = observation.end_time or observation.start_time
            base = _event(
                event_id=f"langfuse:{observation.observation_id}:observation",
                timestamp=timestamp,
                event_name=(
                    "agent_step_completed" if observation.end_time else "agent_step_started"
                ),
                trace=trace,
                step_name=observation.name or "unnamed_observation",
                status="in_progress",
                config=config,
                sort_order=10,
            )
            _set_if_present(base, "input_tokens", observation.input_tokens)
            _set_if_present(base, "output_tokens", observation.output_tokens)
            _set_if_present(base, "observed_cost_usd", observation.observed_cost_usd)
            _set_if_present(base, "quality_score", observation.quality_score)
            _set_if_present(base, "model_name", observation.model_name)
            _set_if_present(base, "prompt_version", observation.prompt_version)
            _set_if_present(base, "parent_observation_id", observation.parent_observation_id)
            _set_if_present(base, "langfuse_input_evidence", observation.input_evidence)
            _set_if_present(base, "langfuse_output_evidence", observation.output_evidence)
            base["langfuse_observation_type"] = observation.observation_type
            base["source_observation_hash"] = observation.source_observation_hash
            base["source_name"] = observation.source_name
            _set_if_present(base, "source_line", observation.source_line)
            _set_if_present(base, "trace_name", observation.trace_name)
            _set_if_present(base, "environment", observation.environment)
            events.append(base)

            marker_specs: list[tuple[str, str, dict[str, Any]]] = []
            if _marker_is_set(observation.retry_value):
                marker_specs.append(
                    (
                        "retry",
                        "retry_requested",
                        {"attempt": _retry_attempt(observation.retry_value)},
                    )
                )
            if _marker_is_set(observation.intervention_value):
                marker_specs.append(
                    (
                        "intervention",
                        "human_intervention_requested",
                        {"human_intervention": True},
                    )
                )
            if observation.handoff_target is not None:
                marker_specs.append(
                    (
                        "handoff",
                        "handoff_requested",
                        {"handoff_target": observation.handoff_target},
                    )
                )
            if observation.routing_decision is not None:
                marker_specs.append(
                    (
                        "routing",
                        "routing_decision_recorded",
                        {"routing_decision": observation.routing_decision},
                    )
                )
            for marker_index, (suffix, event_name, fields) in enumerate(marker_specs):
                marker = _event(
                    event_id=f"langfuse:{observation.observation_id}:{suffix}",
                    timestamp=timestamp,
                    event_name=event_name,
                    trace=trace,
                    step_name=observation.name or "unnamed_observation",
                    status="in_progress",
                    config=config,
                    sort_order=20 + marker_index,
                )
                for key, value in fields.items():
                    _set_if_present(marker, key, value)
                events.append(marker)
            for tool_index, tool_name in enumerate(observation.tool_names):
                tool = _event(
                    event_id=f"langfuse:{observation.observation_id}:tool:{tool_index + 1}",
                    timestamp=timestamp,
                    event_name="tool_invoked",
                    trace=trace,
                    step_name=observation.name or "unnamed_observation",
                    status="in_progress",
                    config=config,
                    sort_order=30 + tool_index,
                )
                tool["tool_name"] = tool_name
                tool["tool_call_id"] = f"{observation.observation_id}:{tool_index + 1}"
                events.append(tool)

        if trace.outcome is not None and trace.end_time is not None and trace.boundary_order_valid:
            events.append(
                _event(
                    event_id=f"langfuse:{trace.trace_id}:run-{trace.outcome}",
                    timestamp=trace.end_time,
                    event_name=(
                        "workflow_completed" if trace.outcome == "completed" else "workflow_failed"
                    ),
                    trace=trace,
                    step_name="workflow",
                    status=trace.outcome,
                    config=trace_config,
                    sort_order=99,
                )
            )

    events.sort(
        key=lambda event: (
            event["workflow_run_id"],
            _parse_normalized_timestamp(event["timestamp"]),
            event.pop("_sort_order"),
            event["event_id"],
        )
    )
    return events


def _event(
    *,
    event_id: str,
    timestamp: str,
    event_name: str,
    trace: _Trace,
    step_name: str,
    status: str,
    config: dict[str, str | None],
    sort_order: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "event_id": event_id,
        "timestamp": timestamp,
        "event_name": event_name,
        "workflow_run_id": trace.trace_id,
        "trace_id": trace.trace_id,
        "step_name": step_name,
        "status": status,
        "_sort_order": sort_order,
    }
    for key, value in config.items():
        _set_if_present(result, key, value)
    return result


def _build_coverage(traces: list[_Trace], mapping: dict[str, Any]) -> dict[str, Any]:
    denominator = len(traces)
    required_checks: dict[str, list[bool]] = {
        "run_id": [bool(trace.trace_id) for trace in traces],
        "trace_id": [bool(trace.trace_id) for trace in traces],
        "timestamp": [
            trace.end_time is not None and trace.boundary_order_valid for trace in traces
        ],
        "run_boundary": [
            trace.end_time is not None and trace.boundary_order_valid and trace.outcome is not None
            for trace in traces
        ],
        "status": [trace.outcome is not None for trace in traces],
        "step_name": [
            all(item.name is not None for item in trace.observations) for trace in traces
        ],
        "agent_configuration_id": [len(trace.configuration_ids) == 1 for trace in traces],
        "agent_version": [len(trace.agent_versions) == 1 for trace in traces],
        "configuration_hash": [len(trace.configuration_hashes) == 1 for trace in traces],
    }
    optional_checks: dict[str, list[bool]] = {
        "input_tokens": [
            any(item.input_tokens is not None for item in trace.observations) for trace in traces
        ],
        "output_tokens": [
            any(item.output_tokens is not None for item in trace.observations) for trace in traces
        ],
        "observed_cost_usd": [
            any(item.observed_cost_usd is not None for item in trace.observations)
            for trace in traces
        ],
        "quality_score": [
            any(item.quality_score is not None for item in trace.observations) for trace in traces
        ],
        "retry_markers": [
            any(item.retry_field_present for item in trace.observations) for trace in traces
        ],
        "intervention_markers": [
            any(item.intervention_field_present for item in trace.observations) for trace in traces
        ],
        "handoff_targets": [
            any(item.handoff_field_present for item in trace.observations) for trace in traces
        ],
        "routing_decisions": [
            any(item.routing_field_present for item in trace.observations) for trace in traces
        ],
        "tool_call_trace": [
            any(item.tool_names for item in trace.observations) for trace in traces
        ],
    }
    required = {
        name: _coverage_entry(values, denominator, required=True)
        for name, values in required_checks.items()
    }
    optional = {
        name: _coverage_entry(values, denominator, required=False)
        for name, values in optional_checks.items()
    }
    missing_required = sorted(
        name for name, details in required.items() if details["covered_count"] != denominator
    )
    configuration_drift = sorted(
        trace.trace_id
        for trace in traces
        if len(trace.configuration_ids) > 1
        or len(trace.agent_versions) > 1
        or len(trace.configuration_hashes) > 1
    )
    preparation_passed = denominator > 0 and not missing_required and not configuration_drift
    return {
        "artifact_schema_version": LANGFUSE_ARTIFACT_SCHEMA_VERSION,
        "trace_count": denominator,
        "required": required,
        "optional": optional,
        "capability_declarations": {
            "retry": mapping["metadata_paths"]["retry"],
            "intervention": mapping["metadata_paths"]["intervention"],
            "handoff": mapping["metadata_paths"]["handoff"],
            "routing": mapping["metadata_paths"]["routing"],
            "quality": mapping["metadata_paths"]["quality"],
            "token": "Langfuse usageDetails/inputUsage/outputUsage",
            "cost": "Langfuse totalCost/costDetails",
            "tool_call": "Langfuse toolCallNames",
        },
        "configuration_drift_trace_ids": configuration_drift,
        "preparation_gate": {
            "passed": preparation_passed,
            "required_coverage_percent": (
                100.0
                if not required
                else round(
                    100
                    * sum(item["covered_count"] for item in required.values())
                    / (denominator * len(required)),
                    3,
                )
            ),
            "missing_required_signals": missing_required,
        },
        "enterprise_evaluation_gate": {
            "passed": False,
            "reason": (
                "Manual trace-as-workflow-run approval is pending"
                if preparation_passed
                else "Required telemetry coverage or configuration consistency gate failed"
            ),
            "trace_as_workflow_run_approved": False,
        },
    }


def _coverage_entry(values: list[bool], denominator: int, *, required: bool) -> dict[str, Any]:
    covered = sum(values)
    return {
        "required": required,
        "covered_count": covered,
        "denominator": denominator,
        "coverage_percent": round(100 * covered / denominator, 3) if denominator else 0.0,
        "missing_trace_count": denominator - covered,
    }


def _build_audit_csv(traces: list[_Trace]) -> tuple[str, int, str]:
    selected, mode = _select_audit_traces(traces)
    fieldnames = [
        "trace_id",
        "trace_name",
        "environment",
        "observation_count",
        "start_time",
        "end_time",
        "mapped_business_status",
        "boundary_complete",
        "agent_configuration_ids",
        "agent_versions",
        "configuration_hashes",
        "input_tokens",
        "output_tokens",
        "observed_cost_usd",
        "selection_mode",
        "input_output_retention",
        "reviewed",
        "trace_as_workflow_run_approved",
        "reviewer",
        "review_notes",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for trace in selected:
        writer.writerow(
            {
                "trace_id": trace.trace_id,
                "trace_name": trace.trace_name or "",
                "environment": trace.environment or "",
                "observation_count": len(trace.observations),
                "start_time": trace.start_time,
                "end_time": trace.end_time or "",
                "mapped_business_status": trace.outcome or "",
                "boundary_complete": str(
                    trace.end_time is not None
                    and trace.boundary_order_valid
                    and trace.outcome is not None
                ).lower(),
                "agent_configuration_ids": "|".join(trace.configuration_ids),
                "agent_versions": "|".join(trace.agent_versions),
                "configuration_hashes": "|".join(trace.configuration_hashes),
                "input_tokens": _sum_optional(item.input_tokens for item in trace.observations),
                "output_tokens": _sum_optional(item.output_tokens for item in trace.observations),
                "observed_cost_usd": _sum_optional(
                    item.observed_cost_usd for item in trace.observations
                ),
                "selection_mode": mode,
                "input_output_retention": "redacted_descriptors_only",
                "reviewed": "false",
                "trace_as_workflow_run_approved": "false",
                "reviewer": "",
                "review_notes": "",
            }
        )
    return output.getvalue(), len(selected), mode


def _select_audit_traces(traces: list[_Trace]) -> tuple[list[_Trace], str]:
    if len(traces) <= MAX_AUDIT_TRACES:
        return sorted(traces, key=lambda item: item.trace_id), "all_traces"

    groups: dict[tuple[str, str], list[_Trace]] = defaultdict(list)
    for trace in traces:
        groups[(trace.trace_name or "(missing)", trace.environment or "(missing)")].append(trace)
    for values in groups.values():
        values.sort(key=lambda item: (_stable_hash(item.trace_id), item.trace_id))
    group_keys = sorted(groups, key=lambda key: (_stable_hash("\x1f".join(key)), key))

    selected: list[_Trace] = []
    cursor = 0
    while len(selected) < MAX_AUDIT_TRACES:
        added = False
        for key in group_keys:
            values = groups[key]
            if cursor < len(values):
                selected.append(values[cursor])
                added = True
                if len(selected) == MAX_AUDIT_TRACES:
                    break
        if not added:
            break
        cursor += 1
    return selected, "deterministic_stratified_30"


def _build_source_profile(
    observations: list[_Observation],
    traces: list[_Trace],
    source_formats: Counter[str],
    file_count: int,
    skipped_non_observation_rows: int,
    audit_count: int,
    audit_mode: str,
) -> dict[str, Any]:
    observation_types = Counter(item.observation_type for item in observations)
    alias_styles = Counter(item.alias_style for item in observations)
    mapped_statuses = Counter(
        item.business_status or "unmapped_or_missing" for item in observations
    )
    return {
        "artifact_schema_version": LANGFUSE_ARTIFACT_SCHEMA_VERSION,
        "source": "offline_langfuse_export",
        "file_count": file_count,
        "source_formats": dict(sorted(source_formats.items())),
        "observation_count": len(observations),
        "trace_count": len(traces),
        "skipped_non_observation_row_count": skipped_non_observation_rows,
        "observation_types": dict(sorted(observation_types.items())),
        "field_alias_styles": dict(sorted(alias_styles.items())),
        "mapped_status_observation_counts": dict(sorted(mapped_statuses.items())),
        "usage_totals": {
            "input_tokens": _sum_optional(item.input_tokens for item in observations),
            "output_tokens": _sum_optional(item.output_tokens for item in observations),
            "observed_cost_usd": _sum_optional(item.observed_cost_usd for item in observations),
            "observations_with_input_tokens": sum(
                item.input_tokens is not None for item in observations
            ),
            "observations_with_output_tokens": sum(
                item.output_tokens is not None for item in observations
            ),
            "observations_with_observed_cost": sum(
                item.observed_cost_usd is not None for item in observations
            ),
        },
        "trace_audit": {
            "sample_count": audit_count,
            "selection_mode": audit_mode,
            "maximum_sample_size": MAX_AUDIT_TRACES,
            "strata": ["trace_name", "environment"],
        },
    }


def _build_enterprise_source_profile(
    traces: list[_Trace],
    mapping: dict[str, Any],
    source_formats: Counter[str],
    file_count: int,
    audit_count: int,
    coverage: dict[str, Any],
    source_fingerprint: str,
) -> dict[str, Any]:
    denominator = len(traces)
    signal_paths = _langfuse_signal_paths(mapping)
    field_inventory_by_path: dict[str, dict[str, Any]] = {}
    coverage_entries = {**coverage["required"], **coverage["optional"]}
    type_by_signal = {
        "input_tokens": ["integer"],
        "output_tokens": ["integer"],
        "observed_cost_usd": ["number"],
        "quality_score": ["number"],
        "retry_markers": ["boolean", "integer", "string"],
        "intervention_markers": ["boolean", "integer", "string"],
    }
    for signal, path in sorted(signal_paths.items()):
        entry = coverage_entries.get(signal)
        observed_count = int(entry["covered_count"]) if entry else denominator
        existing = field_inventory_by_path.get(path)
        value_types = set(type_by_signal.get(signal, ["string"]))
        if existing is not None:
            existing["observed_count"] = max(existing["observed_count"], observed_count)
            existing["coverage"] = (
                round(existing["observed_count"] / denominator, 3) if denominator else None
            )
            existing["value_types"] = sorted(set(existing["value_types"]) | value_types)
            continue
        field_inventory_by_path[path] = {
            "path": path,
            "observed_count": observed_count,
            "record_denominator": denominator,
            "coverage": round(observed_count / denominator, 3) if denominator else None,
            "value_types": sorted(value_types),
        }
    return {
        "source_profile_schema_version": "1.0.0",
        "logical_source_id": f"langfuse-{source_fingerprint[:12]}",
        "source_kind": "offline_langfuse_export",
        "file_count": file_count,
        "record_count": denominator,
        "formats": sorted(source_formats),
        "field_inventory": [
            field_inventory_by_path[path] for path in sorted(field_inventory_by_path)
        ],
        "manual_review_record_count": audit_count,
    }


def _build_enterprise_mapping_spec(
    mapping: dict[str, Any],
    starter_manifest: dict[str, Any],
    source_profile: dict[str, Any],
) -> dict[str, Any]:
    signal_paths = _langfuse_signal_paths(mapping)
    requested_signals = (
        starter_manifest["telemetry_requirements"]["required"]
        + starter_manifest["telemetry_requirements"]["optional"]
    )
    derived_signals = {"run_id", "workflow_definition_id", "event_type"}
    lookup_signals = {"status"}
    mappings: list[dict[str, Any]] = []
    for signal in requested_signals:
        trace_semantics_pending = signal in {"run_id", "trace_id", "run_boundary"}
        mappings.append(
            {
                "canonical_signal": signal,
                "source_paths": [signal_paths[signal]],
                "transformation": (
                    "derivation"
                    if signal in derived_signals
                    else "lookup" if signal in lookup_signals else "direct"
                ),
                "semantic_status": "provisional" if trace_semantics_pending else "confirmed",
                "transformation_tested": True,
                "approval": (
                    {
                        "status": "provisional",
                        "reviewer_role": None,
                        "evidence_ref": None,
                    }
                    if trace_semantics_pending
                    else {
                        "status": "approved",
                        "reviewer_role": "mapping-owner",
                        "evidence_ref": "approved-offline-mapping",
                    }
                ),
            }
        )
    return {
        "mapping_id": f"langfuse-mapping-{source_profile['logical_source_id'][-12:]}",
        "mapping_version": "prepared-v1",
        "logical_source_id": source_profile["logical_source_id"],
        "mappings": mappings,
        "workflow_boundary_approval": {
            "status": "provisional",
            "reviewer_role": None,
            "evidence_ref": None,
        },
        "business_status_approval": {
            "status": "approved",
            "reviewer_role": "mapping-owner",
            "evidence_ref": "approved-status-mapping",
        },
        "privacy_approval": {
            "status": "approved",
            "reviewer_role": "mapping-owner",
            "evidence_ref": "strict-body-omission-policy",
        },
        "issues": [],
    }


def _langfuse_signal_paths(mapping: dict[str, Any]) -> dict[str, str]:
    metadata = mapping["metadata_paths"]
    return {
        "run_id": "traceId|trace_id",
        "trace_id": "traceId|trace_id",
        "timestamp": "startTime|start_time",
        "run_boundary": "startTime|start_time+endTime|end_time",
        "workflow_definition_id": "$derived.workflow_definition_id",
        "event_type": "$derived.event_type",
        "status": metadata["status"],
        "step_name": "name",
        "agent_configuration_id": metadata["agent_configuration_id"],
        "agent_version": metadata["agent_version"],
        "configuration_hash": metadata["configuration_hash"],
        "input_tokens": "usageDetails.input|inputUsage",
        "output_tokens": "usageDetails.output|outputUsage",
        "observed_cost_usd": "totalCost|costDetails",
        "quality_score": metadata["quality"],
        "retry_markers": metadata["retry"],
        "intervention_markers": metadata["intervention"],
        "handoff_targets": metadata["handoff"],
        "routing_decisions": metadata["routing"],
        "tool_call_trace": "toolCallNames|tool_call_names",
    }


def _build_redaction_report(
    observations: list[_Observation], body_evidence: dict[str, Any]
) -> dict[str, Any]:
    input_values = [item.input_evidence for item in observations if item.input_evidence]
    output_values = [item.output_evidence for item in observations if item.output_evidence]
    types = Counter(
        evidence["type"] for evidence in [*input_values, *output_values] if evidence is not None
    )
    retained_attributes = ["value", "type", "length"]
    policy: dict[str, Any] = {
        "fields": ["input", "output"],
        "replacement": REDACTED_VALUE,
        "mode": body_evidence["mode"],
        "retained_attributes": retained_attributes,
        "raw_sha256_retained": False,
    }
    if body_evidence["mode"] == "hmac_sha256":
        retained_attributes.extend(["hmac_sha256", "key_id"])
        policy["hash_algorithm"] = "hmac-sha256"
        policy["key_id"] = body_evidence["key_id"]
    else:
        policy["hash_algorithm"] = "none"
    return {
        "artifact_schema_version": LANGFUSE_ARTIFACT_SCHEMA_VERSION,
        "passed": True,
        "policy": policy,
        "redacted_field_counts": {
            "input": len(input_values),
            "output": len(output_values),
        },
        "redacted_value_types": dict(sorted(types.items())),
        "raw_input_output_values_retained": 0,
        "raw_body_sha256_values_retained": 0,
        "redacted_descriptor_count": len(input_values) + len(output_values),
    }


def _build_input_fingerprints(
    files: list[dict[str, Any]], mapping_fingerprint: dict[str, Any]
) -> dict[str, Any]:
    ordered_files = sorted(files, key=lambda item: item["path"])
    combined_payload = {"source_files": ordered_files, "mapping": mapping_fingerprint}
    combined_hash = hashlib.sha256(_canonical_json_bytes(combined_payload)).hexdigest()
    return {
        "artifact_schema_version": LANGFUSE_ARTIFACT_SCHEMA_VERSION,
        "hash_algorithm": "sha256",
        "source_files": ordered_files,
        "mapping": mapping_fingerprint,
        "combined_sha256": combined_hash,
    }


def _build_workflow_config(
    mapping: dict[str, Any], observations: list[_Observation]
) -> dict[str, Any]:
    by_trace: dict[str, list[_Observation]] = defaultdict(list)
    for observation in observations:
        by_trace[observation.trace_id].append(observation)
    telemetry_capabilities: list[str] = []
    count_capabilities = (
        ("retry_markers", "retry_field_present"),
        ("intervention_markers", "intervention_field_present"),
        ("handoff_targets", "handoff_field_present"),
        ("routing_decisions", "routing_field_present"),
    )
    for capability, presence_field in count_capabilities:
        if by_trace and all(
            any(bool(getattr(item, presence_field)) for item in trace_observations)
            for trace_observations in by_trace.values()
        ):
            telemetry_capabilities.append(capability)
    if by_trace and all(
        any(item.tool_call_field_present for item in trace_observations)
        for trace_observations in by_trace.values()
    ):
        telemetry_capabilities.append("tool_call_trace")
    value_capabilities = (
        ("input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens"),
        ("observed_cost_usd", "observed_cost_usd"),
        ("quality_score", "quality_score"),
    )
    for capability, value_field in value_capabilities:
        if any(getattr(item, value_field) is not None for item in observations):
            telemetry_capabilities.append(capability)
    return {
        "workflow_family": mapping["workflow_family"],
        "workflow_definition_id": mapping["workflow_definition_id"],
        "source_schema": {
            "event_id_field": "event_id",
            "timestamp_field": "timestamp",
            "event_type_field": "event_name",
        },
        "event_type_map": {
            "workflow_started": "run_started",
            "agent_step_started": "step_started",
            "agent_step_completed": "step_completed",
            "tool_invoked": "tool_call",
            "routing_decision_recorded": "routing_decision",
            "retry_requested": "retry",
            "handoff_requested": "handoff",
            "human_intervention_requested": "intervention",
            "quality_check_completed": "quality_check",
            "workflow_completed": "run_completed",
            "workflow_failed": "run_failed",
        },
        "run_id_keys": ["workflow_run_id"],
        "trace_id_keys": ["trace_id"],
        "step_name_keys": ["step_name"],
        "status_field": "status",
        "attempt_field": "attempt",
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
        "quality_score_field": "quality_score",
        "capture_raw_fields": [
            "event_name",
            "workflow_run_id",
            "trace_id",
            "step_name",
            "status",
            "agent_config_id",
            "agent_version",
            "config_hash",
            "source_observation_hash",
            "langfuse_input_evidence",
            "langfuse_output_evidence",
        ],
        "allowed_metadata_fields": [
            "langfuse_observation_type",
            "parent_observation_id",
            "trace_name",
            "environment",
        ],
        "restricted_fields": [],
        "redact_raw_fields": [],
        "evidence_mode": "anchor_hash_redacted_snapshot",
        "run_boundaries": {
            "start_event_type": "run_started",
            "completed_event_type": "run_completed",
            "failed_event_type": "run_failed",
        },
        "telemetry_capabilities": telemetry_capabilities,
    }


def _build_starter_manifest(
    traces: list[_Trace],
    mapping: dict[str, Any],
    coverage: dict[str, Any],
    combined_fingerprint: str,
) -> dict[str, Any]:
    starts = [trace.start_dt for trace in traces]
    ends = [trace.end_dt for trace in traces if trace.end_dt is not None]
    start_dt = min(starts)
    end_dt = max(ends) if ends else max(starts) + timedelta(seconds=1)
    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(seconds=1)

    config_ids = _unique_values(value for trace in traces for value in trace.configuration_ids)
    versions = _unique_values(value for trace in traces for value in trace.agent_versions)
    hashes = _unique_values(value for trace in traces for value in trace.configuration_hashes)
    evaluation_id = f"langfuse-monitoring-{combined_fingerprint[:12]}"
    return {
        "evaluation_id": evaluation_id,
        "evaluation_design": "monitoring",
        "evidence_context": {
            "source_kind": "enterprise_operational",
            "mapping_assessment_required": True,
        },
        "publication_policy": {"minimum_evidence_tier": "partial"},
        "measurement_window": {
            "start": start_dt.isoformat().replace("+00:00", "Z"),
            "end": end_dt.isoformat().replace("+00:00", "Z"),
            "timezone": "UTC",
        },
        "baseline": {
            "name": "observed_langfuse_workflow",
            "type": "descriptive_monitoring",
            "definition": "Observed workflow runs reconstructed from an approved Langfuse export.",
        },
        "comparator": {
            "name": "no_causal_comparator",
            "type": "none",
            "definition": "Monitoring evidence is descriptive and has no causal comparator.",
        },
        "assumptions": {},
        "telemetry_requirements": {
            "required": [
                "run_id",
                "timestamp",
                "workflow_definition_id",
                "trace_id",
                "event_type",
                "status",
                "step_name",
                "agent_configuration_id",
                "agent_version",
                "configuration_hash",
            ],
            "optional": [
                "input_tokens",
                "output_tokens",
                "observed_cost_usd",
                "quality_score",
                "retry_markers",
                "intervention_markers",
                "handoff_targets",
                "routing_decisions",
                "tool_call_trace",
            ],
        },
        "configuration": {
            "agent_configuration_id": _manifest_value(config_ids, "configuration-id"),
            "agent_version": _manifest_value(versions, "agent-version"),
            "configuration_hash": _manifest_value(hashes, "configuration-hash"),
        },
        "taxonomy": {
            "deployment_mode": "embedded_automation",
            "workflow_integration": mapping["workflow_family"],
            "autonomy_level": "review_required",
            "hitl_pattern": "review_required",
            "statefulness": "workflow_stateful",
            "memory_strategy": "review_required",
            "composition": "review_required",
            "decomposition_strategy": "review_required",
            "allocation_mechanism": "review_required",
            "routing_strategy": "review_required",
            "protocol_surfaces": [],
        },
        "inclusion_rules": [
            "Include only traces approved as one complete workflow run after audit review."
        ],
        "exclusion_rules": [
            "Exclude traces with incomplete boundaries, missing required telemetry, test traffic, "
            "or configuration drift."
        ],
        "run_exclusions": [],
        "interpretation_boundaries": [
            "Monitoring supports descriptive evidence only.",
            "Absence of a Langfuse error is not interpreted as business completion.",
            "No economic or causal claim is authorized by this starter manifest.",
        ],
        "concurrent_changes": [],
        "onboarding": {
            "source": "offline_langfuse_export",
            "input_fingerprint": combined_fingerprint,
            "status_mapping_approved": True,
            "trace_as_workflow_run_approved": False,
            "trace_audit_file": "trace_audit_sample.csv",
            "reviewer": None,
            "review_evidence": "Pending review of trace_audit_sample.csv",
            "preparation_gate_passed": coverage["preparation_gate"]["passed"],
            "enterprise_evaluation_gate": "pending_manual_trace_review",
        },
    }


def _manifest_value(values: tuple[str, ...], label: str) -> str:
    if len(values) == 1:
        return values[0]
    if not values:
        return f"review-required-missing-{label}"
    return f"review-required-multiple-{label}s"


def _fingerprint_file(path: Path, source_root: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise DataValidationError(
            f"Could not fingerprint Langfuse source `{_logical_source_name(path, source_root)}`"
        ) from exc
    return {
        "path": _logical_source_name(path, source_root),
        "sha256": digest.hexdigest(),
        "size_bytes": size,
    }


def _write_artifacts(output_root: Path, artifact_text: dict[str, str], *, overwrite: bool) -> None:
    if output_root.exists() and not output_root.is_dir():
        raise DataValidationError(f"Langfuse output path is not a directory: {output_root}")
    existing = [name for name in GENERATED_ARTIFACTS if (output_root / name).exists()]
    if existing and not overwrite:
        raise DataValidationError(
            "Refusing to overwrite existing Langfuse artifacts: " + ", ".join(existing)
        )

    output_root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".langfuse-prepare-", dir=output_root.parent))
    replacement: Path | None = None
    backup: Path | None = None
    try:
        for name in GENERATED_ARTIFACTS:
            (stage / name).write_text(artifact_text[name], encoding="utf-8", newline="")
        if not output_root.exists():
            os.replace(stage, output_root)
            return

        replacement = Path(tempfile.mkdtemp(prefix=".langfuse-publish-", dir=output_root.parent))
        generated_names = set(GENERATED_ARTIFACTS)
        for child in output_root.iterdir():
            if child.name in generated_names:
                continue
            destination = replacement / child.name
            if child.is_symlink():
                destination.symlink_to(os.readlink(child))
            elif child.is_dir():
                shutil.copytree(child, destination, symlinks=True)
            else:
                shutil.copy2(child, destination)
        for name in GENERATED_ARTIFACTS:
            os.replace(stage / name, replacement / name)

        backup = Path(tempfile.mkdtemp(prefix=".langfuse-backup-", dir=output_root.parent))
        backup.rmdir()
        os.replace(output_root, backup)
        try:
            os.replace(replacement, output_root)
        except OSError:
            os.replace(backup, output_root)
            backup = None
            raise
        shutil.rmtree(backup, ignore_errors=True)
        backup = None
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        if replacement is not None and replacement.exists():
            shutil.rmtree(replacement)
        if backup is not None and backup.exists() and not output_root.exists():
            os.replace(backup, output_root)
        elif backup is not None and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _jsonl_text(values: Iterable[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        for value in values
    )


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _has_alias(payload: dict[str, Any], aliases: tuple[str, ...]) -> bool:
    return any(alias in payload for alias in aliases)


def _alias_entry(payload: dict[str, Any], aliases: tuple[str, ...]) -> tuple[bool, Any]:
    for alias in aliases:
        if alias in payload:
            return True, payload[alias]
    return False, None


def _alias_value(payload: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    return _alias_entry(payload, aliases)[1]


def _path_value(payload: dict[str, Any], path: str) -> Any:
    return _path_entry(payload, path)[1]


def _path_entry(payload: dict[str, Any], path: str) -> tuple[bool, Any]:
    if path.startswith("$."):
        path = path[2:]
    if path.startswith("/"):
        parts = [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]
    else:
        parts = path.split(".")
    value: Any = payload
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


def _optional_string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    result = str(value).strip()
    return result or None


def _normalize_status_value(value: Any) -> str:
    return str(value).strip().casefold()


def _map_business_status(value: Any, status_mapping: dict[str, Any]) -> str | None:
    if value in (None, "") or isinstance(value, (dict, list)):
        return None
    normalized = _normalize_status_value(value)
    if normalized in status_mapping["completed"]:
        return "completed"
    if normalized in status_mapping["failed"]:
        return "failed"
    return None


def _alias_style(payload: dict[str, Any]) -> str:
    camel = any(
        key in payload for key in ("traceId", "startTime", "endTime", "usageDetails", "totalCost")
    )
    snake = any(
        key in payload
        for key in ("trace_id", "start_time", "end_time", "usage_details", "total_cost")
    )
    if camel and snake:
        return "mixed"
    if camel:
        return "camelCase"
    if snake:
        return "snake_case"
    return "core_only"


def _consistent_value(values: Iterable[str | None]) -> str | None:
    unique = _unique_values(values)
    return unique[0] if len(unique) == 1 else None


def _unique_values(values: Iterable[str | None]) -> tuple[str, ...]:
    return tuple(sorted({value for value in values if value is not None}))


def _trace_config(trace: _Trace) -> dict[str, str | None]:
    return {
        "agent_config_id": (
            trace.configuration_ids[0] if len(trace.configuration_ids) == 1 else None
        ),
        "agent_version": trace.agent_versions[0] if len(trace.agent_versions) == 1 else None,
        "config_hash": (
            trace.configuration_hashes[0] if len(trace.configuration_hashes) == 1 else None
        ),
    }


def _marker_is_set(value: Any) -> bool:
    if value in (None, "", False, 0):
        return False
    if isinstance(value, str) and value.strip().casefold() in {
        "false",
        "no",
        "none",
        "null",
        "0",
    }:
        return False
    return True


def _retry_attempt(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        attempt = int(value)
    except (TypeError, ValueError):
        return None
    return attempt if attempt >= 0 else None


def _set_if_present(payload: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        payload[key] = value


def _parse_normalized_timestamp(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sum_optional(values: Iterable[int | float | None]) -> int | float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    total = sum(present)
    return round(total, 12) if isinstance(total, float) else total
