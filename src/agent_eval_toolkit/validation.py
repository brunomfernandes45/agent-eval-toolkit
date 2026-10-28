from __future__ import annotations

import math
import re
from collections import defaultdict
from decimal import Decimal
from typing import Any

from .exceptions import ConfigurationError, DataValidationError
from .io_utils import RawJsonlRow, unpack_jsonl_row
from .models import SUPPORTED_NORMALIZED_EVENT_TYPES, WorkflowRun, parse_timestamp

REQUIRED_MANIFEST_FIELDS = {
    "evaluation_id",
    "evaluation_design",
    "measurement_window",
    "baseline",
    "comparator",
    "assumptions",
    "telemetry_requirements",
    "configuration",
    "taxonomy",
    "interpretation_boundaries",
}

REQUIRED_TAXONOMY_FIELDS = {
    "deployment_mode",
    "workflow_integration",
    "autonomy_level",
    "hitl_pattern",
    "statefulness",
    "memory_strategy",
    "composition",
    "decomposition_strategy",
    "allocation_mechanism",
    "routing_strategy",
    "protocol_surfaces",
}

SUPPORTED_EVIDENCE_MODES = {
    "anchor_hash_only",
    "anchor_hash_redacted_snapshot",
}

SUPPORTED_EVALUATION_DESIGNS = {
    "ab_test",
    "before_after",
    "monitoring",
    "scenario_suite",
    "staged_rollout",
}

SUPPORTED_SOURCE_KINDS = {
    "synthetic_fixture",
    "benchmark",
    "enterprise_operational",
    "canonical_prevalidated",
}

SUPPORTED_EVIDENCE_TIERS = {"mapping_only", "partial", "complete"}

SCHEMA_TELEMETRY_SIGNALS = {
    "business_success",
    "failure_modes",
    "failure_mode",
    "handoff_targets",
    "human_effort_seconds",
    "intervention_markers",
    "input_tokens",
    "observed_cost_usd",
    "output_tokens",
    "quality_score",
    "retry_markers",
    "routing_decisions",
    "tool_call_trace",
}

SUPPORTED_TELEMETRY_SIGNALS = {
    "agent_configuration_id",
    "agent_version",
    "business_success",
    "configuration_hash",
    "duration",
    "event_type",
    "failure_mode",
    "failure_modes",
    "handoff_targets",
    "human_effort_seconds",
    "input_tokens",
    "intervention_markers",
    "model_name",
    "observed_cost_usd",
    "output_tokens",
    "prompt_version",
    "quality_score",
    "retry_markers",
    "routing_decisions",
    "run_id",
    "status",
    "step_name",
    "timestamp",
    "tool_call_trace",
    "trace_id",
    "workflow_definition_id",
    "workflow_family",
}

SUPPORTED_TELEMETRY_CAPABILITIES = {
    "business_success",
    "failure_modes",
    "handoff_targets",
    "human_effort_seconds",
    "input_tokens",
    "intervention_markers",
    "observed_cost_usd",
    "output_tokens",
    "quality_score",
    "retry_markers",
    "routing_decisions",
    "tool_call_trace",
}

RUN_BOUNDARY_DEFAULTS = {
    "start_event_type": "run_started",
    "completed_event_type": "run_completed",
    "failed_event_type": "run_failed",
}

SEMANTIC_MAPPING_FIELDS = [
    "status_field",
    "attempt_field",
    "actor_field",
    "intervention_flag_field",
    "cost_field",
    "input_tokens_field",
    "output_tokens_field",
    "agent_configuration_id_field",
    "agent_version_field",
    "configuration_hash_field",
    "model_field",
    "prompt_version_field",
    "tool_name_field",
    "tool_call_id_field",
    "routing_decision_field",
    "handoff_target_field",
    "failure_mode_field",
    "quality_score_field",
    "business_success_field",
    "human_effort_seconds_field",
]

AGGREGATED_FIELD_MAPPINGS = {
    "input_tokens": "input_tokens_field",
    "output_tokens": "output_tokens_field",
    "observed_cost_usd": "cost_field",
}

SUPPORTED_AGGREGATION_SEMANTICS = {"per_event_increment"}

COMPARATIVE_EVALUATION_DESIGNS = {
    "ab_test",
    "before_after",
    "scenario_suite",
    "staged_rollout",
}

_PLACEHOLDER_PATTERN = re.compile(r"(?:\bTODO\b|TODO_|REPLACE_ME|\{\{[^{}]+\}\})", re.IGNORECASE)


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"`{field_name}` must be an object")
    return value


def _require_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"`{field_name}` must be a non-empty string")
    return value


