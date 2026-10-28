from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from .diagnostics import diagnostic_document, make_diagnostic
from .exceptions import ConfigurationError, DataValidationError
from .io_utils import read_json, read_jsonl_with_line_numbers, unpack_jsonl_row
from .models import parse_timestamp
from .validation import validate_evaluation_manifest

MAPPING_ARTIFACT_SCHEMA_VERSION = "1.0.0"

TRANSFORMATION_CLASSES = {
    "direct",
    "coercion",
    "lookup",
    "derivation",
    "aggregation",
    "manual_review",
    "unmapped",
}
SEMANTIC_STATUSES = {"confirmed", "provisional", "unresolved"}
APPROVAL_STATUSES = {"approved", "provisional", "unresolved"}
ISSUE_SEVERITIES = {"critical", "warning", "info"}
ISSUE_STATUSES = {"open", "resolved", "accepted"}
ACTIVITY_TYPES = {
    "source_review",
    "mapping_change",
    "semantic_decision",
    "stakeholder_question",
    "test_run",
    "approval",
    "issue_opened",
    "issue_resolved",
}

GENERATED_MAPPING_ARTIFACTS = (
    "source_profile.json",
    "mapping_assessment.json",
    "mapping_friction_summary.json",
    "mapping_issue_register.csv",
    "mapping_readiness_report.md",
    "diagnostics.json",
)


def assess_mapping(
    source_profile_path: str | Path | dict[str, Any],
    mapping_spec_path: str | Path | dict[str, Any],
    evaluation_manifest_path: str | Path | dict[str, Any],
    output_dir: str | Path,
    *,
    activity_log_path: str | Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Assess source-to-canonical mapping readiness without retaining source values."""

    source_profile = _load_json_object(source_profile_path, "source profile")
    mapping_spec = _load_json_object(mapping_spec_path, "mapping specification")
    manifest = _load_json_object(evaluation_manifest_path, "evaluation manifest")
    manifest = validate_evaluation_manifest(manifest)
    normalized_profile = _validate_source_profile(source_profile)
    normalized_spec = _validate_mapping_spec(mapping_spec)
    activity_rows = _load_activity_log(activity_log_path)
    required_signals = list(manifest["telemetry_requirements"]["required"])
    optional_signals = list(manifest["telemetry_requirements"]["optional"])

    assessment, diagnostics = _build_mapping_assessment(
        normalized_profile,
        normalized_spec,
        required_signals,
        optional_signals,
    )
    friction = _build_friction_summary(
        normalized_profile,
        normalized_spec,
        activity_rows,
        assessment,
    )
    if friction["active_effort"]["measurement_status"] == "partial":
        diagnostics.append(
            make_diagnostic(
                "MAPPING_ACTIVITY_EFFORT_INCOMPLETE",
                "Active mapping effort is available for only part of the activity log.",
                "mapping_activity",
                severity="warning",
                blocking_scope="none",
                affected_outputs=["mapping_friction_summary.json"],
                evidence_tier_impact="none",
                remediation_code="RECORD_ACTIVITY_EFFORT",
                recommended_action=(
                    "Record active-effort minutes for every activity or retain the value as a "
                    "partial observed sum."
                ),
                field="active_effort_minutes",
            )
        )

    assessment["diagnostic_count"] = len(diagnostics)
    assessment["diagnostic_codes"] = sorted({item["code"] for item in diagnostics})
    diagnostic_payload = diagnostic_document(
        diagnostics,
        stage="mapping_assessment",
        passed=assessment["mapping_readiness"] == "verified",
    )
    artifacts: dict[str, str] = {
        "source_profile.json": _json_text(normalized_profile),
        "mapping_assessment.json": _json_text(assessment),
        "mapping_friction_summary.json": _json_text(friction),
        "mapping_issue_register.csv": _issue_register_csv(normalized_spec, diagnostics),
        "mapping_readiness_report.md": _mapping_report(assessment, friction, diagnostics),
        "diagnostics.json": _json_text(diagnostic_payload),
    }
    _publish_mapping_artifacts(Path(output_dir), artifacts, overwrite=overwrite)
    return {
        "mapping_readiness": assessment["mapping_readiness"],
        "required_signal_count": assessment["required_signal_summary"]["signal_count"],
        "diagnostic_count": len(diagnostics),
        "artifacts": list(GENERATED_MAPPING_ARTIFACTS),
    }


def build_mapping_assessment_payload(
    source_profile: dict[str, Any],
    mapping_spec: dict[str, Any],
    evaluation_manifest: dict[str, Any],
    *,
    activity_rows: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Build mapping artifacts in memory for adapters that share the onboarding model."""

    manifest = validate_evaluation_manifest(evaluation_manifest)
    normalized_profile = _validate_source_profile(source_profile)
    normalized_spec = _validate_mapping_spec(mapping_spec)
    normalized_activity = [
        _validate_activity(item, index + 1) for index, item in enumerate(activity_rows or [])
    ]
    assessment, diagnostics = _build_mapping_assessment(
        normalized_profile,
        normalized_spec,
        list(manifest["telemetry_requirements"]["required"]),
        list(manifest["telemetry_requirements"]["optional"]),
    )
    friction = _build_friction_summary(
        normalized_profile,
        normalized_spec,
        normalized_activity,
        assessment,
    )
    assessment["diagnostic_count"] = len(diagnostics)
    assessment["diagnostic_codes"] = sorted({item["code"] for item in diagnostics})
    return assessment, friction, diagnostics


def render_mapping_issue_register(
    mapping_spec: dict[str, Any], diagnostics: list[dict[str, Any]]
) -> str:
    return _issue_register_csv(_validate_mapping_spec(mapping_spec), diagnostics)


def render_mapping_readiness_report(
    assessment: dict[str, Any],
    friction: dict[str, Any],
    diagnostics: list[dict[str, Any]],
) -> str:
    return _mapping_report(assessment, friction, diagnostics)


def load_mapping_activity(path: str | Path | None) -> list[dict[str, Any]]:
    """Load and validate an optional mapping activity log for adapter reuse."""

    return _load_activity_log(path)


def _load_json_object(path_or_value: str | Path | dict[str, Any], label: str) -> dict[str, Any]:
    value = path_or_value if isinstance(path_or_value, dict) else read_json(path_or_value)
    if not isinstance(value, dict):
        raise ConfigurationError(f"The {label} must be a JSON object")
    return value


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"`{field}` must be a non-empty string")
    return value


