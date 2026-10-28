from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

TERMINAL_STATUSES = {"completed", "failed"}
SUPPORTED_NORMALIZED_EVENT_TYPES = {
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
    "unknown",
}


def parse_timestamp(value: str) -> datetime:
    if value.endswith("Z"):
        value = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp must include a UTC offset or `Z`")
    return parsed.astimezone(timezone.utc)


def to_dict(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return to_dict(asdict(value))
    if isinstance(value, list):
        return [to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: to_dict(item) for key, item in value.items()}
    if isinstance(value, Decimal):
        return float(value)
    return value


def _require_non_empty_string(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"`{field_name}` must be a non-empty string")


def _require_optional_string(value: Any, field_name: str) -> None:
    if value is not None:
        _require_non_empty_string(value, field_name)


def _require_optional_non_negative_number(value: Any, field_name: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise TypeError(f"`{field_name}` must be a non-negative number or null")
    if not math.isfinite(float(value)) or value < 0:
        raise ValueError(f"`{field_name}` must be finite and greater than or equal to zero")


def _require_optional_non_negative_int(value: Any, field_name: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"`{field_name}` must be a non-negative integer or null")
    if value < 0:
        raise ValueError(f"`{field_name}` must be greater than or equal to zero")


@dataclass(slots=True)
class EvidenceRef:
    source_file: str
    source_line: int
    raw_event_id: str | None
    raw_event_hash: str
    captured_fields: dict[str, Any]
    raw_line_hash: str | None = None
    redacted_fields: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _require_non_empty_string(self.source_file, "source_file")
        if isinstance(self.source_line, bool) or not isinstance(self.source_line, int):
            raise TypeError("`source_line` must be a positive integer")
        if self.source_line < 1:
            raise ValueError("`source_line` must be a positive integer")
        _require_optional_string(self.raw_event_id, "raw_event_id")
        _require_non_empty_string(self.raw_event_hash, "raw_event_hash")
        _require_optional_string(self.raw_line_hash, "raw_line_hash")
        if not isinstance(self.captured_fields, dict):
            raise TypeError("`captured_fields` must be an object")
        if not isinstance(self.redacted_fields, list) or any(
            not isinstance(item, str) or not item for item in self.redacted_fields
        ):
            raise TypeError("`redacted_fields` must be a list of non-empty strings")


@dataclass(slots=True)
class NormalizedEvent:
    event_id: str
    workflow_family: str
    workflow_definition_id: str
    timestamp: str | None
    run_id: str | None
    trace_id: str | None
    event_type: str
    status: str | None
    step_name: str | None
    actor_type: str | None
    attempt_index: int | None
    human_intervention: bool
    observed_cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    agent_configuration_id: str | None = None
    agent_version: str | None = None
    configuration_hash: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    routing_decision: str | None = None
    handoff_target: str | None = None
    failure_mode: str | None = None
    quality_score: float | None = None
    business_success: bool | None = None
    human_effort_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    evidence: EvidenceRef | None = None
    missing_fields: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for field_name in (
            "event_id",
            "workflow_family",
            "workflow_definition_id",
            "event_type",
        ):
            _require_non_empty_string(getattr(self, field_name), field_name)
        for field_name in (
            "timestamp",
            "run_id",
            "trace_id",
            "status",
            "step_name",
            "actor_type",
            "agent_configuration_id",
            "agent_version",
            "configuration_hash",
            "model_name",
            "prompt_version",
            "tool_name",
            "tool_call_id",
            "routing_decision",
            "handoff_target",
            "failure_mode",
        ):
            _require_optional_string(getattr(self, field_name), field_name)
        _require_optional_non_negative_int(self.attempt_index, "attempt_index")
        _require_optional_non_negative_int(self.input_tokens, "input_tokens")
        _require_optional_non_negative_int(self.output_tokens, "output_tokens")
        _require_optional_non_negative_number(self.observed_cost_usd, "observed_cost_usd")
        _require_optional_non_negative_number(self.human_effort_seconds, "human_effort_seconds")
        _require_optional_non_negative_number(self.quality_score, "quality_score")
        if self.quality_score is not None and self.quality_score > 1:
            raise ValueError("`quality_score` must be between zero and one")
        if self.event_type not in SUPPORTED_NORMALIZED_EVENT_TYPES:
            raise ValueError(f"Unsupported normalized event type `{self.event_type}`")
        if not isinstance(self.human_intervention, bool):
            raise TypeError("`human_intervention` must be a boolean")
        if self.business_success is not None and not isinstance(self.business_success, bool):
            raise TypeError("`business_success` must be a boolean or null")
        if not isinstance(self.metadata, dict):
            raise TypeError("`metadata` must be an object")
        if not isinstance(self.missing_fields, list) or any(
            not isinstance(item, str) or not item for item in self.missing_fields
        ):
            raise TypeError("`missing_fields` must be a list of non-empty strings")

    def timestamp_dt(self) -> datetime:
        if self.timestamp is None:
            raise ValueError("Cannot parse a missing event timestamp")
        return parse_timestamp(self.timestamp)


@dataclass(slots=True)
class WorkflowRun:
    run_id: str
    workflow_family: str
    workflow_definition_id: str
    started_at: str | None
    ended_at: str | None
    status: str
    completion_state: str
    duration_seconds: float | None
    retry_count: int | None
    intervention_count: int | None
    observed_cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    agent_configuration_ids: list[str]
    agent_versions: list[str]
    configuration_hashes: list[str]
    model_names: list[str]
    prompt_versions: list[str]
    tool_call_count: int | None
    handoff_count: int | None
    routing_decision_count: int | None
    failure_modes: list[str]
    avg_quality_score: float | None
    step_count: int
    trace_ids: list[str]
    event_ids: list[str]
    evidence: list[EvidenceRef]
    missing_telemetry: list[str]
    unsupported_telemetry: list[str]
    events: list[NormalizedEvent]
    business_success: bool | None = None
    human_effort_seconds: float | None = None
    aggregation_semantics: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "run_id",
            "workflow_family",
            "workflow_definition_id",
            "status",
            "completion_state",
        ):
            _require_non_empty_string(getattr(self, field_name), field_name)
        _require_optional_string(self.started_at, "started_at")
        _require_optional_string(self.ended_at, "ended_at")
        for field_name in (
            "duration_seconds",
            "observed_cost_usd",
            "avg_quality_score",
            "human_effort_seconds",
        ):
            _require_optional_non_negative_number(getattr(self, field_name), field_name)
        if self.avg_quality_score is not None and self.avg_quality_score > 1:
            raise ValueError("`avg_quality_score` must be between zero and one")
        for field_name in (
            "retry_count",
            "intervention_count",
            "input_tokens",
            "output_tokens",
            "tool_call_count",
            "handoff_count",
            "routing_decision_count",
        ):
            _require_optional_non_negative_int(getattr(self, field_name), field_name)
        if isinstance(self.step_count, bool) or not isinstance(self.step_count, int):
            raise TypeError("`step_count` must be a non-negative integer")
        if self.step_count < 0:
            raise ValueError("`step_count` must be greater than or equal to zero")
        if self.business_success is not None and not isinstance(self.business_success, bool):
            raise TypeError("`business_success` must be a boolean or null")
        for field_name in (
            "agent_configuration_ids",
            "agent_versions",
            "configuration_hashes",
            "model_names",
            "prompt_versions",
            "failure_modes",
            "trace_ids",
            "event_ids",
            "missing_telemetry",
            "unsupported_telemetry",
        ):
            values = getattr(self, field_name)
            if not isinstance(values, list) or any(
                not isinstance(item, str) or not item for item in values
            ):
                raise TypeError(f"`{field_name}` must be a list of non-empty strings")
        if not isinstance(self.aggregation_semantics, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.aggregation_semantics.items()
        ):
            raise TypeError("`aggregation_semantics` must be an object of string values")


@dataclass(slots=True)
class RunScorecard:
    run_id: str
    observed_metrics: dict[str, Any]
    assumption_based_estimates: dict[str, Any]
    missing_telemetry: list[str]
    evidence: dict[str, Any]


@dataclass(slots=True)
class PilotScorecard:
    evaluation: dict[str, Any]
    workflow_family: str
    workflow_definition_id: str
    run_count: int
    measurement_window_summary: dict[str, Any]
    observed_summary: dict[str, Any]
    assumption_based_summary: dict[str, Any]
    comparator_summary: dict[str, Any]
    telemetry_validation: dict[str, Any]
    missing_telemetry_summary: dict[str, Any]
    evaluation_readiness: dict[str, Any]
    metric_statuses: dict[str, Any]
    runs: list[RunScorecard]
