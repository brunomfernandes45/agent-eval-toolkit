from __future__ import annotations

import re
from typing import Any, Iterable

DIAGNOSTIC_SCHEMA_VERSION = "1.0.0"

STABLE_DIAGNOSTIC_CODES = {
    "AGGREGATION_SEMANTICS_UNSUPPORTED",
    "BOUNDARY_AMBIGUOUS_TIMESTAMP",
    "BOUNDARY_CONFLICTING_TERMINAL",
    "BOUNDARY_DUPLICATE_START",
    "BOUNDARY_MISSING_START",
    "BOUNDARY_MISSING_TERMINAL",
    "BOUNDARY_POST_TERMINAL_EVENT",
    "CANONICAL_DUPLICATE_EVENT_ID",
    "CANONICAL_INVALID_IDENTIFIER",
    "CANONICAL_INVALID_TIMESTAMP",
    "CANONICAL_NEGATIVE_VALUE",
    "CANONICAL_UNSUPPORTED_EVENT_TYPE",
    "CONFIGURATION_DRIFT",
    "CONFIGURATION_UNEXPECTED",
    "CONFIG_UNRESOLVED_PLACEHOLDER",
    "EVIDENCE_MINIMUM_TIER_NOT_MET",
    "EVIDENCE_TIER_BLOCKED",
    "EVIDENCE_TIER_MAPPING_ONLY",
    "EVIDENCE_TIER_PARTIAL",
    "LANGFUSE_INCOMPLETE_PAGINATION",
    "LANGFUSE_STATUS_MAPPING_REQUIRED",
    "MANIFEST_INVALID_WINDOW",
    "MAPPING_ACTIVITY_EFFORT_INCOMPLETE",
    "MAPPING_APPROVAL_REQUIRED",
    "MAPPING_ASSESSMENT_REQUIRED",
    "MAPPING_CRITICAL_ISSUE_OPEN",
    "MAPPING_REQUIRED_SIGNAL_UNMAPPED",
    "MAPPING_SEMANTICS_UNRESOLVED",
    "MAPPING_SOURCE_PATH_UNOBSERVED",
    "MAPPING_TRANSFORMATION_UNTESTED",
    "PACKAGE_HASH_MISMATCH",
    "PIPELINE_STAGE_FAILED",
    "PRIVACY_RESTRICTED_MAPPING",
    "TELEMETRY_REQUIRED_SIGNAL_MISSING",
}


_EXCEPTION_CODE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("LANGFUSE_INCOMPLETE_PAGINATION", ("incomplete langfuse api capture", "non-null")),
    (
        "LANGFUSE_STATUS_MAPPING_REQUIRED",
        ("approved status mapping", "status mapping is required"),
    ),
    (
        "AGGREGATION_SEMANTICS_UNSUPPORTED",
        ("aggregation semantics", "per_event_increment", "cumulative"),
    ),
    (
        "MANIFEST_INVALID_WINDOW",
        ("measurement window", "window `end` must be later", "must be later than"),
    ),
    ("CONFIG_UNRESOLVED_PLACEHOLDER", ("unresolved template placeholder",)),
    (
        "PRIVACY_RESTRICTED_MAPPING",
        (
            "restricted fields cannot be mapped",
            "restricted fields must be listed",
            "not protected by redaction",
            "restricted canary",
        ),
    ),
    ("CANONICAL_DUPLICATE_EVENT_ID", ("duplicate event id",)),
    ("CANONICAL_UNSUPPORTED_EVENT_TYPE", ("unknown event type", "unsupported event type")),
    ("CANONICAL_INVALID_IDENTIFIER", ("missing a run id", "invalid identifier")),
    ("CANONICAL_INVALID_TIMESTAMP", ("invalid timestamp", "valid iso 8601 timestamp")),
    (
        "CANONICAL_NEGATIVE_VALUE",
        ("greater than or equal to zero", "cannot be negative", "finite and non-negative"),
    ),
    ("BOUNDARY_MISSING_START", ("missing its `run_started` boundary",)),
    ("BOUNDARY_MISSING_TERMINAL", ("missing a terminal boundary",)),
    ("BOUNDARY_DUPLICATE_START", ("duplicate `run_started` boundaries",)),
    (
        "BOUNDARY_CONFLICTING_TERMINAL",
        (
            "conflicting completed and failed terminal boundaries",
            "duplicate terminal boundaries",
            "terminal event/status conflict",
        ),
    ),
    ("BOUNDARY_POST_TERMINAL_EVENT", ("occurs after the terminal boundary",)),
    (
        "BOUNDARY_AMBIGUOUS_TIMESTAMP",
        (
            "boundary with a missing timestamp",
            "invalid boundary timestamp",
            "out-of-order run boundaries",
            "occurs before the start boundary",
        ),
    ),
    ("CONFIGURATION_DRIFT", ("configuration_drift", "configuration drift")),
    ("CONFIGURATION_UNEXPECTED", ("unexpected configuration",)),
    (
        "TELEMETRY_REQUIRED_SIGNAL_MISSING",
        ("minimum telemetry feasibility gate failed", "required telemetry"),
    ),
    ("PACKAGE_HASH_MISMATCH", ("package hash", "fingerprint mismatch", "hash mismatch")),
)