def _require_non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigurationError(f"`{field}` must be a non-negative integer")
    return value


def _validate_source_profile(profile: dict[str, Any]) -> dict[str, Any]:
    logical_source_id = _require_string(profile.get("logical_source_id"), "logical_source_id")
    source_kind = _require_string(profile.get("source_kind"), "source_kind")
    file_count = _require_non_negative_int(profile.get("file_count"), "file_count")
    record_count = _require_non_negative_int(profile.get("record_count"), "record_count")
    raw_formats = profile.get("formats", [])
    if not isinstance(raw_formats, list) or any(
        not isinstance(item, str) or not item for item in raw_formats
    ):
        raise ConfigurationError("`formats` must be a list of non-empty strings")
    if len(set(raw_formats)) != len(raw_formats):
        raise ConfigurationError("`formats` must not contain duplicates")
    inventory = profile.get("field_inventory")
    if not isinstance(inventory, list):
        raise ConfigurationError("`field_inventory` must be a list")
    normalized_inventory: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for index, item in enumerate(inventory):
        if not isinstance(item, dict):
            raise ConfigurationError(f"`field_inventory[{index}]` must be an object")
        path = _require_string(item.get("path"), f"field_inventory[{index}].path")
        if path in seen_paths:
            raise ConfigurationError(f"Duplicate source-profile field path `{path}`")
        seen_paths.add(path)
        observed_count = _require_non_negative_int(
            item.get("observed_count"), f"field_inventory[{index}].observed_count"
        )
        denominator = _require_non_negative_int(
            item.get("record_denominator", record_count),
            f"field_inventory[{index}].record_denominator",
        )
        if observed_count > denominator:
            raise ConfigurationError(f"Source-profile field `{path}` exceeds its denominator")
        value_types = item.get("value_types", [])
        if not isinstance(value_types, list) or any(
            not isinstance(value, str) or not value for value in value_types
        ):
            raise ConfigurationError(
                f"`field_inventory[{index}].value_types` must be a string list"
            )
        normalized_inventory.append(
            {
                "path": path,
                "observed_count": observed_count,
                "record_denominator": denominator,
                "coverage": round(observed_count / denominator, 3) if denominator else None,
                "value_types": sorted(set(value_types)),
            }
        )
    manual_review_count = _require_non_negative_int(
        profile.get("manual_review_record_count", 0), "manual_review_record_count"
    )
    if manual_review_count > record_count:
        raise ConfigurationError("`manual_review_record_count` cannot exceed record_count")
    return {
        "source_profile_schema_version": MAPPING_ARTIFACT_SCHEMA_VERSION,
        "logical_source_id": logical_source_id,
        "source_kind": source_kind,
        "file_count": file_count,
        "record_count": record_count,
        "formats": sorted(raw_formats),
        "field_inventory": sorted(normalized_inventory, key=lambda item: item["path"]),
        "manual_review_record_count": manual_review_count,
    }