def _require_string_list(value: Any, field_name: str, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise ConfigurationError(f"`{field_name}` must be a list of strings")
    cleaned: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ConfigurationError(f"`{field_name}[{index}]` must be a non-empty string")
        cleaned.append(item)
    if not allow_empty and not cleaned:
        raise ConfigurationError(f"`{field_name}` must contain at least one value")
    return cleaned


def _reject_unresolved_placeholders(value: Any, root_name: str) -> None:
    unresolved: list[str] = []

    def visit(item: Any, path: str) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                visit(nested, f"{path}.{key}")
        elif isinstance(item, list):
            for index, nested in enumerate(item):
                visit(nested, f"{path}[{index}]")
        elif isinstance(item, str) and _PLACEHOLDER_PATTERN.search(item):
            unresolved.append(path)

    visit(value, root_name)
    if unresolved:
        raise ConfigurationError(
            "Unresolved template placeholders are not allowed: " + ", ".join(unresolved)
        )


def validate_workflow_config(config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ConfigurationError("Workflow config must be a JSON object")
    _reject_unresolved_placeholders(config, "workflow_config")

    _require_string(config.get("workflow_family"), "workflow_family")
    _require_string(config.get("workflow_definition_id"), "workflow_definition_id")

    source_schema = _require_mapping(config.get("source_schema"), "source_schema")
    _require_string(source_schema.get("event_id_field"), "source_schema.event_id_field")
    _require_string(source_schema.get("timestamp_field"), "source_schema.timestamp_field")
    _require_string(source_schema.get("event_type_field"), "source_schema.event_type_field")

    event_type_map = _require_mapping(config.get("event_type_map"), "event_type_map")
    for raw_name, normalized_name in event_type_map.items():
        _require_string(raw_name, "event_type_map key")
        normalized_event_type = _require_string(normalized_name, f"event_type_map.{raw_name}")
        if normalized_event_type not in SUPPORTED_NORMALIZED_EVENT_TYPES:
            supported_values = sorted(SUPPORTED_NORMALIZED_EVENT_TYPES)
            raise ConfigurationError(
                f"`event_type_map.{raw_name}` must be one of {supported_values}"
            )

    run_boundaries = _require_mapping(config.get("run_boundaries"), "run_boundaries")
    mapped_event_types = set(event_type_map.values())
    for field_name, required_event_type in RUN_BOUNDARY_DEFAULTS.items():
        configured_event_type = _require_string(
            run_boundaries.get(field_name), f"run_boundaries.{field_name}"
        )
        if configured_event_type != required_event_type:
            raise ConfigurationError(
                f"`run_boundaries.{field_name}` must be `{required_event_type}`"
            )
        if configured_event_type not in mapped_event_types:
            raise ConfigurationError(
                f"`run_boundaries.{field_name}` is not produced by `event_type_map`"
            )

    _require_string_list(config.get("run_id_keys"), "run_id_keys")
    _require_string_list(config.get("trace_id_keys", []), "trace_id_keys", allow_empty=True)
    _require_string_list(config.get("step_name_keys", []), "step_name_keys", allow_empty=True)
    _require_string_list(
        config.get("capture_raw_fields", []), "capture_raw_fields", allow_empty=True
    )
    _require_string_list(config.get("metadata_fields", []), "metadata_fields", allow_empty=True)
    _require_string_list(
        config.get("allowed_metadata_fields", []), "allowed_metadata_fields", allow_empty=True
    )
    _require_string_list(config.get("restricted_fields", []), "restricted_fields", allow_empty=True)
    _require_string_list(config.get("redact_raw_fields", []), "redact_raw_fields", allow_empty=True)
    evidence_mode = config.get("evidence_mode", "anchor_hash_redacted_snapshot")
    if evidence_mode not in SUPPORTED_EVIDENCE_MODES:
        supported_values = sorted(SUPPORTED_EVIDENCE_MODES)
        raise ConfigurationError(f"`evidence_mode` must be one of {supported_values}")

    for field_name in SEMANTIC_MAPPING_FIELDS:
        value = config.get(field_name)
        if value is not None:
            _require_string(value, field_name)

    _declared_telemetry_capabilities(config)
    _validate_capability_instrumentation(config)
    _validate_aggregation_semantics(config)
    _validate_redaction_policy(config)
    _validate_restricted_semantic_mappings(config)

    return config


def _declared_telemetry_capabilities(config: dict[str, Any]) -> set[str]:
    raw_capabilities = config.get("telemetry_capabilities", [])
    if isinstance(raw_capabilities, list):
        capabilities = _require_string_list(
            raw_capabilities, "telemetry_capabilities", allow_empty=True
        )
        if len(set(capabilities)) != len(capabilities):
            raise ConfigurationError("`telemetry_capabilities` must not contain duplicates")
        declared = set(capabilities)
    elif isinstance(raw_capabilities, dict):
        declared = set()
        for name, enabled in raw_capabilities.items():
            _require_string(name, "telemetry_capabilities key")
            if not isinstance(enabled, bool):
                raise ConfigurationError(f"`telemetry_capabilities.{name}` must be a boolean")
            if enabled:
                declared.add(name)
        unknown_keys = set(raw_capabilities) - SUPPORTED_TELEMETRY_CAPABILITIES
        if unknown_keys:
            raise ConfigurationError(
                "Unsupported telemetry capabilities: " + ", ".join(sorted(unknown_keys))
            )
    else:
        raise ConfigurationError("`telemetry_capabilities` must be a list or object")

    unknown = declared - SUPPORTED_TELEMETRY_CAPABILITIES
    if unknown:
        raise ConfigurationError(
            "Unsupported telemetry capabilities: " + ", ".join(sorted(unknown))
        )
    return declared


def _validate_capability_instrumentation(config: dict[str, Any]) -> None:
    capabilities = _declared_telemetry_capabilities(config)
    event_types = set(config["event_type_map"].values())
    supported_by_mapping = {
        "business_success": bool(config.get("business_success_field")),
        "failure_modes": bool(config.get("failure_mode_field")),
        "handoff_targets": bool(config.get("handoff_target_field") or "handoff" in event_types),
        "human_effort_seconds": bool(config.get("human_effort_seconds_field")),
        "input_tokens": bool(config.get("input_tokens_field")),
        "intervention_markers": bool(
            config.get("intervention_flag_field") or "intervention" in event_types
        ),
        "observed_cost_usd": bool(config.get("cost_field")),
        "output_tokens": bool(config.get("output_tokens_field")),
        "quality_score": bool(config.get("quality_score_field")),
        "retry_markers": bool(config.get("attempt_field") or "retry" in event_types),
        "routing_decisions": bool(
            config.get("routing_decision_field") or "routing_decision" in event_types
        ),
        "tool_call_trace": bool(
            config.get("tool_name_field")
            or config.get("tool_call_id_field")
            or "tool_call" in event_types
        ),
    }
    unsupported = sorted(
        capability for capability in capabilities if not supported_by_mapping.get(capability, False)
    )
    if unsupported:
        raise ConfigurationError(
            "Telemetry capabilities lack a source field or event mapping: " + ", ".join(unsupported)
        )


def _validate_aggregation_semantics(config: dict[str, Any]) -> None:
    raw_semantics = config.get("aggregation_semantics")
    mapped_signals = {
        signal
        for signal, mapping_field in AGGREGATED_FIELD_MAPPINGS.items()
        if config.get(mapping_field)
    }
    if not mapped_signals and raw_semantics is None:
        return
    semantics = _require_mapping(raw_semantics, "aggregation_semantics")
    unknown_signals = sorted(set(semantics) - set(AGGREGATED_FIELD_MAPPINGS))
    if unknown_signals:
        raise ConfigurationError(
            "`aggregation_semantics` has unsupported signals: " + ", ".join(unknown_signals)
        )
    missing_signals = sorted(mapped_signals - set(semantics))
    if missing_signals:
        raise ConfigurationError(
            "`aggregation_semantics` must explicitly declare mapped signals: "
            + ", ".join(missing_signals)
        )
    for signal, semantic in semantics.items():
        normalized_semantic = _require_string(semantic, f"aggregation_semantics.{signal}")
        if normalized_semantic not in SUPPORTED_AGGREGATION_SEMANTICS:
            raise ConfigurationError(
                f"`aggregation_semantics.{signal}` must be `per_event_increment`; "
                "cumulative snapshots cannot be summed safely"
            )


def _validate_restricted_semantic_mappings(config: dict[str, Any]) -> None:
    restricted_fields = set(config.get("restricted_fields", []))
    source_schema = config["source_schema"]
    semantic_fields = {
        source_schema["event_id_field"],
        source_schema["timestamp_field"],
        source_schema["event_type_field"],
        *config.get("run_id_keys", []),
        *config.get("trace_id_keys", []),
        *config.get("step_name_keys", []),
    }
    semantic_fields.update(
        config[field_name] for field_name in SEMANTIC_MAPPING_FIELDS if config.get(field_name)
    )
    unsafe_fields = sorted(restricted_fields & semantic_fields)
    if unsafe_fields:
        raise ConfigurationError(
            "Restricted fields cannot be mapped into canonical semantic fields: "
            + ", ".join(unsafe_fields)
        )


def _validate_redaction_policy(config: dict[str, Any]) -> None:
    metadata_fields = set(config.get("metadata_fields", []))
    allowed_metadata_fields = set(config.get("allowed_metadata_fields", []))
    if (
        metadata_fields
        and allowed_metadata_fields
        and not metadata_fields <= allowed_metadata_fields
    ):
        invalid_fields = sorted(metadata_fields - allowed_metadata_fields)
        raise ConfigurationError(
            "`metadata_fields` contains fields outside `allowed_metadata_fields`: "
            f"{', '.join(invalid_fields)}"
        )

    restricted_fields = set(config.get("restricted_fields", []))
    redact_raw_fields = set(config.get("redact_raw_fields", []))
    unprotected_fields = sorted(restricted_fields - redact_raw_fields)
    if unprotected_fields:
        raise ConfigurationError(
            "Restricted fields must be listed in "
            f"`redact_raw_fields`: {', '.join(unprotected_fields)}"
        )


def validate_baseline_context(baseline_context: dict[str, Any] | None) -> dict[str, Any]:
    if baseline_context is None:
        return {}
    if not isinstance(baseline_context, dict):
        raise ConfigurationError("Baseline context or assumptions must be a JSON object")
    _reject_unresolved_placeholders(baseline_context, "baseline_context")

    normalized: dict[str, Any] = {}
    numeric_fields = [
        "manual_baseline_cycle_time_seconds",
        "manual_baseline_human_effort_seconds",
        "agent_human_effort_seconds",
        # Deprecated compatibility input. It is cycle time only and must never be
        # used as a proxy for human effort.
        "manual_baseline_duration_seconds",
        "loaded_labor_rate_usd_per_hour",
        "overhead_cost_usd_per_run",
        "tooling_cost_usd_per_run",
    ]
    for field_name in numeric_fields:
        raw_value = baseline_context.get(field_name)
        if raw_value is None:
            continue
        try:
            numeric_value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(f"`{field_name}` must be numeric") from exc
        if not math.isfinite(numeric_value):
            raise ConfigurationError(f"`{field_name}` must be finite")
        if numeric_value < 0:
            raise ConfigurationError(f"`{field_name}` must be greater than or equal to zero")
        normalized[field_name] = numeric_value

    sensitivity = baseline_context.get("sensitivity")
    if sensitivity is not None:
        normalized["sensitivity"] = _validate_sensitivity(sensitivity)

    for key, value in baseline_context.items():
        if key not in numeric_fields and key != "sensitivity":
            normalized[key] = value
    return normalized


def _validate_sensitivity(value: Any) -> dict[str, dict[str, float]]:
    sensitivity = _require_mapping(value, "sensitivity")
    normalized: dict[str, dict[str, float]] = {}
    for field_name, bounds in sensitivity.items():
        _require_string(field_name, "sensitivity key")
        bounds_mapping = _require_mapping(bounds, f"sensitivity.{field_name}")
        normalized_bounds: dict[str, float] = {}
        for bound_name in ("low", "high"):
            if bound_name not in bounds_mapping:
                raise ConfigurationError(f"`sensitivity.{field_name}.{bound_name}` is required")
            raw_value = bounds_mapping[bound_name]
            try:
                numeric_value = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise ConfigurationError(
                    f"`sensitivity.{field_name}.{bound_name}` must be numeric"
                ) from exc
            if not math.isfinite(numeric_value):
                raise ConfigurationError(f"`sensitivity.{field_name}.{bound_name}` must be finite")
            if numeric_value < 0:
                raise ConfigurationError(
                    f"`sensitivity.{field_name}.{bound_name}` must be greater than or equal to zero"
                )
            normalized_bounds[bound_name] = numeric_value
        if normalized_bounds["low"] > normalized_bounds["high"]:
            raise ConfigurationError(
                f"`sensitivity.{field_name}.low` must be less than or equal to high"
            )
        normalized[field_name] = normalized_bounds
    return normalized


def validate_evaluation_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ConfigurationError("Evaluation manifest must be a JSON object")
    _reject_unresolved_placeholders(manifest, "evaluation_manifest")

    missing_fields = sorted(REQUIRED_MANIFEST_FIELDS - set(manifest))
    if missing_fields:
        joined = ", ".join(missing_fields)
        raise ConfigurationError(f"Evaluation manifest is missing required fields: {joined}")

    normalized = dict(manifest)
    _require_string(normalized.get("evaluation_id"), "evaluation_id")
    evaluation_design = _require_string(normalized.get("evaluation_design"), "evaluation_design")
    if evaluation_design not in SUPPORTED_EVALUATION_DESIGNS:
        supported_values = sorted(SUPPORTED_EVALUATION_DESIGNS)
        raise ConfigurationError(f"`evaluation_design` must be one of {supported_values}")

    measurement_window = _require_mapping(
        normalized.get("measurement_window"), "measurement_window"
    )
    window_start = normalize_timestamp(measurement_window.get("start"), "measurement_window.start")
    window_end = normalize_timestamp(measurement_window.get("end"), "measurement_window.end")
    if parse_timestamp(window_end) <= parse_timestamp(window_start):
        raise ConfigurationError(
            "`measurement_window.end` must be later than `measurement_window.start`"
        )
    normalized["measurement_window"] = {
        "start": window_start,
        "end": window_end,
        "timezone": _require_string(
            measurement_window.get("timezone"), "measurement_window.timezone"
        ),
    }

    baseline = _require_mapping(normalized.get("baseline"), "baseline")
    _require_string(baseline.get("name"), "baseline.name")
    _require_string(baseline.get("type"), "baseline.type")
    _require_string(baseline.get("definition"), "baseline.definition")

    comparator = _require_mapping(normalized.get("comparator"), "comparator")
    _require_string(comparator.get("name"), "comparator.name")
    _require_string(comparator.get("type"), "comparator.type")
    _require_string(comparator.get("definition"), "comparator.definition")

    normalized["assumptions"] = validate_baseline_context(
        _require_mapping(normalized.get("assumptions"), "assumptions")
    )

    telemetry_requirements = _require_mapping(
        normalized.get("telemetry_requirements"), "telemetry_requirements"
    )
    required_signals = _require_string_list(
        telemetry_requirements.get("required"), "telemetry_requirements.required"
    )
    optional_signals = _require_string_list(
        telemetry_requirements.get("optional", []),
        "telemetry_requirements.optional",
        allow_empty=True,
    )
    all_signals = required_signals + optional_signals
    duplicate_signals = sorted(
        signal for signal in set(all_signals) if all_signals.count(signal) > 1
    )
    if duplicate_signals:
        raise ConfigurationError(
            "Telemetry signals must be unique across required and optional lists: "
            + ", ".join(duplicate_signals)
        )
    unknown_signals = sorted(set(all_signals) - SUPPORTED_TELEMETRY_SIGNALS)
    if unknown_signals:
        raise ConfigurationError(
            "Unsupported telemetry requirements: " + ", ".join(unknown_signals)
        )
    normalized["telemetry_requirements"] = {
        "required": required_signals,
        "optional": optional_signals,
    }

    evidence_context = normalized.get("evidence_context")
    if evidence_context is None:
        searchable = " ".join(
            str(value).lower()
            for value in (
                normalized.get("evidence_scope", ""),
                normalized.get("data_provenance", ""),
                normalized.get("evaluation_id", ""),
                *normalized.get("interpretation_boundaries", []),
            )
        )
        source_kind = (
            "synthetic_fixture"
            if "synthetic" in searchable
            else "benchmark" if "benchmark" in searchable else "canonical_prevalidated"
        )
        normalized["evidence_context"] = {
            "source_kind": source_kind,
            "mapping_assessment_required": False,
            "legacy_default_applied": True,
        }
    else:
        context = _require_mapping(evidence_context, "evidence_context")
        source_kind = _require_string(context.get("source_kind"), "evidence_context.source_kind")
        if source_kind not in SUPPORTED_SOURCE_KINDS:
            raise ConfigurationError(
                "`evidence_context.source_kind` must be one of "
                + str(sorted(SUPPORTED_SOURCE_KINDS))
            )
        mapping_required = context.get("mapping_assessment_required")
        if not isinstance(mapping_required, bool):
            raise ConfigurationError(
                "`evidence_context.mapping_assessment_required` must be a boolean"
            )
        normalized["evidence_context"] = {
            "source_kind": source_kind,
            "mapping_assessment_required": mapping_required,
            "legacy_default_applied": False,
        }

    publication_policy = normalized.get("publication_policy")
    if publication_policy is None:
        normalized["publication_policy"] = {
            "minimum_evidence_tier": "complete",
            "legacy_default_applied": True,
        }
    else:
        policy = _require_mapping(publication_policy, "publication_policy")
        minimum_tier = _require_string(
            policy.get("minimum_evidence_tier"),
            "publication_policy.minimum_evidence_tier",
        )
        if minimum_tier not in SUPPORTED_EVIDENCE_TIERS:
            raise ConfigurationError(
                "`publication_policy.minimum_evidence_tier` must be one of "
                + str(sorted(SUPPORTED_EVIDENCE_TIERS))
            )
        normalized["publication_policy"] = {
            "minimum_evidence_tier": minimum_tier,
            "legacy_default_applied": False,
        }

    configuration = _require_mapping(normalized.get("configuration"), "configuration")
    _require_string(
        configuration.get("agent_configuration_id"), "configuration.agent_configuration_id"
    )
    _require_string(configuration.get("agent_version"), "configuration.agent_version")
    _require_string(configuration.get("configuration_hash"), "configuration.configuration_hash")
    normalized["expected_configurations"] = _validate_expected_configurations(
        normalized.get("expected_configurations", [])
    )
    quality_gate = normalized.get("quality_gate")
    if quality_gate is not None:
        normalized["quality_gate"] = _validate_quality_gate(quality_gate)
    taxonomy = _require_mapping(normalized.get("taxonomy"), "taxonomy")
    normalized["taxonomy"] = _validate_taxonomy(taxonomy)

    normalized["interpretation_boundaries"] = _require_string_list(
        normalized.get("interpretation_boundaries"), "interpretation_boundaries"
    )
    normalized["inclusion_rules"] = _require_string_list(
        normalized.get("inclusion_rules", []), "inclusion_rules", allow_empty=True
    )
    normalized["exclusion_rules"] = _require_string_list(
        normalized.get("exclusion_rules", []), "exclusion_rules", allow_empty=True
    )
    normalized["run_exclusions"] = _validate_run_exclusions(normalized.get("run_exclusions", []))
    normalized["concurrent_changes"] = _require_string_list(
        normalized.get("concurrent_changes", []), "concurrent_changes", allow_empty=True
    )
    onboarding = normalized.get("onboarding")
    if onboarding is not None:
        normalized["onboarding"] = _validate_onboarding(onboarding)
    return normalized


def _validate_quality_gate(value: Any) -> dict[str, Any]:
    gate = dict(_require_mapping(value, "quality_gate"))
    approved = gate.get("approved")
    if not isinstance(approved, bool):
        raise ConfigurationError("`quality_gate.approved` must be a boolean")
    threshold = gate.get("minimum_score", gate.get("threshold"))
    if isinstance(threshold, bool) or not isinstance(threshold, int | float | Decimal):
        raise ConfigurationError(
            "`quality_gate.minimum_score` must be an explicit numeric threshold"
        )
    numeric_threshold = float(threshold)
    if not math.isfinite(numeric_threshold) or not 0 <= numeric_threshold <= 1:
        raise ConfigurationError("`quality_gate.minimum_score` must be between zero and one")
    gate.pop("threshold", None)
    gate["minimum_score"] = numeric_threshold
    return gate


def _validate_expected_configurations(value: Any) -> list[dict[str, Any]]:
    raw_items: list[tuple[str | None, Any]]
    if isinstance(value, dict):
        raw_items = [(str(group), item) for group, item in value.items()]
    elif isinstance(value, list):
        raw_items = [(None, item) for item in value]
    else:
        raise ConfigurationError("`expected_configurations` must be a list or object")

    normalized: list[dict[str, Any]] = []
    seen_identities: set[tuple[str, str, str]] = set()
    seen_groups: set[str] = set()
    allowed_fields = {
        "group",
        "name",
        "agent_configuration_id",
        "agent_version",
        "configuration_hash",
        "required",
    }
    for index, (mapping_group, item) in enumerate(raw_items):
        path = f"expected_configurations[{index}]"
        expected = _require_mapping(item, path)
        extra_fields = sorted(set(expected) - allowed_fields)
        if extra_fields:
            raise ConfigurationError(f"`{path}` has unsupported fields: " + ", ".join(extra_fields))
        config_id = _require_string(
            expected.get("agent_configuration_id"), f"{path}.agent_configuration_id"
        )
        version = _require_string(expected.get("agent_version"), f"{path}.agent_version")
        config_hash = _require_string(
            expected.get("configuration_hash"), f"{path}.configuration_hash"
        )
        required = expected.get("required", True)
        if not isinstance(required, bool):
            raise ConfigurationError(f"`{path}.required` must be a boolean")
        group_value = mapping_group or expected.get("group") or expected.get("name") or config_id
        group = _require_string(group_value, f"{path}.group")
        identity = (config_id, version, config_hash)
        if identity in seen_identities:
            raise ConfigurationError(f"Duplicate expected configuration identity in `{path}`")
        if group in seen_groups:
            raise ConfigurationError(f"Duplicate expected configuration group `{group}`")
        seen_identities.add(identity)
        seen_groups.add(group)
        normalized.append(
            {
                "group": group,
                "agent_configuration_id": config_id,
                "agent_version": version,
                "configuration_hash": config_hash,
                "required": required,
            }
        )
    return normalized


def _validate_onboarding(value: Any) -> dict[str, Any]:
    onboarding = _require_mapping(value, "onboarding")
    normalized = dict(onboarding)
    source = _require_string(normalized.get("source"), "onboarding.source")
    supported_sources = {  # pragma: no cover -- optional adapter registration
        "offline_langfuse_export",
        "opentelemetry_otlp_json_logs",
        "prevalidated_canonical",
    }
    if source not in supported_sources:
        raise ConfigurationError(f"`onboarding.source` must be one of {sorted(supported_sources)}")
    if normalized.get("status_mapping_approved") is not True:
        raise ConfigurationError("Business-status mapping must be approved before evaluation")
    if normalized.get("trace_as_workflow_run_approved") is not True:
        raise ConfigurationError("trace-as-workflow-run review must be approved before evaluation")
    if normalized.get("enterprise_evaluation_gate") != "approved":
        raise ConfigurationError("Enterprise evaluation gate is not approved")
    _require_string(normalized.get("trace_audit_file"), "onboarding.trace_audit_file")
    _require_string(normalized.get("reviewer"), "onboarding.reviewer")
    _require_string(normalized.get("review_evidence"), "onboarding.review_evidence")
    return normalized


def _validate_run_exclusions(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ConfigurationError("`run_exclusions` must be a list of objects")
    normalized: list[dict[str, str]] = []
    seen_run_ids: set[str] = set()
    for index, item in enumerate(value):
        exclusion = _require_mapping(item, f"run_exclusions[{index}]")
        run_id = _require_string(exclusion.get("run_id"), f"run_exclusions[{index}].run_id")
        reason = _require_string(exclusion.get("reason"), f"run_exclusions[{index}].reason")
        extra_fields = sorted(set(exclusion) - {"run_id", "reason"})
        if extra_fields:
            raise ConfigurationError(
                f"`run_exclusions[{index}]` has unsupported fields: " + ", ".join(extra_fields)
            )
        if run_id in seen_run_ids:
            raise ConfigurationError(f"Duplicate `run_exclusions` entry for run `{run_id}`")
        seen_run_ids.add(run_id)
        normalized.append({"run_id": run_id, "reason": reason})
    return normalized


def _validate_taxonomy(taxonomy: dict[str, Any]) -> dict[str, Any]:
    missing_fields = sorted(REQUIRED_TAXONOMY_FIELDS - set(taxonomy))
    if missing_fields:
        joined = ", ".join(missing_fields)
        raise ConfigurationError(f"Taxonomy is missing required fields: {joined}")

    normalized = dict(taxonomy)
    for field_name in REQUIRED_TAXONOMY_FIELDS - {"protocol_surfaces"}:
        _require_string(normalized.get(field_name), f"taxonomy.{field_name}")
    normalized["protocol_surfaces"] = _require_string_list(
        normalized.get("protocol_surfaces"), "taxonomy.protocol_surfaces", allow_empty=True
    )
    return normalized


def validate_canonical_rows(
    raw_rows: list[RawJsonlRow], workflow_config: dict[str, Any]
) -> dict[str, Any]:
    validate_workflow_config(workflow_config)
    source_schema = workflow_config["source_schema"]
    required_field_groups = {
        "event_id": [source_schema["event_id_field"]],
        "timestamp": [source_schema["timestamp_field"]],
        "event_type": [source_schema["event_type_field"]],
        "run_id": workflow_config["run_id_keys"],
        "trace_id": workflow_config.get("trace_id_keys", []),
        "step_name": workflow_config.get("step_name_keys", []),
    }

    missing_by_signal: dict[str, int] = {}
    invalid_by_signal: dict[str, int] = {}
    rows_with_missing_fields: list[dict[str, Any]] = []
    rows_with_invalid_fields: list[dict[str, Any]] = []
    event_id_lines: dict[str, list[int]] = defaultdict(list)
    event_id_field = source_schema["event_id_field"]
    timestamp_field = source_schema["timestamp_field"]
    event_type_field = source_schema["event_type_field"]
    event_type_map = workflow_config["event_type_map"]
    semantic_string_fields: dict[str, list[str]] = {
        "run_id": list(workflow_config.get("run_id_keys", [])),
        "trace_id": list(workflow_config.get("trace_id_keys", [])),
        "step_name": list(workflow_config.get("step_name_keys", [])),
        "status": [workflow_config["status_field"]] if workflow_config.get("status_field") else [],
        "actor_type": (
            [workflow_config["actor_field"]] if workflow_config.get("actor_field") else []
        ),
        "agent_configuration_id": (
            [workflow_config["agent_configuration_id_field"]]
            if workflow_config.get("agent_configuration_id_field")
            else []
        ),
        "agent_version": (
            [workflow_config["agent_version_field"]]
            if workflow_config.get("agent_version_field")
            else []
        ),
        "configuration_hash": (
            [workflow_config["configuration_hash_field"]]
            if workflow_config.get("configuration_hash_field")
            else []
        ),
        "model_name": (
            [workflow_config["model_field"]] if workflow_config.get("model_field") else []
        ),
        "prompt_version": (
            [workflow_config["prompt_version_field"]]
            if workflow_config.get("prompt_version_field")
            else []
        ),
        "tool_name": (
            [workflow_config["tool_name_field"]] if workflow_config.get("tool_name_field") else []
        ),
        "tool_call_id": (
            [workflow_config["tool_call_id_field"]]
            if workflow_config.get("tool_call_id_field")
            else []
        ),
        "routing_decision": (
            [workflow_config["routing_decision_field"]]
            if workflow_config.get("routing_decision_field")
            else []
        ),
        "handoff_target": (
            [workflow_config["handoff_target_field"]]
            if workflow_config.get("handoff_target_field")
            else []
        ),
        "failure_mode": (
            [workflow_config["failure_mode_field"]]
            if workflow_config.get("failure_mode_field")
            else []
        ),
    }

    for row in raw_rows:
        line_number, payload, _raw_line = unpack_jsonl_row(row)
        if not isinstance(payload, dict):
            rows_with_invalid_fields.append(
                {"source_line": line_number, "invalid_signals": ["row_shape"]}
            )
            invalid_by_signal["row_shape"] = invalid_by_signal.get("row_shape", 0) + 1
            continue

        row_missing: list[str] = []
        row_invalid: list[str] = []
        for signal, field_names in required_field_groups.items():
            if field_names and not any(
                payload.get(field_name) not in (None, "") for field_name in field_names
            ):
                row_missing.append(signal)
                missing_by_signal[signal] = missing_by_signal.get(signal, 0) + 1
        if row_missing:
            rows_with_missing_fields.append(
                {"source_line": line_number, "missing_signals": sorted(row_missing)}
            )

        raw_event_id = payload.get(event_id_field)
        if raw_event_id not in (None, ""):
            if not isinstance(raw_event_id, str) or not raw_event_id.strip():
                row_invalid.append("event_id")
            else:
                event_id_lines[raw_event_id].append(line_number)

        for signal, field_names in semantic_string_fields.items():
            for field_name in field_names:
                raw_value = payload.get(field_name)
                if raw_value in (None, ""):
                    continue
                if not isinstance(raw_value, str) or not raw_value.strip():
                    row_invalid.append(signal)

        raw_timestamp = payload.get(timestamp_field)
        if raw_timestamp not in (None, ""):
            try:
                normalize_timestamp(raw_timestamp, timestamp_field)
            except DataValidationError:
                row_invalid.append("timestamp")

        raw_event_type = payload.get(event_type_field)
        if raw_event_type not in (None, ""):
            mapped_event_type = (
                event_type_map.get(raw_event_type) if isinstance(raw_event_type, str) else None
            )
            if mapped_event_type is None:
                row_invalid.append("event_type")

        numeric_fields = {
            "attempt_index": (workflow_config.get("attempt_field"), "int"),
            "observed_cost_usd": (workflow_config.get("cost_field"), "float"),
            "input_tokens": (workflow_config.get("input_tokens_field"), "int"),
            "output_tokens": (workflow_config.get("output_tokens_field"), "int"),
            "quality_score": (workflow_config.get("quality_score_field"), "float"),
            "human_effort_seconds": (
                workflow_config.get("human_effort_seconds_field"),
                "float",
            ),
        }
        for signal, (numeric_field_name, numeric_type) in numeric_fields.items():
            if not numeric_field_name or payload.get(numeric_field_name) is None:
                continue
            try:
                if numeric_type == "int":
                    coerce_optional_int(
                        payload[numeric_field_name], numeric_field_name, str(raw_event_id)
                    )
                else:
                    numeric_value = coerce_optional_float(
                        payload[numeric_field_name], numeric_field_name, str(raw_event_id)
                    )
                    if (
                        signal == "quality_score"
                        and numeric_value is not None
                        and numeric_value > 1
                    ):
                        raise DataValidationError(
                            f"`{numeric_field_name}` must be between zero and one "
                            f"for event `{raw_event_id}`"
                        )
            except DataValidationError:
                row_invalid.append(signal)

        for signal, boolean_field_name in (
            ("intervention_markers", workflow_config.get("intervention_flag_field")),
            ("business_success", workflow_config.get("business_success_field")),
        ):
            if not boolean_field_name or payload.get(boolean_field_name) is None:
                continue
            try:
                coerce_optional_bool(
                    payload[boolean_field_name], boolean_field_name, str(raw_event_id)
                )
            except DataValidationError:
                row_invalid.append(signal)

        if row_invalid:
            unique_invalid = sorted(set(row_invalid))
            rows_with_invalid_fields.append(
                {"source_line": line_number, "invalid_signals": unique_invalid}
            )
            for signal in unique_invalid:
                invalid_by_signal[signal] = invalid_by_signal.get(signal, 0) + 1

    duplicate_event_ids = [
        {"event_id": event_id, "source_lines": source_lines}
        for event_id, source_lines in sorted(event_id_lines.items())
        if len(source_lines) > 1
    ]

    return {
        "passed": not (rows_with_missing_fields or rows_with_invalid_fields or duplicate_event_ids),
        "row_count": len(raw_rows),
        "missing_by_signal": dict(sorted(missing_by_signal.items())),
        "invalid_by_signal": dict(sorted(invalid_by_signal.items())),
        "rows_with_missing_fields": rows_with_missing_fields,
        "rows_with_invalid_fields": rows_with_invalid_fields,
        "duplicate_event_ids": duplicate_event_ids,
    }


def validate_telemetry_requirements(
    runs: list[WorkflowRun],
    evaluation_manifest: dict[str, Any],
    workflow_config: dict[str, Any],
) -> dict[str, Any]:
    manifest = validate_evaluation_manifest(evaluation_manifest)
    validate_workflow_config(workflow_config)
    telemetry_requirements = manifest["telemetry_requirements"]
    required_signals = telemetry_requirements["required"]
    optional_signals = telemetry_requirements["optional"]

    missing_required_schema_signals = _missing_schema_signals(required_signals, workflow_config)
    missing_optional_schema_signals = _missing_schema_signals(optional_signals, workflow_config)

    missing_required_by_run = _missing_run_signals(
        runs,
        [signal for signal in required_signals if signal not in missing_required_schema_signals],
    )
    missing_optional_by_run = _missing_run_signals(
        runs,
        [signal for signal in optional_signals if signal not in missing_optional_schema_signals],
    )

    missing_required_summary = _count_missing_signals(
        missing_required_schema_signals, missing_required_by_run
    )
    missing_optional_summary = _count_missing_signals(
        missing_optional_schema_signals, missing_optional_by_run
    )

    if not runs:
        missing_required_summary = {
            **missing_required_summary,
            "workflow_runs": 1,
        }

    configuration_consistency = validate_configuration_drift(runs, manifest)
    if not configuration_consistency["passed"]:
        missing_required_summary = {
            **missing_required_summary,
            "configuration_drift": max(
                1,
                len(configuration_consistency["invalid_runs"])
                + len(configuration_consistency["missing_expected_configurations"]),
            ),
        }

    passed = (
        bool(runs)
        and not missing_required_schema_signals
        and not missing_required_by_run
        and configuration_consistency["passed"]
    )
    return {
        "passed": passed,
        "run_count": len(runs),
        "required_signals": required_signals,
        "optional_signals": optional_signals,
        "missing_required_schema_signals": missing_required_schema_signals,
        "missing_optional_schema_signals": missing_optional_schema_signals,
        "missing_required_by_run": missing_required_by_run,
        "missing_optional_by_run": missing_optional_by_run,
        "missing_required_summary": missing_required_summary,
        "missing_optional_summary": missing_optional_summary,
        "coverage_by_signal": _telemetry_coverage(
            runs, required_signals + optional_signals, workflow_config
        ),
        "configuration_consistency": configuration_consistency,
    }


def _missing_schema_signals(signals: list[str], workflow_config: dict[str, Any]) -> list[str]:
    return sorted(
        signal
        for signal in signals
        if signal in SCHEMA_TELEMETRY_SIGNALS
        and not _workflow_config_supports_signal(workflow_config, signal)
    )


def _workflow_config_supports_signal(workflow_config: dict[str, Any], signal: str) -> bool:
    capabilities = _declared_telemetry_capabilities(workflow_config)
    event_types = set(workflow_config["event_type_map"].values())
    if signal == "business_success":
        return "business_success" in capabilities and bool(
            workflow_config.get("business_success_field")
        )
    if signal == "human_effort_seconds":
        return "human_effort_seconds" in capabilities and bool(
            workflow_config.get("human_effort_seconds_field")
        )
    if signal in {"failure_mode", "failure_modes"}:
        return "failure_modes" in capabilities and bool(workflow_config.get("failure_mode_field"))
    if signal == "handoff_targets":
        return "handoff_targets" in capabilities and bool(
            workflow_config.get("handoff_target_field") or "handoff" in event_types
        )
    if signal == "intervention_markers":
        return "intervention_markers" in capabilities and bool(
            workflow_config.get("intervention_flag_field") or "intervention" in event_types
        )
    if signal == "retry_markers":
        return "retry_markers" in capabilities and bool(
            workflow_config.get("attempt_field") or "retry" in event_types
        )
    if signal == "routing_decisions":
        return "routing_decisions" in capabilities and bool(
            workflow_config.get("routing_decision_field") or "routing_decision" in event_types
        )
    if signal == "tool_call_trace":
        return "tool_call_trace" in capabilities and bool(
            workflow_config.get("tool_name_field")
            or workflow_config.get("tool_call_id_field")
            or "tool_call" in event_types
        )
    if signal == "input_tokens":
        return "input_tokens" in capabilities and bool(workflow_config.get("input_tokens_field"))
    if signal == "output_tokens":
        return "output_tokens" in capabilities and bool(workflow_config.get("output_tokens_field"))
    if signal == "observed_cost_usd":
        return "observed_cost_usd" in capabilities and bool(workflow_config.get("cost_field"))
    if signal == "quality_score":
        return "quality_score" in capabilities and bool(workflow_config.get("quality_score_field"))
    return True


def _missing_run_signals(runs: list[WorkflowRun], signals: list[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for run in runs:
        missing = sorted(signal for signal in signals if not _run_has_signal(run, signal))
        if missing:
            result[run.run_id] = missing
    return result


def _run_has_signal(run: WorkflowRun, signal: str) -> bool:
    if signal == "run_id":
        return bool(run.run_id and not run.run_id.startswith("missing-run-id::"))
    if signal == "timestamp":
        return run.started_at is not None and run.ended_at is not None
    if signal == "duration":
        return run.duration_seconds is not None
    if signal == "workflow_definition_id":
        return bool(run.workflow_definition_id)
    if signal == "workflow_family":
        return bool(run.workflow_family)
    if signal == "trace_id":
        return bool(run.trace_ids)
    if signal == "event_type":
        return "event_type_field" not in run.missing_telemetry and (
            "unknown_event_type_mapping" not in run.missing_telemetry
        )
    if signal == "status":
        return run.status != "incomplete"
    if signal == "step_name":
        return "step_name" not in run.missing_telemetry and run.step_count > 0
    if signal == "agent_configuration_id":
        return bool(run.agent_configuration_ids)
    if signal == "agent_version":
        return bool(run.agent_versions)
    if signal == "configuration_hash":
        return bool(run.configuration_hashes)
    if signal == "model_name":
        return bool(run.model_names)
    if signal == "prompt_version":
        return bool(run.prompt_versions)
    if signal == "business_success":
        return run.business_success is not None
    if signal == "human_effort_seconds":
        return run.human_effort_seconds is not None
    if signal == "input_tokens":
        return run.input_tokens is not None
    if signal == "output_tokens":
        return run.output_tokens is not None
    if signal == "observed_cost_usd":
        return run.observed_cost_usd is not None
    if signal == "quality_score":
        return run.avg_quality_score is not None
    if signal in {"failure_mode", "failure_modes"}:
        return run.status == "completed" or bool(run.failure_modes)
    if signal == "retry_markers":
        return run.retry_count is not None
    if signal == "intervention_markers":
        return run.intervention_count is not None
    if signal == "tool_call_trace":
        return run.tool_call_count is not None
    if signal == "handoff_targets":
        return run.handoff_count is not None
    if signal == "routing_decisions":
        return run.routing_decision_count is not None
    return True


def _telemetry_coverage(
    runs: list[WorkflowRun], signals: list[str], workflow_config: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    coverage: dict[str, dict[str, Any]] = {}
    for signal in signals:
        supported = _workflow_config_supports_signal(workflow_config, signal)
        runs_with_signal = (
            sum(1 for run in runs if _run_has_signal(run, signal)) if supported else 0
        )
        coverage[signal] = {
            "supported": supported,
            "runs_with_signal": runs_with_signal,
            "run_denominator": len(runs),
            "coverage": round(runs_with_signal / len(runs), 3) if runs else None,
        }
    return dict(sorted(coverage.items()))


def validate_configuration_drift(
    runs: list[WorkflowRun], evaluation_manifest: dict[str, Any]
) -> dict[str, Any]:
    """Validate stable run identities and any manifest-declared configuration matrix."""

    design = str(evaluation_manifest.get("evaluation_design", ""))
    raw_expected = evaluation_manifest.get("expected_configurations", [])
    expected_configurations = _validate_expected_configurations(raw_expected)
    if not expected_configurations:
        manifest_configuration = _require_mapping(
            evaluation_manifest.get("configuration"), "configuration"
        )
        expected_configurations = _validate_expected_configurations(
            [
                {
                    "group": "manifest_configuration",
                    "agent_configuration_id": manifest_configuration.get("agent_configuration_id"),
                    "agent_version": manifest_configuration.get("agent_version"),
                    "configuration_hash": manifest_configuration.get("configuration_hash"),
                    "required": True,
                }
            ]
        )
    expected_by_identity = {
        (
            item["agent_configuration_id"],
            item["agent_version"],
            item["configuration_hash"],
        ): item
        for item in expected_configurations
    }
    applicable = design in COMPARATIVE_EVALUATION_DESIGNS | {"monitoring"} or bool(
        expected_configurations
    )
    invalid_runs: list[dict[str, Any]] = []
    observed_identities: set[tuple[str, str, str]] = set()
    if applicable:
        configuration_fields = {
            "agent_configuration_id": "agent_configuration_ids",
            "agent_version": "agent_versions",
            "configuration_hash": "configuration_hashes",
        }
        for run in runs:
            issues: list[str] = []
            values_by_field: dict[str, list[str]] = {}
            for label, attribute_name in configuration_fields.items():
                values = list(getattr(run, attribute_name))
                values_by_field[label] = values
                if not values:
                    issues.append(f"missing_{label}")
                elif len(values) > 1:
                    issues.append(f"mixed_{label}")
            identity: tuple[str, str, str] | None = None
            if not issues:
                identity = (
                    values_by_field["agent_configuration_id"][0],
                    values_by_field["agent_version"][0],
                    values_by_field["configuration_hash"][0],
                )
                observed_identities.add(identity)
                if expected_by_identity and identity not in expected_by_identity:
                    issues.append("unexpected_configuration_identity")
            if issues:
                invalid_runs.append(
                    {
                        "run_id": run.run_id,
                        "issues": sorted(issues),
                        "values": values_by_field,
                        "event_ids": run.event_ids,
                        "trace_ids": run.trace_ids,
                        "observed_identity": list(identity) if identity else None,
                    }
                )

    missing_expected_configurations = [
        item
        for identity, item in expected_by_identity.items()
        if item["required"] and identity not in observed_identities
    ]
    return {
        "applicable": applicable,
        "evaluation_design": design,
        "passed": not invalid_runs and not missing_expected_configurations,
        "invalid_runs": invalid_runs,
        "expected_configurations": expected_configurations,
        "observed_configuration_identities": [
            {
                "agent_configuration_id": identity[0],
                "agent_version": identity[1],
                "configuration_hash": identity[2],
            }
            for identity in sorted(observed_identities)
        ],
        "missing_expected_configurations": missing_expected_configurations,
    }


def _count_missing_signals(
    missing_schema_signals: list[str], missing_by_run: dict[str, list[str]]
) -> dict[str, int]:
    counts = {signal: 1 for signal in missing_schema_signals}
    for missing in missing_by_run.values():
        for signal in missing:
            counts[signal] = counts.get(signal, 0) + 1
    return dict(sorted(counts.items()))


def normalize_timestamp(value: Any, field_name: str = "timestamp") -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError(f"`{field_name}` must be a non-empty ISO 8601 timestamp")
    try:
        parsed = parse_timestamp(value.strip())
    except ValueError as exc:
        raise DataValidationError(f"`{field_name}` must be a valid ISO 8601 timestamp") from exc
    return parsed.isoformat().replace("+00:00", "Z")


def coerce_optional_int(value: Any, field_name: str, event_id: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise DataValidationError(f"`{field_name}` must be a JSON integer for event `{event_id}`")
    if value < 0:
        raise DataValidationError(
            f"`{field_name}` must be greater than or equal to zero for event `{event_id}`"
        )
    return value


def coerce_optional_float(value: Any, field_name: str, event_id: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise DataValidationError(f"`{field_name}` must be a JSON number for event `{event_id}`")
    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise DataValidationError(f"`{field_name}` must be finite for event `{event_id}`")
    if numeric_value < 0:
        raise DataValidationError(
            f"`{field_name}` must be greater than or equal to zero for event `{event_id}`"
        )
    return numeric_value


def coerce_optional_bool(value: Any, field_name: str, event_id: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise DataValidationError(f"`{field_name}` must be a boolean for event `{event_id}`")
    return value


def coerce_optional_string(value: Any, field_name: str, event_id: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError(
            f"`{field_name}` must be a non-empty string for event `{event_id}`"
        )
    return value


def coerce_optional_quality_score(value: Any, field_name: str, event_id: str) -> float | None:
    score = coerce_optional_float(value, field_name, event_id)
    if score is not None and score > 1:
        raise DataValidationError(
            f"`{field_name}` must be between zero and one for event `{event_id}`"
        )
    return score