def make_diagnostic(
    code: str,
    message: str,
    stage: str,
    *,
    severity: str = "error",
    blocking_scope: str = "bundle",
    affected_outputs: list[str] | None = None,
    evidence_tier_impact: str | None = None,
    remediation_code: str | None = None,
    recommended_action: str | None = None,
    field: str | None = None,
    event_id: str | None = None,
    run_id: str | None = None,
    source_line: int | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if code not in STABLE_DIAGNOSTIC_CODES:
        raise ValueError(f"Unsupported stable diagnostic code `{code}`")
    if severity not in {"error", "warning", "info"}:
        raise ValueError("Diagnostic severity must be error, warning, or info")
    if blocking_scope not in {"bundle", "mapping", "run", "metric", "claim", "none"}:
        raise ValueError("Diagnostic blocking_scope is unsupported")
    if affected_outputs is None:
        affected_outputs = [] if blocking_scope == "none" else ["analytical_outputs"]
    if evidence_tier_impact is None:
        evidence_tier_impact = {
            "bundle": "blocked",
            "mapping": "caps_at_mapping_only",
            "run": "caps_at_partial",
            "metric": "withholds_affected_metric",
            "claim": "withholds_affected_claim",
            "none": "none",
        }[blocking_scope]
    remediation_code = remediation_code or f"REMEDIATE_{code}"
    recommended_action = recommended_action or (
        "Correct the affected input or decision recorded in this diagnostic and rerun the "
        "reported validation stage."
    )
    diagnostic: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "stage": stage,
        "message": message,
        "blocking_scope": blocking_scope,
        "affected_outputs": sorted(set(affected_outputs)),
        "evidence_tier_impact": evidence_tier_impact,
        "remediation_code": remediation_code,
        "recommended_action": recommended_action,
    }
    for name, value in (
        ("field", field),
        ("event_id", event_id),
        ("run_id", run_id),
        ("source_line", source_line),
        ("details", details),
    ):
        if value is not None:
            diagnostic[name] = value
    return diagnostic


def diagnostic_document(
    diagnostics: Iterable[dict[str, Any]], *, stage: str, passed: bool = False
) -> dict[str, Any]:
    values = list(diagnostics)
    return {
        "diagnostic_schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "passed": passed,
        "stage": stage,
        "diagnostic_count": len(values),
        "diagnostics": values,
    }


def diagnostic_from_exception(
    exc: Exception,
    stage: str,
    *,
    path_aliases: dict[str, str] | None = None,
    restricted_values: Iterable[str] = (),
) -> dict[str, Any]:
    message = sanitize_message(
        str(exc), path_aliases=path_aliases, restricted_values=restricted_values
    )
    lowered = message.casefold()
    code = "PIPELINE_STAGE_FAILED"
    for candidate, phrases in _EXCEPTION_CODE_RULES:
        if any(phrase in lowered for phrase in phrases):
            code = candidate
            break
    if stage == "reconstruction":
        reconstruction_rules = (
            ("BOUNDARY_MISSING_START", ("missing its `run_started` boundary",)),
            ("BOUNDARY_MISSING_TERMINAL", ("missing a terminal boundary",)),
            ("BOUNDARY_DUPLICATE_START", ("duplicate `run_started` boundaries",)),
            (
                "BOUNDARY_CONFLICTING_TERMINAL",
                ("terminal boundaries", "terminal event/status conflict"),
            ),
            ("BOUNDARY_POST_TERMINAL_EVENT", ("after the terminal boundary",)),
            (
                "BOUNDARY_AMBIGUOUS_TIMESTAMP",
                ("timestamp", "before the start boundary", "out-of-order"),
            ),
        )
        for candidate, phrases in reconstruction_rules:
            if any(phrase in lowered for phrase in phrases):
                code = candidate
                break

    event_match = re.search(r"event `([^`]+)`", message, flags=re.IGNORECASE)
    run_match = re.search(r"run `([^`]+)`", message, flags=re.IGNORECASE)
    field_match = re.search(r"`([^`]+)` must", message)
    line_match = re.search(r"(?:source )?line (\d+)", message, flags=re.IGNORECASE)
    return make_diagnostic(
        code,
        message,
        stage,
        field=field_match.group(1) if field_match else None,
        event_id=event_match.group(1) if event_match else None,
        run_id=run_match.group(1) if run_match else None,
        source_line=int(line_match.group(1)) if line_match else None,
    )