def _validate_mapping_spec(spec: dict[str, Any]) -> dict[str, Any]:
    mapping_id = _require_string(spec.get("mapping_id"), "mapping_id")
    mapping_version = _require_string(spec.get("mapping_version"), "mapping_version")
    logical_source_id = _require_string(spec.get("logical_source_id"), "logical_source_id")
    raw_mappings = spec.get("mappings")
    if not isinstance(raw_mappings, list):
        raise ConfigurationError("`mappings` must be a list")
    normalized_mappings: list[dict[str, Any]] = []
    seen_signals: set[str] = set()
    for index, item in enumerate(raw_mappings):
        if not isinstance(item, dict):
            raise ConfigurationError(f"`mappings[{index}]` must be an object")
        signal = _require_string(
            item.get("canonical_signal"), f"mappings[{index}].canonical_signal"
        )
        if signal in seen_signals:
            raise ConfigurationError(f"Duplicate canonical mapping signal `{signal}`")
        seen_signals.add(signal)
        paths = item.get("source_paths", [])
        if not isinstance(paths, list) or any(
            not isinstance(path, str) or not path for path in paths
        ):
            raise ConfigurationError(f"`mappings[{index}].source_paths` must be a string list")
        transformation = item.get("transformation")
        if transformation not in TRANSFORMATION_CLASSES:
            raise ConfigurationError(f"Unsupported transformation for `{signal}`")
        semantic_status = item.get("semantic_status")
        if semantic_status not in SEMANTIC_STATUSES:
            raise ConfigurationError(f"Unsupported semantic status for `{signal}`")
        tested = item.get("transformation_tested")
        if not isinstance(tested, bool):
            raise ConfigurationError(f"`mappings[{index}].transformation_tested` must be boolean")
        approval = _validate_approval(item.get("approval"), f"mappings[{index}].approval")
        normalized_mappings.append(
            {
                "canonical_signal": signal,
                "source_paths": sorted(set(paths)),
                "transformation": transformation,
                "semantic_status": semantic_status,
                "transformation_tested": tested,
                "approval": approval,
            }
        )
    approvals = {
        name: _validate_approval(spec.get(name), name)
        for name in (
            "workflow_boundary_approval",
            "business_status_approval",
            "privacy_approval",
        )
    }
    raw_issues = spec.get("issues", [])
    if not isinstance(raw_issues, list):
        raise ConfigurationError("`issues` must be a list")
    issues: list[dict[str, Any]] = []
    issue_ids: set[str] = set()
    for index, item in enumerate(raw_issues):
        if not isinstance(item, dict):
            raise ConfigurationError(f"`issues[{index}]` must be an object")
        issue_id = _require_string(item.get("issue_id"), f"issues[{index}].issue_id")
        if issue_id in issue_ids:
            raise ConfigurationError(f"Duplicate mapping issue `{issue_id}`")
        issue_ids.add(issue_id)
        severity = item.get("severity")
        status = item.get("status")
        if severity not in ISSUE_SEVERITIES or status not in ISSUE_STATUSES:
            raise ConfigurationError(f"Mapping issue `{issue_id}` has an unsupported state")
        issue_signal: str | None = item.get("canonical_signal")
        if issue_signal is not None:
            issue_signal = _require_string(issue_signal, f"issues[{index}].canonical_signal")
        evidence_ref = item.get("evidence_ref")
        if evidence_ref is not None:
            evidence_ref = _require_string(evidence_ref, f"issues[{index}].evidence_ref")
        issues.append(
            {
                "issue_id": issue_id,
                "severity": severity,
                "status": status,
                "canonical_signal": issue_signal,
                "evidence_ref": evidence_ref,
            }
        )
    return {
        "mapping_spec_schema_version": MAPPING_ARTIFACT_SCHEMA_VERSION,
        "mapping_id": mapping_id,
        "mapping_version": mapping_version,
        "logical_source_id": logical_source_id,
        "mappings": sorted(normalized_mappings, key=lambda item: item["canonical_signal"]),
        **approvals,
        "issues": sorted(issues, key=lambda item: item["issue_id"]),
    }


