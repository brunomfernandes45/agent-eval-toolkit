from __future__ import annotations

import hashlib
import json
import sys
from importlib import resources
from pathlib import Path
from typing import Any

from .diagnostics import make_diagnostic
from .exceptions import DataValidationError
from .io_utils import read_jsonl_with_line_numbers, unpack_jsonl_row
from .traceability import (
    build_artifact_index,
    file_fingerprint,
    hash_document,
    hash_payload,
    hash_raw_line,
)
from .version import ARTIFACT_SCHEMA_VERSION, TOOLKIT_VERSION

PACKAGE_BUNDLE_HASH_FIELD = "bundle_sha256"
SCHEMA_LOGICAL_ROOT = "schemas"


def build_evaluation_package(
    *,
    workflow_config_path: str,
    raw_logs_path: str,
    evaluation_manifest_path: str,
    baseline_context_path: str | None,
    mapping_assessment_path: str | None = None,
    output_dir: str,
    scorecard: dict[str, Any],
    telemetry_validation: dict[str, Any],
    redaction_report: dict[str, Any],
    excluded_runs: list[dict[str, Any]],
    artifact_names: list[str],
) -> dict[str, Any]:
    input_fingerprints = {
        "workflow_config": file_fingerprint(workflow_config_path, logical_path="workflow_config"),
        "raw_logs": file_fingerprint(raw_logs_path, logical_path="raw_logs"),
        "evaluation_manifest": file_fingerprint(
            evaluation_manifest_path, logical_path="evaluation_manifest"
        ),
    }
    if baseline_context_path:
        input_fingerprints["baseline_context"] = file_fingerprint(
            baseline_context_path, logical_path="baseline_context"
        )
    if mapping_assessment_path:
        input_fingerprints["mapping_assessment"] = file_fingerprint(
            mapping_assessment_path, logical_path="mapping_assessment"
        )

    lock_path = _find_dependency_lock()
    dependency_lock = (
        file_fingerprint(lock_path, logical_path="uv.lock")
        if lock_path is not None
        else {"path": "uv.lock", "status": "unavailable"}
    )

    package = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "toolkit_version": TOOLKIT_VERSION,
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "dependency_lock": dependency_lock,
        "evaluation_id": scorecard["evaluation"]["evaluation_id"],
        "evaluation_design": scorecard["evaluation"]["evaluation_design"],
        "workflow_family": scorecard["workflow_family"],
        "workflow_definition_id": scorecard["workflow_definition_id"],
        "input_fingerprints": input_fingerprints,
        "artifact_fingerprints": build_artifact_index(output_dir, artifact_names),
        "schema_fingerprints": build_schema_index(),
        "integrity": {
            "hash_algorithm": "sha256",
            "event_hash_canonicalization": "json-sort-keys-compact-utf8",
            "raw_line_hash_scope": "exact-jsonl-line-without-line-terminator",
            "bundle_hash_scope": "evaluation_package_excluding_bundle_sha256",
        },
        "configuration": scorecard["evaluation"]["configuration"],
        "taxonomy": scorecard["evaluation"].get("taxonomy", {}),
        "assumptions": scorecard["assumption_based_summary"]["assumptions_used"],
        "telemetry_validation": telemetry_validation,
        "redaction_report": redaction_report,
        "measurement_window_summary": scorecard["measurement_window_summary"],
        "evaluation_readiness": scorecard.get(
            "evaluation_readiness",
            {
                "attained_tier": "complete",
                "minimum_tier": "complete",
                "minimum_tier_met": True,
            },
        ),
        "metric_statuses": scorecard.get("metric_statuses", {}),
        "excluded_runs": excluded_runs,
        "warnings": _build_warnings(telemetry_validation, redaction_report, excluded_runs),
        "evidence": {
            "mode": redaction_report["policy"]["evidence_mode"],
            "raw_payload_retention": "disabled",
        },
    }
    package[PACKAGE_BUNDLE_HASH_FIELD] = compute_bundle_hash(package)
    return package