def canonical_validation_diagnostics(
    validation: dict[str, Any],
    raw_rows: Iterable[Any] = (),
    workflow_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    context_by_line = _canonical_context_by_line(raw_rows, workflow_config or {})
    for duplicate in validation.get("duplicate_event_ids", []):
        source_lines = duplicate.get("source_lines", [])
        first_line = source_lines[0] if source_lines else None
        context = context_by_line.get(first_line, {}) if isinstance(first_line, int) else {}
        diagnostics.append(
            make_diagnostic(
                "CANONICAL_DUPLICATE_EVENT_ID",
                "Canonical event identifier is duplicated.",
                "canonical_validation",
                field="event_id",
                event_id=str(duplicate.get("event_id")),
                run_id=context.get("run_id"),
                source_line=first_line if isinstance(first_line, int) else None,
                details={"source_lines": source_lines},
            )
        )

    signal_codes = {
        "event_id": "CANONICAL_INVALID_IDENTIFIER",
        "event_type": "CANONICAL_UNSUPPORTED_EVENT_TYPE",
        "timestamp": "CANONICAL_INVALID_TIMESTAMP",
        "attempt_index": "CANONICAL_NEGATIVE_VALUE",
        "observed_cost_usd": "CANONICAL_NEGATIVE_VALUE",
        "input_tokens": "CANONICAL_NEGATIVE_VALUE",
        "output_tokens": "CANONICAL_NEGATIVE_VALUE",
        "quality_score": "CANONICAL_NEGATIVE_VALUE",
        "human_effort_seconds": "CANONICAL_NEGATIVE_VALUE",
    }
    for row in validation.get("rows_with_invalid_fields", []):
        context = context_by_line.get(row.get("source_line"), {})
        for signal in row.get("invalid_signals", []):
            diagnostics.append(
                make_diagnostic(
                    signal_codes.get(signal, "CANONICAL_INVALID_IDENTIFIER"),
                    f"Canonical signal `{signal}` is invalid.",
                    "canonical_validation",
                    field=_canonical_source_field(str(signal), workflow_config or {}),
                    event_id=context.get("event_id"),
                    run_id=context.get("run_id"),
                    source_line=row.get("source_line"),
                )
            )
    for row in validation.get("rows_with_missing_fields", []):
        context = context_by_line.get(row.get("source_line"), {})
        for signal in row.get("missing_signals", []):
            diagnostics.append(
                make_diagnostic(
                    "TELEMETRY_REQUIRED_SIGNAL_MISSING",
                    f"Required canonical signal `{signal}` is missing.",
                    "canonical_validation",
                    field=str(signal),
                    event_id=context.get("event_id"),
                    run_id=context.get("run_id"),
                    source_line=row.get("source_line"),
                )
            )
    return diagnostics


def _canonical_context_by_line(
    raw_rows: Iterable[Any], workflow_config: dict[str, Any]
) -> dict[int, dict[str, str]]:
    source_schema = workflow_config.get("source_schema", {})
    event_id_field = source_schema.get("event_id_field")
    run_id_keys = workflow_config.get("run_id_keys", [])
    contexts: dict[int, dict[str, str]] = {}
    for row in raw_rows:
        if not isinstance(row, tuple) or len(row) < 2:
            continue
        line_number, payload = row[0], row[1]
        if not isinstance(line_number, int) or not isinstance(payload, dict):
            continue
        context: dict[str, str] = {}
        event_id = payload.get(event_id_field) if isinstance(event_id_field, str) else None
        if event_id not in (None, "") and not isinstance(event_id, (dict, list, bool)):
            context["event_id"] = str(event_id)
        for key in run_id_keys if isinstance(run_id_keys, list) else []:
            run_id = payload.get(key)
            if run_id not in (None, "") and not isinstance(run_id, (dict, list, bool)):
                context["run_id"] = str(run_id)
                break
        contexts[line_number] = context
    return contexts


def _canonical_source_field(signal: str, workflow_config: dict[str, Any]) -> str:
    source_schema = workflow_config.get("source_schema", {})
    list_mappings = {
        "run_id": workflow_config.get("run_id_keys"),
        "trace_id": workflow_config.get("trace_id_keys"),
        "step_name": workflow_config.get("step_name_keys"),
    }
    configured_list = list_mappings.get(signal)
    if isinstance(configured_list, list) and configured_list:
        return str(configured_list[0])
    mappings = {
        "event_id": source_schema.get("event_id_field"),
        "event_type": source_schema.get("event_type_field"),
        "timestamp": source_schema.get("timestamp_field"),
        "attempt_index": workflow_config.get("attempt_field"),
        "observed_cost_usd": workflow_config.get("cost_field"),
        "input_tokens": workflow_config.get("input_tokens_field"),
        "output_tokens": workflow_config.get("output_tokens_field"),
        "quality_score": workflow_config.get("quality_score_field"),
        "human_effort_seconds": workflow_config.get("human_effort_seconds_field"),
        "status": workflow_config.get("status_field"),
        "actor_type": workflow_config.get("actor_field"),
        "agent_configuration_id": workflow_config.get("agent_configuration_id_field"),
        "agent_version": workflow_config.get("agent_version_field"),
        "configuration_hash": workflow_config.get("configuration_hash_field"),
        "model_name": workflow_config.get("model_field"),
        "prompt_version": workflow_config.get("prompt_version_field"),
        "tool_name": workflow_config.get("tool_name_field"),
        "tool_call_id": workflow_config.get("tool_call_id_field"),
        "routing_decision": workflow_config.get("routing_decision_field"),
        "handoff_target": workflow_config.get("handoff_target_field"),
        "failure_mode": workflow_config.get("failure_mode_field"),
        "business_success": workflow_config.get("business_success_field"),
        "intervention_markers": workflow_config.get("intervention_flag_field"),
    }
    value = mappings.get(signal)
    return str(value) if isinstance(value, str) and value else signal


def telemetry_validation_diagnostics(
    validation: dict[str, Any],
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    consistency = validation.get("configuration_consistency", {})
    for invalid_run in consistency.get("invalid_runs", []):
        issues = invalid_run.get("issues", [])
        unexpected = "unexpected_configuration_identity" in issues
        diagnostics.append(
            make_diagnostic(
                "CONFIGURATION_UNEXPECTED" if unexpected else "CONFIGURATION_DRIFT",
                (
                    "A run uses a configuration identity outside the declared matrix."
                    if unexpected
                    else "A run contains missing or mixed configuration identity."
                ),
                "telemetry_validation",
                field="configuration",
                run_id=(
                    str(invalid_run["run_id"]) if invalid_run.get("run_id") is not None else None
                ),
                details={
                    "issues": issues,
                    "event_ids": invalid_run.get("event_ids", []),
                },
            )
        )
    for missing in consistency.get("missing_expected_configurations", []):
        diagnostics.append(
            make_diagnostic(
                "CONFIGURATION_UNEXPECTED",
                "A required configuration identity was not observed.",
                "telemetry_validation",
                field="expected_configurations",
                details={"missing_configuration": missing},
            )
        )
    for signal in validation.get("missing_required_schema_signals", []):
        diagnostics.append(
            make_diagnostic(
                "TELEMETRY_REQUIRED_SIGNAL_MISSING",
                f"Required telemetry signal `{signal}` is not instrumented.",
                "telemetry_validation",
                field=str(signal),
            )
        )
    for run_id, signals in validation.get("missing_required_by_run", {}).items():
        for signal in signals:
            diagnostics.append(
                make_diagnostic(
                    "TELEMETRY_REQUIRED_SIGNAL_MISSING",
                    f"Required telemetry signal `{signal}` is missing for a run.",
                    "telemetry_validation",
                    field=str(signal),
                    run_id=str(run_id),
                )
            )
    if not validation.get("run_count", 0):
        diagnostics.append(
            make_diagnostic(
                "TELEMETRY_REQUIRED_SIGNAL_MISSING",
                "No workflow runs were available for evaluation.",
                "telemetry_validation",
                field="workflow_runs",
            )
        )
    return diagnostics


def sanitize_message(
    message: str,
    *,
    path_aliases: dict[str, str] | None = None,
    restricted_values: Iterable[str] = (),
) -> str:
    sanitized = message
    aliases = path_aliases or {}
    for source, logical_name in sorted(
        aliases.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if source:
            sanitized = sanitized.replace(source, logical_name)
    for value in sorted({item for item in restricted_values if item}, key=len, reverse=True):
        sanitized = sanitized.replace(value, "[RESTRICTED]")
    return sanitized