def _validate_approval(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"`{field}` must be an object")
    status = value.get("status")
    if status not in APPROVAL_STATUSES:
        raise ConfigurationError(f"`{field}.status` is unsupported")
    reviewer_role = value.get("reviewer_role")
    evidence_ref = value.get("evidence_ref")
    if reviewer_role is not None:
        reviewer_role = _require_string(reviewer_role, f"{field}.reviewer_role")
    if evidence_ref is not None:
        evidence_ref = _require_string(evidence_ref, f"{field}.evidence_ref")
    if status == "approved" and (reviewer_role is None or evidence_ref is None):
        raise ConfigurationError(f"`{field}` requires reviewer_role and evidence_ref when approved")
    return {
        "status": status,
        "reviewer_role": reviewer_role,
        "evidence_ref": evidence_ref,
    }


def _load_activity_log(path: str | Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    rows = read_jsonl_with_line_numbers(path)
    activities: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        line, payload, _raw = unpack_jsonl_row(row)
        activity = _validate_activity(payload, line)
        if activity["activity_id"] in seen:
            raise DataValidationError(
                f"Duplicate mapping activity id `{activity['activity_id']}` at line {line}"
            )
        seen.add(activity["activity_id"])
        activities.append(activity)
    return sorted(activities, key=lambda item: (item["timestamp"], item["activity_id"]))


def _validate_activity(payload: dict[str, Any], line: int) -> dict[str, Any]:
    activity_id = _require_string(payload.get("activity_id"), f"activity line {line}.activity_id")
    timestamp = _require_string(payload.get("timestamp"), f"activity line {line}.timestamp")
    try:
        normalized_timestamp = parse_timestamp(timestamp).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError) as exc:
        raise DataValidationError(f"Invalid mapping activity timestamp at line {line}") from exc
    actor_role = _require_string(payload.get("actor_role"), f"activity line {line}.actor_role")
    activity_type = payload.get("activity_type")
    if activity_type not in ACTIVITY_TYPES:
        raise ConfigurationError(f"Unsupported mapping activity type at line {line}")
    mapping_version = _require_string(
        payload.get("mapping_version"), f"activity line {line}.mapping_version"
    )
    effort = payload.get("active_effort_minutes")
    if effort is not None and (
        isinstance(effort, bool) or not isinstance(effort, int | float) or effort < 0
    ):
        raise ConfigurationError(
            f"`active_effort_minutes` at activity line {line} must be non-negative or null"
        )
    issue_id = payload.get("issue_id")
    if issue_id is not None:
        issue_id = _require_string(issue_id, f"activity line {line}.issue_id")
    outcome = payload.get("outcome")
    if outcome is not None:
        outcome = _require_string(outcome, f"activity line {line}.outcome")
    return {
        "activity_id": activity_id,
        "timestamp": normalized_timestamp,
        "actor_role": actor_role,
        "activity_type": activity_type,
        "active_effort_minutes": float(effort) if effort is not None else None,
        "mapping_version": mapping_version,
        "issue_id": issue_id,
        "outcome": outcome,
    }