def compute_bundle_hash(package: dict[str, Any]) -> str:
    return hash_document(package, exclude_fields=(PACKAGE_BUNDLE_HASH_FIELD,))


def build_schema_index(schema_dir: str | Path | None = None) -> list[dict[str, Any]]:
    if schema_dir is not None:
        root = Path(schema_dir)
        if not root.is_dir():
            raise DataValidationError(f"Schema directory not found: {root}")
        return [
            file_fingerprint(path, logical_path=f"{SCHEMA_LOGICAL_ROOT}/{path.name}")
            for path in sorted(root.glob("*.schema.json"), key=lambda item: item.name)
        ]

    source_root = Path(__file__).resolve().parents[2] / SCHEMA_LOGICAL_ROOT
    if source_root.is_dir():
        return build_schema_index(source_root)

    installed_root = Path(sys.prefix) / SCHEMA_LOGICAL_ROOT
    if installed_root.is_dir():
        return build_schema_index(installed_root)

    try:
        packaged_root = resources.files("agent_eval_toolkit.resources.schemas")
    except ModuleNotFoundError as exc:
        raise DataValidationError("Published JSON Schemas were not found") from exc

    fingerprints: list[dict[str, Any]] = []
    for entry in sorted(
        (
            candidate
            for candidate in packaged_root.iterdir()
            if candidate.is_file() and candidate.name.endswith(".schema.json")
        ),
        key=lambda candidate: candidate.name,
    ):
        content = entry.read_bytes()
        fingerprints.append(
            {
                "path": f"{SCHEMA_LOGICAL_ROOT}/{entry.name}",
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        )
    if not fingerprints:
        raise DataValidationError("Published JSON Schemas were not found")
    return fingerprints


def verify_evaluation_package(
    package_path: str | Path,
    *,
    workflow_config_path: str | Path | None = None,
    raw_logs_path: str | Path | None = None,
    evaluation_manifest_path: str | Path | None = None,
    baseline_context_path: str | Path | None = None,
    mapping_assessment_path: str | Path | None = None,
    schema_dir: str | Path | None = None,
) -> dict[str, Any]:
    target = Path(package_path)
    if target.is_dir():
        target = target / "evaluation_package.json"
    try:
        package = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DataValidationError("Evaluation package was not found") from exc
    except json.JSONDecodeError as exc:
        raise DataValidationError("Evaluation package must be valid JSON") from exc
    if not isinstance(package, dict):
        raise DataValidationError("Evaluation package must be a JSON object")

    package_root = target.parent
    diagnostics: list[dict[str, Any]] = []
    input_paths: dict[str, str | Path | None] = {
        "workflow_config": workflow_config_path,
        "raw_logs": raw_logs_path,
        "evaluation_manifest": evaluation_manifest_path,
        "baseline_context": baseline_context_path,
        "mapping_assessment": mapping_assessment_path,
    }
    input_check = _verify_fingerprint_group(
        package.get("input_fingerprints"),
        input_paths,
        stage="package_input_hashes",
        diagnostics=diagnostics,
    )
    artifact_check = _verify_artifact_fingerprints(
        package.get("artifact_fingerprints"), package_root, diagnostics
    )
    schema_check = _verify_schema_fingerprints(
        package.get("schema_fingerprints"), schema_dir, diagnostics
    )
    event_check, raw_line_check = _verify_event_anchors(package_root, raw_logs_path, diagnostics)
    expected_bundle = package.get(PACKAGE_BUNDLE_HASH_FIELD)
    actual_bundle = compute_bundle_hash(package)
    bundle_passed = isinstance(expected_bundle, str) and expected_bundle == actual_bundle
    bundle_check = {
        "passed": bundle_passed,
        "expected_sha256": expected_bundle,
        "actual_sha256": actual_bundle,
    }
    if not bundle_passed:
        diagnostics.append(
            _hash_mismatch_diagnostic(
                "package_bundle", expected_bundle, actual_bundle, "package_bundle_hash"
            )
        )

    checks = {
        "input_hashes": input_check,
        "event_hashes": event_check,
        "raw_line_hashes": raw_line_check,
        "schema_hashes": schema_check,
        "artifact_hashes": artifact_check,
        "bundle_hash": bundle_check,
    }
    return {
        "verification_schema_version": "1.0.0",
        "passed": all(check["passed"] for check in checks.values()),
        "package": "evaluation_package.json",
        "checks": checks,
        "diagnostic_count": len(diagnostics),
        "diagnostics": diagnostics,
    }


def _verify_fingerprint_group(
    expected_group: Any,
    actual_paths: dict[str, str | Path | None],
    *,
    stage: str,
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    if not isinstance(expected_group, dict):
        mismatches.append({"field": "input_fingerprints", "reason": "missing_or_invalid"})
    else:
        for logical_name, expected in sorted(expected_group.items()):
            if not isinstance(expected, dict) or expected.get("path") != logical_name:
                mismatch = {
                    "field": logical_name,
                    "reason": "non_logical_source_identifier",
                }
                mismatches.append(mismatch)
                diagnostics.append(
                    _hash_mismatch_diagnostic(logical_name, _expected_hash(expected), None, stage)
                )
                continue
            path = actual_paths.get(logical_name)
            if path is None:
                mismatch = {"field": logical_name, "reason": "input_path_not_supplied"}
                mismatches.append(mismatch)
                diagnostics.append(
                    _hash_mismatch_diagnostic(logical_name, _expected_hash(expected), None, stage)
                )
                continue
            try:
                actual = file_fingerprint(path, logical_path=logical_name)
            except (FileNotFoundError, OSError):
                mismatch = {"field": logical_name, "reason": "input_unavailable"}
                mismatches.append(mismatch)
                diagnostics.append(
                    _hash_mismatch_diagnostic(logical_name, _expected_hash(expected), None, stage)
                )
                continue
            if not _fingerprint_matches(expected, actual):
                mismatch = _fingerprint_mismatch(logical_name, expected, actual)
                mismatches.append(mismatch)
                diagnostics.append(
                    _hash_mismatch_diagnostic(
                        logical_name,
                        _expected_hash(expected),
                        actual.get("sha256"),
                        stage,
                    )
                )
    return {
        "passed": not mismatches,
        "verified_count": (
            max(0, len(expected_group) - len(mismatches)) if isinstance(expected_group, dict) else 0
        ),
        "mismatches": mismatches,
    }


def _verify_artifact_fingerprints(
    expected_group: Any,
    package_root: Path,
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    fingerprints = expected_group if isinstance(expected_group, list) else []
    if not isinstance(expected_group, list):
        mismatches.append({"field": "artifact_fingerprints", "reason": "missing_or_invalid"})
    for expected in fingerprints:
        logical_path = expected.get("path") if isinstance(expected, dict) else None
        if not _safe_logical_path(logical_path):
            mismatch = {"field": str(logical_path), "reason": "unsafe_logical_path"}
            mismatches.append(mismatch)
            diagnostics.append(
                _hash_mismatch_diagnostic(
                    str(logical_path), _expected_hash(expected), None, "artifact_hashes"
                )
            )
            continue
        artifact_path = package_root / str(logical_path)
        try:
            actual = file_fingerprint(artifact_path, logical_path=str(logical_path))
        except (FileNotFoundError, OSError):
            mismatch = {"field": str(logical_path), "reason": "artifact_unavailable"}
            mismatches.append(mismatch)
            diagnostics.append(
                _hash_mismatch_diagnostic(
                    str(logical_path), _expected_hash(expected), None, "artifact_hashes"
                )
            )
            continue
        if not _fingerprint_matches(expected, actual):
            mismatches.append(_fingerprint_mismatch(str(logical_path), expected, actual))
            diagnostics.append(
                _hash_mismatch_diagnostic(
                    str(logical_path), _expected_hash(expected), actual["sha256"], "artifact_hashes"
                )
            )
    return {
        "passed": not mismatches,
        "verified_count": max(0, len(fingerprints) - len(mismatches)),
        "mismatches": mismatches,
    }


def _verify_schema_fingerprints(
    expected_group: Any,
    schema_dir: str | Path | None,
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = expected_group if isinstance(expected_group, list) else []
    mismatches: list[dict[str, Any]] = []
    try:
        actual = build_schema_index(schema_dir)
    except DataValidationError:
        actual = []
    expected_by_path = {
        item["path"]: item
        for item in expected
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    actual_by_path = {item["path"]: item for item in actual}
    for logical_path in sorted(set(expected_by_path) | set(actual_by_path)):
        expected_item = expected_by_path.get(logical_path)
        actual_item = actual_by_path.get(logical_path)
        if not _fingerprint_matches(expected_item, actual_item):
            mismatches.append(_fingerprint_mismatch(str(logical_path), expected_item, actual_item))
            diagnostics.append(
                _hash_mismatch_diagnostic(
                    str(logical_path),
                    _expected_hash(expected_item),
                    _expected_hash(actual_item),
                    "schema_hashes",
                )
            )
    if not isinstance(expected_group, list):
        mismatches.append({"field": "schema_fingerprints", "reason": "missing_or_invalid"})
    return {
        "passed": not mismatches,
        "verified_count": max(0, len(expected_by_path) - len(mismatches)),
        "mismatches": mismatches,
    }


def _verify_event_anchors(
    package_root: Path,
    raw_logs_path: str | Path | None,
    diagnostics: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    event_mismatches: list[dict[str, Any]] = []
    line_mismatches: list[dict[str, Any]] = []
    if raw_logs_path is None:
        reason = {"field": "raw_logs", "reason": "input_path_not_supplied"}
        event_mismatches.append(reason)
        line_mismatches.append(dict(reason))
    else:
        try:
            rows = read_jsonl_with_line_numbers(raw_logs_path)
            normalized = json.loads(
                (package_root / "normalized_events.json").read_text(encoding="utf-8")
            )
        except (DataValidationError, FileNotFoundError, json.JSONDecodeError):
            reason = {"field": "normalized_events", "reason": "anchor_source_unavailable"}
            event_mismatches.append(reason)
            line_mismatches.append(dict(reason))
        else:
            rows_by_line = {
                line_number: (payload, raw_line)
                for line_number, payload, raw_line in (unpack_jsonl_row(row) for row in rows)
            }
            if not isinstance(normalized, list):
                event_mismatches.append(
                    {"field": "normalized_events", "reason": "invalid_artifact_shape"}
                )
                line_mismatches.append(
                    {"field": "normalized_events", "reason": "invalid_artifact_shape"}
                )
            else:
                for event in normalized:
                    if not isinstance(event, dict) or not isinstance(event.get("evidence"), dict):
                        continue
                    event_id = str(event.get("event_id", "unknown"))
                    evidence = event["evidence"]
                    if evidence.get("source_file") != "raw_logs":
                        event_mismatches.append(
                            {
                                "event_id": event_id,
                                "field": "source_file",
                                "reason": "non_logical_source_identifier",
                            }
                        )
                        diagnostics.append(
                            _hash_mismatch_diagnostic(
                                "source_file",
                                "raw_logs",
                                evidence.get("source_file"),
                                "event_hashes",
                                event_id=event_id,
                            )
                        )
                    source_line = evidence.get("source_line")
                    row = rows_by_line.get(source_line)
                    if row is None:
                        mismatch = {
                            "event_id": event_id,
                            "source_line": source_line,
                            "reason": "source_line_unavailable",
                        }
                        event_mismatches.append(mismatch)
                        line_mismatches.append(dict(mismatch))
                        continue
                    payload, raw_line = row
                    actual_event_hash = hash_payload(payload)
                    if evidence.get("raw_event_hash") != actual_event_hash:
                        event_mismatches.append(
                            {
                                "event_id": event_id,
                                "source_line": source_line,
                                "expected_sha256": evidence.get("raw_event_hash"),
                                "actual_sha256": actual_event_hash,
                            }
                        )
                        diagnostics.append(
                            _hash_mismatch_diagnostic(
                                "raw_event_hash",
                                evidence.get("raw_event_hash"),
                                actual_event_hash,
                                "event_hashes",
                                event_id=event_id,
                            )
                        )
                    actual_line_hash = hash_raw_line(raw_line) if raw_line is not None else None
                    if evidence.get("raw_line_hash") != actual_line_hash:
                        line_mismatches.append(
                            {
                                "event_id": event_id,
                                "source_line": source_line,
                                "expected_sha256": evidence.get("raw_line_hash"),
                                "actual_sha256": actual_line_hash,
                            }
                        )
                        diagnostics.append(
                            _hash_mismatch_diagnostic(
                                "raw_line_hash",
                                evidence.get("raw_line_hash"),
                                actual_line_hash,
                                "raw_line_hashes",
                                event_id=event_id,
                            )
                        )
    return (
        {
            "passed": not event_mismatches,
            "verified_count": _anchor_verified_count(package_root, event_mismatches),
            "mismatches": event_mismatches,
        },
        {
            "passed": not line_mismatches,
            "verified_count": _anchor_verified_count(package_root, line_mismatches),
            "mismatches": line_mismatches,
        },
    )


def _anchor_verified_count(package_root: Path, mismatches: list[dict[str, Any]]) -> int:
    try:
        normalized = json.loads(
            (package_root / "normalized_events.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError):
        return 0
    anchor_count = (
        sum(
            isinstance(event, dict) and isinstance(event.get("evidence"), dict)
            for event in normalized
        )
        if isinstance(normalized, list)
        else 0
    )
    return max(0, anchor_count - len(mismatches))


def _fingerprint_matches(expected: Any, actual: Any) -> bool:
    return (
        isinstance(expected, dict)
        and isinstance(actual, dict)
        and expected.get("sha256") == actual.get("sha256")
        and expected.get("size_bytes") == actual.get("size_bytes")
    )


def _fingerprint_mismatch(logical_name: str, expected: Any, actual: Any) -> dict[str, Any]:
    return {
        "field": logical_name,
        "expected_sha256": _expected_hash(expected),
        "actual_sha256": _expected_hash(actual),
        "expected_size_bytes": expected.get("size_bytes") if isinstance(expected, dict) else None,
        "actual_size_bytes": actual.get("size_bytes") if isinstance(actual, dict) else None,
    }


def _expected_hash(fingerprint: Any) -> Any:
    return fingerprint.get("sha256") if isinstance(fingerprint, dict) else None


def _hash_mismatch_diagnostic(
    field: str,
    expected: Any,
    actual: Any,
    stage: str,
    *,
    event_id: str | None = None,
) -> dict[str, Any]:
    return make_diagnostic(
        "PACKAGE_HASH_MISMATCH",
        f"Package integrity verification failed for `{field}`.",
        stage,
        field=field,
        event_id=event_id,
        details={"expected_sha256": expected, "actual_sha256": actual},
    )


def _safe_logical_path(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def _find_dependency_lock() -> Path | None:
    """Find the project lock without making installed packages depend on its presence."""
    candidates = [Path.cwd() / "uv.lock", Path(__file__).resolve().parents[2] / "uv.lock"]
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _build_warnings(
    telemetry_validation: dict[str, Any],
    redaction_report: dict[str, Any],
    excluded_runs: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    if not telemetry_validation["passed"]:
        warnings.append("Required telemetry coverage did not pass.")
    if redaction_report["redacted_field_counts"]:
        warnings.append("One or more restricted fields were redacted from evidence snapshots.")
    if redaction_report["unprotected_restricted_fields"]:
        warnings.append("Restricted fields are configured without redaction protection.")
    if excluded_runs:
        warnings.append("One or more reconstructed runs were excluded from the measurement window.")
    return warnings