def _build_mapping_assessment(
    profile: dict[str, Any],
    spec: dict[str, Any],
    required_signals: list[str],
    optional_signals: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if profile["logical_source_id"] != spec["logical_source_id"]:
        raise ConfigurationError(
            "Source profile and mapping specification identify different sources"
        )
    inventory = {item["path"]: item for item in profile["field_inventory"]}
    mappings = {item["canonical_signal"]: item for item in spec["mappings"]}
    diagnostics: list[dict[str, Any]] = []
    signal_results: dict[str, dict[str, Any]] = {}
    for required, signals in ((True, required_signals), (False, optional_signals)):
        for signal in signals:
            mapping = mappings.get(signal)
            configured = bool(
                mapping and mapping["transformation"] != "unmapped" and mapping["source_paths"]
            )
            observed_paths = [
                path
                for path in (mapping["source_paths"] if mapping else [])
                if path in inventory and inventory[path]["observed_count"] > 0
            ]
            observed = bool(
                mapping and configured and len(observed_paths) == len(mapping["source_paths"])
            )
            tested = bool(mapping and mapping["transformation_tested"])
            confirmed = bool(
                mapping
                and mapping["semantic_status"] == "confirmed"
                and mapping["approval"]["status"] == "approved"
            )
            signal_results[signal] = {
                "required": required,
                "configured": configured,
                "observed": observed,
                "transformation_tested": tested,
                "semantically_approved": confirmed,
                "transformation": mapping["transformation"] if mapping else "unmapped",
                "source_path_count": len(mapping["source_paths"]) if mapping else 0,
                "observed_source_path_count": len(observed_paths),
            }
            if required and not configured:
                diagnostics.append(
                    _mapping_diagnostic(
                        "MAPPING_REQUIRED_SIGNAL_UNMAPPED",
                        signal,
                        "Required canonical signal is not mapped.",
                        "MAP_REQUIRED_SIGNAL",
                        (
                            "Map the required signal or remove it from the requested "
                            "evaluation design."
                        ),
                    )
                )
            elif required and not observed:
                diagnostics.append(
                    _mapping_diagnostic(
                        "MAPPING_SOURCE_PATH_UNOBSERVED",
                        signal,
                        "One or more mapped source paths were not observed.",
                        "VERIFY_SOURCE_PATH",
                        "Correct the source path or provide evidence that the field is present.",
                    )
                )
            if required and configured and not tested:
                diagnostics.append(
                    _mapping_diagnostic(
                        "MAPPING_TRANSFORMATION_UNTESTED",
                        signal,
                        "The configured transformation has not been tested.",
                        "TEST_TRANSFORMATION",
                        "Execute and record a positive and boundary mapping test for this signal.",
                        severity="warning",
                    )
                )
            if required and configured and not confirmed:
                diagnostics.append(
                    _mapping_diagnostic(
                        "MAPPING_SEMANTICS_UNRESOLVED",
                        signal,
                        "The mapping semantics have not been confirmed and approved.",
                        "APPROVE_SIGNAL_SEMANTICS",
                        "Record the semantic decision, reviewer role, and evidence reference.",
                        severity="warning",
                    )
                )

    approval_results = {
        name: spec[name]["status"]
        for name in (
            "workflow_boundary_approval",
            "business_status_approval",
            "privacy_approval",
        )
    }
    for name, status in approval_results.items():
        if status != "approved":
            diagnostics.append(
                make_diagnostic(
                    "MAPPING_APPROVAL_REQUIRED",
                    f"Mapping approval `{name}` is not complete.",
                    "mapping_assessment",
                    severity="warning",
                    blocking_scope="mapping",
                    affected_outputs=["scorecard.json", "evaluation_report.md"],
                    evidence_tier_impact="caps_at_mapping_only",
                    remediation_code="COMPLETE_MAPPING_APPROVAL",
                    recommended_action=(
                        "Record an approved reviewer role and evidence reference for this gate."
                    ),
                    field=name,
                )
            )
    open_critical_issues = [
        item
        for item in spec["issues"]
        if item["severity"] == "critical" and item["status"] == "open"
    ]
    for issue in open_critical_issues:
        diagnostics.append(
            make_diagnostic(
                "MAPPING_CRITICAL_ISSUE_OPEN",
                "A critical mapping issue remains open.",
                "mapping_assessment",
                blocking_scope="mapping",
                affected_outputs=["scorecard.json", "evaluation_report.md"],
                evidence_tier_impact="caps_at_mapping_only",
                remediation_code="RESOLVE_CRITICAL_MAPPING_ISSUE",
                recommended_action="Resolve or explicitly close the critical mapping issue.",
                field=issue.get("canonical_signal") or "issues",
                details={"issue_id": issue["issue_id"]},
            )
        )

    required_results = [signal_results[signal] for signal in required_signals]
    required_mapped = sum(item["configured"] for item in required_results)
    required_observed = sum(item["observed"] for item in required_results)
    required_tested = sum(item["transformation_tested"] for item in required_results)
    required_confirmed = sum(item["semantically_approved"] for item in required_results)
    all_required_observed = bool(required_results) and required_observed == len(required_results)
    all_approvals = all(status == "approved" for status in approval_results.values())
    if open_critical_issues or approval_results["privacy_approval"] != "approved":
        readiness = "blocked"
    elif (
        not all_required_observed
        or required_tested != len(required_results)
        or required_confirmed != len(required_results)
        or not all_approvals
    ):
        readiness = "provisional"
    else:
        readiness = "verified"
    return (
        {
            "mapping_assessment_schema_version": MAPPING_ARTIFACT_SCHEMA_VERSION,
            "mapping_id": spec["mapping_id"],
            "mapping_version": spec["mapping_version"],
            "logical_source_id": spec["logical_source_id"],
            "mapping_readiness": readiness,
            "required_signal_summary": {
                "signal_count": len(required_results),
                "mapped_count": required_mapped,
                "observed_count": required_observed,
                "transformation_tested_count": required_tested,
                "semantically_approved_count": required_confirmed,
            },
            "optional_signal_count": len(optional_signals),
            "signals": dict(sorted(signal_results.items())),
            "approvals": approval_results,
            "open_critical_issue_count": len(open_critical_issues),
            "mapping_spec_sha256": _canonical_hash(spec),
            "source_profile_sha256": _canonical_hash(profile),
        },
        diagnostics,
    )


def _mapping_diagnostic(
    code: str,
    signal: str,
    message: str,
    remediation_code: str,
    action: str,
    *,
    severity: str = "error",
) -> dict[str, Any]:
    return make_diagnostic(
        code,
        message,
        "mapping_assessment",
        severity=severity,
        blocking_scope="mapping",
        affected_outputs=["scorecard.json", "evaluation_report.md"],
        evidence_tier_impact="caps_at_mapping_only",
        remediation_code=remediation_code,
        recommended_action=action,
        field=signal,
    )


def _build_friction_summary(
    profile: dict[str, Any],
    spec: dict[str, Any],
    activities: list[dict[str, Any]],
    assessment: dict[str, Any],
) -> dict[str, Any]:
    efforts = [
        item["active_effort_minutes"]
        for item in activities
        if item["active_effort_minutes"] is not None
    ]
    effort_status = (
        "not_recorded"
        if not efforts
        else "complete" if len(efforts) == len(activities) else "partial"
    )
    timestamps = [parse_timestamp(item["timestamp"]) for item in activities]
    cycle_seconds = (
        round((max(timestamps) - min(timestamps)).total_seconds(), 3)
        if len(timestamps) >= 2
        else None
    )
    activity_counts = Counter(item["activity_type"] for item in activities)
    transformation_counts = Counter(item["transformation"] for item in spec["mappings"])
    issue_counts = Counter(f"{item['severity']}:{item['status']}" for item in spec["issues"])
    versions = sorted({item["mapping_version"] for item in activities})
    required = assessment["required_signal_summary"]
    return {
        "mapping_friction_schema_version": MAPPING_ARTIFACT_SCHEMA_VERSION,
        "mapping_id": spec["mapping_id"],
        "mapping_readiness": assessment["mapping_readiness"],
        "required_signal_progress": required,
        "transformation_counts": dict(sorted(transformation_counts.items())),
        "mapping_iteration_count": len(versions),
        "mapping_versions_observed": versions,
        "decision_count": activity_counts["semantic_decision"],
        "approval_activity_count": activity_counts["approval"],
        "stakeholder_dependency_count": activity_counts["stakeholder_question"],
        "issue_activity_counts": {
            "opened": activity_counts["issue_opened"],
            "resolved": activity_counts["issue_resolved"],
        },
        "issue_register_counts": dict(sorted(issue_counts.items())),
        "manual_review_record_count": profile["manual_review_record_count"],
        "manual_review_record_denominator": profile["record_count"],
        "source_heterogeneity": {
            "file_count": profile["file_count"],
            "format_count": len(profile["formats"]),
            "field_count": len(profile["field_inventory"]),
        },
        "elapsed_onboarding_cycle_seconds": cycle_seconds,
        "active_effort": {
            "measurement_status": effort_status,
            "observed_total_minutes": round(sum(efforts), 3) if efforts else None,
            "complete_total_minutes": (
                round(sum(efforts), 3) if effort_status == "complete" else None
            ),
            "observed_activity_count": len(efforts),
            "activity_denominator": len(activities),
        },
        "interpretation_boundaries": [
            "The friction profile is multidimensional and is not a universal weighted score.",
            (
                "Active effort is reported only from recorded activity entries and is not "
                "extrapolated."
            ),
            "Synthetic fixture values verify calculations and do not estimate enterprise burden.",
        ],
    }


def _issue_register_csv(spec: dict[str, Any], diagnostics: list[dict[str, Any]]) -> str:
    fields = [
        "issue_id",
        "severity",
        "status",
        "canonical_signal",
        "diagnostic_code",
        "remediation_code",
        "recommended_action",
        "evidence_ref",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    rows: list[dict[str, Any]] = []
    for item in spec["issues"]:
        rows.append(
            {
                "issue_id": item["issue_id"],
                "severity": item["severity"],
                "status": item["status"],
                "canonical_signal": item.get("canonical_signal") or "",
                "diagnostic_code": "",
                "remediation_code": "",
                "recommended_action": "",
                "evidence_ref": item.get("evidence_ref") or "",
            }
        )
    for index, item in enumerate(diagnostics, start=1):
        rows.append(
            {
                "issue_id": f"generated-{index:03d}",
                "severity": item["severity"],
                "status": "open",
                "canonical_signal": item.get("field", ""),
                "diagnostic_code": item["code"],
                "remediation_code": item.get("remediation_code", ""),
                "recommended_action": item.get("recommended_action", ""),
                "evidence_ref": "",
            }
        )
    for row in sorted(rows, key=lambda value: value["issue_id"]):
        writer.writerow(row)
    return output.getvalue()


def _mapping_report(
    assessment: dict[str, Any],
    friction: dict[str, Any],
    diagnostics: list[dict[str, Any]],
) -> str:
    required = assessment["required_signal_summary"]
    effort = friction["active_effort"]
    lines = [
        "# Mapping Readiness Report",
        "",
        f"- Mapping readiness: `{assessment['mapping_readiness']}`",
        f"- Required signals mapped: {required['mapped_count']}/{required['signal_count']}",
        f"- Required signals observed: {required['observed_count']}/{required['signal_count']}",
        (
            "- Required transformations tested: "
            f"{required['transformation_tested_count']}/{required['signal_count']}"
        ),
        (
            "- Required semantics approved: "
            f"{required['semantically_approved_count']}/{required['signal_count']}"
        ),
        f"- Mapping iterations recorded: {friction['mapping_iteration_count']}",
        f"- Active-effort measurement: `{effort['measurement_status']}`",
        "",
        "The friction measures are a profile, not a composite confidence score. Synthetic values "
        "verify the implementation only and do not estimate enterprise onboarding effort.",
        "",
        "## Actions",
        "",
    ]
    if diagnostics:
        lines.extend(
            f"- `{item['code']}`: {item.get('recommended_action', item['message'])}"
            for item in diagnostics
        )
    else:
        lines.append("- No unresolved mapping actions were identified.")
    lines.append("")
    return "\n".join(lines)


def _publish_mapping_artifacts(
    output_root: Path, artifacts: dict[str, str], *, overwrite: bool
) -> None:
    if output_root.exists() and not output_root.is_dir():
        raise DataValidationError(f"Mapping output path is not a directory: {output_root}")
    existing = [name for name in GENERATED_MAPPING_ARTIFACTS if (output_root / name).exists()]
    if existing and not overwrite:
        raise DataValidationError(
            "Refusing to overwrite existing mapping artifacts: " + ", ".join(existing)
        )
    output_root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".mapping-assessment-", dir=output_root.parent))
    replacement: Path | None = None
    backup: Path | None = None
    try:
        for name in GENERATED_MAPPING_ARTIFACTS:
            if name.endswith(".json"):
                (stage / name).write_text(artifacts[name], encoding="utf-8")
            else:
                (stage / name).write_text(artifacts[name], encoding="utf-8", newline="")
        if not output_root.exists():
            os.replace(stage, output_root)
            return

        replacement = Path(tempfile.mkdtemp(prefix=".mapping-publish-", dir=output_root.parent))
        generated_names = set(GENERATED_MAPPING_ARTIFACTS)
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
        for name in GENERATED_MAPPING_ARTIFACTS:
            os.replace(stage / name, replacement / name)

        backup = Path(tempfile.mkdtemp(prefix=".mapping-backup-", dir=output_root.parent))
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


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
