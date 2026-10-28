from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any, Sequence

from .diagnostics import (
    canonical_validation_diagnostics,
    diagnostic_document,
    diagnostic_from_exception,
    make_diagnostic,
    telemetry_validation_diagnostics,
)
from .evaluation import build_measurement_window_summary, filter_runs_by_measurement_window
from .exceptions import DataValidationError, ToolkitError
from .io_utils import read_json, read_jsonl_with_line_numbers, write_json, write_text
from .langfuse import prepare_langfuse
from .mapping import assess_mapping
from .metrics import build_scorecard
from .models import to_dict
from .normalize import normalize_logs
from .opentelemetry import prepare_opentelemetry  # pragma: no cover -- optional adapter glue
from .package import build_evaluation_package, verify_evaluation_package
from .readiness import blocked_readiness, build_evaluation_readiness, resolve_minimum_tier
from .reconstruct import reconstruct_runs_with_quarantine
from .report import render_markdown_report
from .result_exports import render_monitoring_summary_csv, render_telemetry_coverage_csv
from .traceability import (
    build_redaction_report,
    configured_restricted_canaries,
    scan_derived_bundle,
)
from .validation import (
    validate_baseline_context,
    validate_canonical_rows,
    validate_evaluation_manifest,
    validate_telemetry_requirements,
    validate_workflow_config,
)

LOGGER = logging.getLogger(__name__)
OUTPUT_ARTIFACTS = [
    "input_validation.json",
    "normalized_events.json",
    "workflow_runs.json",
    "excluded_runs.json",
    "telemetry_validation.json",
    "scorecard.json",
    "redaction_report.json",
    "monitoring_summary.csv",
    "telemetry_coverage.csv",
    "pilot_report.md",
    "enterprise_evaluation_report.md",
    "evaluation_readiness.json",
    "quarantined_runs.json",
    "diagnostics.json",
]
KNOWN_OUTPUT_ARTIFACTS = [*OUTPUT_ARTIFACTS, "evaluation_package.json"]
DIAGNOSTIC_ARTIFACTS = ["diagnostics.json"]
MAPPING_ONLY_ARTIFACTS = [
    "input_validation.json",
    "excluded_runs.json",
    "telemetry_validation.json",
    "redaction_report.json",
    "telemetry_coverage.csv",
    "evaluation_readiness.json",
    "quarantined_runs.json",
    "diagnostics.json",
]
BLOCKED_ARTIFACTS = ["evaluation_readiness.json", "diagnostics.json"]


def run_pipeline(
    workflow_config_path: str,
    raw_logs_path: str,
    evaluation_manifest_path: str,
    output_dir: str,
    baseline_context_path: str | None = None,
    mapping_assessment_path: str | None = None,
    minimum_tier_override: str | None = None,
    overwrite: bool = False,
    captured_output: dict[str, str] | None = None,
) -> dict[str, Any]:
    output_root = Path(output_dir)
    _prepare_output_root(output_root, overwrite=overwrite)
    output_root.parent.mkdir(parents=True, exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix=".agent-eval-stage-", dir=str(output_root.parent)))
    stage = "workflow_config"
    diagnostics: list[dict[str, Any]] = []
    quarantined_runs: list[dict[str, Any]] = []
    canaries: list[tuple[str, str]] = []
    minimum_tier = minimum_tier_override or "complete"
    path_aliases = _input_path_aliases(
        workflow_config_path,
        raw_logs_path,
        evaluation_manifest_path,
        baseline_context_path,
        mapping_assessment_path,
    )

    try:
        workflow_config = read_json(workflow_config_path)
        canaries = configured_restricted_canaries(workflow_config)
        validate_workflow_config(workflow_config)

        stage = "evaluation_manifest"
        evaluation_manifest = read_json(evaluation_manifest_path)
        validated_evaluation_manifest = validate_evaluation_manifest(evaluation_manifest)
        minimum_tier = resolve_minimum_tier(validated_evaluation_manifest, minimum_tier_override)
        mapping_assessment = _load_mapping_assessment(mapping_assessment_path)

        stage = "raw_logs"
        raw_rows = read_jsonl_with_line_numbers(raw_logs_path)

        stage = "baseline_context"
        baseline_context = read_json(baseline_context_path) if baseline_context_path else None
        validated_baseline_context = validate_baseline_context(baseline_context)

        stage = "canonical_validation"
        canonical_validation = validate_canonical_rows(raw_rows, workflow_config)
        canonical_diagnostics = canonical_validation_diagnostics(
            canonical_validation, raw_rows, workflow_config
        )
        input_validation = {
            "passed": canonical_validation["passed"],
            "canonical_validation": canonical_validation,
            "workflow_config_valid": True,
            "evaluation_manifest_valid": True,
            "baseline_context_valid": True,
            "diagnostics": canonical_diagnostics,
        }
        write_json(stage_root / "input_validation.json", input_validation)
        if not canonical_validation["passed"]:
            diagnostics.extend(canonical_diagnostics)
            raise DataValidationError("Canonical input validation failed")

        stage = "normalization"
        normalized_events = normalize_logs(raw_rows, workflow_config, "raw_logs")

        stage = "reconstruction"
        all_workflow_runs, reconstruction_quarantine = reconstruct_runs_with_quarantine(
            normalized_events, workflow_config
        )
        quarantined_runs.extend(reconstruction_quarantine)

        stage = "measurement_window"
        workflow_runs, excluded_runs = filter_runs_by_measurement_window(
            all_workflow_runs, validated_evaluation_manifest
        )
        measurement_window_summary = build_measurement_window_summary(
            workflow_runs, excluded_runs, validated_evaluation_manifest
        )
        measurement_window_summary["source_run_candidate_count"] = len(all_workflow_runs) + len(
            quarantined_runs
        )
        measurement_window_summary["quarantined_run_count"] = len(quarantined_runs)
        measurement_window_summary["analytically_eligible_run_count"] = len(workflow_runs)

        stage = "telemetry_validation"
        telemetry_validation = validate_telemetry_requirements(
            workflow_runs, validated_evaluation_manifest, workflow_config
        )
        invalid_run_ids = {
            str(item["run_id"])
            for item in telemetry_validation["configuration_consistency"]["invalid_runs"]
            if item.get("run_id") is not None
        }
        invalid_run_ids.update(telemetry_validation["missing_required_by_run"])
        if invalid_run_ids:
            retained_runs = []
            for run in workflow_runs:
                if run.run_id not in invalid_run_ids:
                    retained_runs.append(run)
                    continue
                missing_signals = telemetry_validation["missing_required_by_run"].get(
                    run.run_id, []
                )
                quarantined_runs.append(
                    {
                        "run_id": run.run_id,
                        "reason": (
                            "required_per_run_telemetry_missing"
                            if missing_signals
                            else "configuration_identity_invalid"
                        ),
                        "missing_required_signals": missing_signals,
                        "event_ids": run.event_ids,
                        "trace_ids": run.trace_ids,
                        "quarantine_stage": "telemetry_validation",
                    }
                )
            workflow_runs = retained_runs
            telemetry_validation = validate_telemetry_requirements(
                workflow_runs, validated_evaluation_manifest, workflow_config
            )
        measurement_window_summary["quarantined_run_count"] = len(quarantined_runs)
        measurement_window_summary["analytically_eligible_run_count"] = len(workflow_runs)
        redaction_report = build_redaction_report(normalized_events, workflow_config)
        write_json(stage_root / "excluded_runs.json", excluded_runs)
        write_json(stage_root / "telemetry_validation.json", telemetry_validation)
        write_json(stage_root / "redaction_report.json", redaction_report)
        write_json(stage_root / "quarantined_runs.json", quarantined_runs)
        write_text(
            stage_root / "telemetry_coverage.csv",
            render_telemetry_coverage_csv(telemetry_validation),
        )
        diagnostics.extend(_quarantine_diagnostics(quarantined_runs))
        diagnostics.extend(_degraded_telemetry_diagnostics(telemetry_validation))
        if not redaction_report["passed"]:
            unprotected = redaction_report["unprotected_restricted_fields"]
            diagnostics.append(
                make_diagnostic(
                    "PRIVACY_RESTRICTED_MAPPING",
                    "Restricted fields are not protected by redaction.",
                    "redaction_validation",
                    field=unprotected[0] if unprotected else "restricted_fields",
                    details={"unprotected_fields": unprotected},
                )
            )
            raise DataValidationError(
                "Restricted fields are not protected by redaction: " f"{', '.join(unprotected)}"
            )

        diagnostics.extend(
            _mapping_readiness_diagnostics(
                validated_evaluation_manifest,
                mapping_assessment,
            )
        )
        readiness = build_evaluation_readiness(
            telemetry_validation,
            validated_evaluation_manifest,
            run_count=len(workflow_runs),
            quarantined_run_count=len(quarantined_runs),
            mapping_assessment=mapping_assessment,
            governance_safe=True,
            minimum_tier=minimum_tier,
        )
        diagnostics.extend(_tier_diagnostics(readiness))
        write_json(stage_root / "evaluation_readiness.json", readiness)
        write_json(
            stage_root / "diagnostics.json",
            diagnostic_document(
                diagnostics,
                stage="evidence_readiness",
                passed=readiness["minimum_tier_met"],
            ),
        )

        if readiness["attained_tier"] in {"blocked", "mapping_only"}:
            stage = "privacy_scan"
            privacy_scan = scan_derived_bundle(
                stage_root, canaries, captured_output=captured_output
            )
            if not privacy_scan["passed"]:
                raise DataValidationError("Restricted canary detected in derived readiness bundle")
            stage = "publish"
            artifact_set = (
                BLOCKED_ARTIFACTS
                if readiness["attained_tier"] == "blocked"
                else MAPPING_ONLY_ARTIFACTS
            )
            _publish_bundle(stage_root, output_root, artifact_set)
            return readiness

        stage = "scorecard"
        scorecard = build_scorecard(
            workflow_runs,
            validated_evaluation_manifest,
            validated_baseline_context,
            telemetry_validation,
            measurement_window_summary,
            readiness,
        )
        report_text = render_markdown_report(scorecard, workflow_runs)

        LOGGER.info("Staging pipeline outputs for %s", output_root)
        write_json(stage_root / "normalized_events.json", normalized_events)
        write_json(stage_root / "workflow_runs.json", workflow_runs)
        write_json(stage_root / "scorecard.json", scorecard)
        write_text(stage_root / "monitoring_summary.csv", render_monitoring_summary_csv(scorecard))
        write_text(stage_root / "pilot_report.md", report_text)
        write_text(stage_root / "enterprise_evaluation_report.md", report_text)

        stage = "package_build"
        evaluation_package = build_evaluation_package(
            workflow_config_path=workflow_config_path,
            raw_logs_path=raw_logs_path,
            evaluation_manifest_path=evaluation_manifest_path,
            baseline_context_path=baseline_context_path,
            mapping_assessment_path=mapping_assessment_path,
            output_dir=str(stage_root),
            scorecard=to_dict(scorecard),
            telemetry_validation=telemetry_validation,
            redaction_report=redaction_report,
            excluded_runs=excluded_runs,
            artifact_names=OUTPUT_ARTIFACTS,
        )
        write_json(stage_root / "evaluation_package.json", evaluation_package)

        stage = "privacy_scan"
        privacy_scan = scan_derived_bundle(stage_root, canaries, captured_output=captured_output)
        if not privacy_scan["passed"]:
            diagnostics.extend(
                make_diagnostic(
                    "PRIVACY_RESTRICTED_MAPPING",
                    "A configured restricted canary was found in a derived output.",
                    "privacy_scan",
                    field=match["location"],
                    details={"canary_id": match["canary_id"]},
                )
                for match in privacy_scan["matches"]
            )
            raise DataValidationError("Restricted canary detected in derived evaluation bundle")

        stage = "publish"
        _publish_bundle(stage_root, output_root, KNOWN_OUTPUT_ARTIFACTS)
        return readiness
    except ToolkitError as exc:
        exception_diagnostic = diagnostic_from_exception(
            exc,
            stage,
            path_aliases=path_aliases,
            restricted_values=(value for _label, value in canaries),
        )
        if not diagnostics or exception_diagnostic["message"] not in {
            item["message"] for item in diagnostics
        }:
            diagnostics.append(exception_diagnostic)
        exc.__dict__["diagnostics"] = diagnostics
        exc.__dict__["diagnostic_stage"] = stage
        if not (stage_root / "evaluation_readiness.json").exists():
            write_json(
                stage_root / "evaluation_readiness.json",
                blocked_readiness(minimum_tier, f"pipeline_blocked_during_{stage}"),
            )
        _publish_failure_diagnostics(
            output_root,
            stage_root,
            diagnostic_document(diagnostics, stage=stage),
            canaries,
            captured_output=captured_output,
        )
        raise
    except Exception as exc:
        diagnostic = diagnostic_from_exception(
            exc,
            stage,
            path_aliases=path_aliases,
            restricted_values=(value for _label, value in canaries),
        )
        if not (stage_root / "evaluation_readiness.json").exists():
            write_json(
                stage_root / "evaluation_readiness.json",
                blocked_readiness(minimum_tier, f"pipeline_blocked_during_{stage}"),
            )
        _publish_failure_diagnostics(
            output_root,
            stage_root,
            diagnostic_document([diagnostic], stage=stage),
            canaries,
            captured_output=captured_output,
        )
        wrapped = DataValidationError(f"Evaluation pipeline failed during `{stage}`")
        wrapped.__dict__["diagnostics"] = [diagnostic]
        wrapped.__dict__["diagnostic_stage"] = stage
        raise wrapped from exc
    finally:
        if stage_root.exists():
            shutil.rmtree(stage_root)


def _prepare_output_root(output_root: Path, *, overwrite: bool) -> None:
    if output_root.exists() and not output_root.is_dir():
        raise DataValidationError(f"Output path is not a directory: {output_root}")
    existing = [
        name
        for name in [*KNOWN_OUTPUT_ARTIFACTS, *DIAGNOSTIC_ARTIFACTS]
        if (output_root / name).exists()
    ]
    if existing and not overwrite:
        raise DataValidationError(
            "Output directory already contains evaluation artifacts; use --overwrite to replace: "
            + ", ".join(sorted(existing))
        )


def _publish_failure_diagnostics(
    output_root: Path,
    stage_root: Path,
    document: dict[str, Any],
    canaries: list[tuple[str, str]],
    *,
    captured_output: dict[str, str] | None,
) -> None:
    diagnostic_root = Path(
        tempfile.mkdtemp(prefix=".agent-eval-diagnostics-", dir=str(output_root.parent))
    )
    try:
        write_json(diagnostic_root / "diagnostics.json", document)
        artifact_names = ["diagnostics.json"]
        readiness = stage_root / "evaluation_readiness.json"
        if readiness.is_file():
            shutil.copy2(readiness, diagnostic_root / "evaluation_readiness.json")
            artifact_names.append("evaluation_readiness.json")
        diagnostic_scan = scan_derived_bundle(
            diagnostic_root, canaries, captured_output=captured_output
        )
        if not diagnostic_scan["passed"]:
            safe_diagnostics = [
                make_diagnostic(
                    "PRIVACY_RESTRICTED_MAPPING",
                    "A restricted value was removed from failure diagnostics.",
                    "diagnostic_privacy_scan",
                    field=match["location"],
                    details={"canary_id": match["canary_id"]},
                )
                for match in diagnostic_scan["matches"]
            ]
            write_json(
                diagnostic_root / "diagnostics.json",
                diagnostic_document(safe_diagnostics, stage="diagnostic_privacy_scan"),
            )
            (diagnostic_root / "evaluation_readiness.json").unlink(missing_ok=True)
            artifact_names = ["diagnostics.json"]
        _publish_bundle(diagnostic_root, output_root, artifact_names)
    finally:
        if diagnostic_root.exists():
            shutil.rmtree(diagnostic_root)


def _publish_bundle(bundle_root: Path, output_root: Path, artifact_names: list[str]) -> None:
    missing = [name for name in artifact_names if not (bundle_root / name).is_file()]
    if missing:
        raise DataValidationError(
            "Staged evaluation bundle is incomplete: " + ", ".join(sorted(missing))
        )

    excluded_names = set(KNOWN_OUTPUT_ARTIFACTS) | set(DIAGNOSTIC_ARTIFACTS)
    replacement = Path(tempfile.mkdtemp(prefix=".agent-eval-publish-", dir=str(output_root.parent)))
    backup: Path | None = None
    try:
        if output_root.exists():
            for child in output_root.iterdir():
                if child.name in excluded_names:
                    continue
                destination = replacement / child.name
                if child.is_symlink():
                    destination.symlink_to(os.readlink(child))
                elif child.is_dir():
                    shutil.copytree(child, destination, symlinks=True)
                else:
                    shutil.copy2(child, destination)
        for artifact_name in artifact_names:
            os.replace(bundle_root / artifact_name, replacement / artifact_name)

        if not output_root.exists():
            os.replace(replacement, output_root)
            return

        backup = Path(tempfile.mkdtemp(prefix=".agent-eval-backup-", dir=str(output_root.parent)))
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
        if replacement.exists():
            shutil.rmtree(replacement)
        if backup is not None and backup.exists() and not output_root.exists():
            os.replace(backup, output_root)
        elif backup is not None and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)


def _input_path_aliases(
    workflow_config_path: str,
    raw_logs_path: str,
    evaluation_manifest_path: str,
    baseline_context_path: str | None,
    mapping_assessment_path: str | None = None,
) -> dict[str, str]:
    values: list[tuple[str | None, str]] = [
        (workflow_config_path, "workflow_config"),
        (raw_logs_path, "raw_logs"),
        (evaluation_manifest_path, "evaluation_manifest"),
        (baseline_context_path, "baseline_context"),
        (mapping_assessment_path, "mapping_assessment"),
    ]
    aliases: dict[str, str] = {}
    for path, logical_name in values:
        if path is None:
            continue
        aliases[str(path)] = logical_name
        aliases[str(Path(path).resolve())] = logical_name
    return aliases


def _load_mapping_assessment(path: str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    value = read_json(path)
    if not isinstance(value, dict):
        raise DataValidationError("Mapping assessment must be a JSON object")
    readiness = value.get("mapping_readiness")
    if readiness not in {"blocked", "provisional", "verified"}:
        raise DataValidationError(
            "Mapping assessment must declare blocked, provisional, or verified readiness"
        )
    for field in ("mapping_spec_sha256", "source_profile_sha256"):
        digest = value.get(field)
        if not isinstance(digest, str) or len(digest) != 64:
            raise DataValidationError(f"Mapping assessment `{field}` must be a SHA-256 digest")
    return value


def _quarantine_diagnostics(
    quarantined_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for item in quarantined_runs:
        run_id = str(item["run_id"])
        if item["quarantine_stage"] == "reconstruction":
            diagnostic = diagnostic_from_exception(
                DataValidationError(str(item["reason"])), "reconstruction"
            )
            diagnostic.update(
                {
                    "severity": "warning",
                    "blocking_scope": "run",
                    "affected_outputs": [
                        "scorecard.json",
                        "monitoring_summary.csv",
                        "evaluation_report.md",
                    ],
                    "evidence_tier_impact": "caps_at_partial",
                    "remediation_code": "REPAIR_RUN_BOUNDARIES",
                    "recommended_action": (
                        "Correct the affected run boundaries and rerun the assessment."
                    ),
                    "run_id": run_id,
                }
            )
            diagnostics.append(diagnostic)
            continue
        missing = item.get("missing_required_signals", [])
        diagnostics.append(
            make_diagnostic(
                ("TELEMETRY_REQUIRED_SIGNAL_MISSING" if missing else "CONFIGURATION_DRIFT"),
                (
                    "The run was quarantined because required per-run telemetry is missing."
                    if missing
                    else "The run was quarantined because configuration identity is invalid."
                ),
                "telemetry_validation",
                severity="warning",
                blocking_scope="run",
                affected_outputs=[
                    "scorecard.json",
                    "monitoring_summary.csv",
                    "evaluation_report.md",
                ],
                evidence_tier_impact="caps_at_partial",
                remediation_code=(
                    "INSTRUMENT_REQUIRED_SIGNAL" if missing else "RECONCILE_CONFIGURATION_IDENTITY"
                ),
                recommended_action=(
                    "Instrument the missing signals and re-export the affected run."
                    if missing
                    else "Use one manifest-declared configuration identity throughout the run."
                ),
                field=missing[0] if missing else "configuration",
                run_id=run_id,
                details={"missing_required_signals": missing},
            )
        )
    return diagnostics


def _degraded_telemetry_diagnostics(
    telemetry_validation: dict[str, Any],
) -> list[dict[str, Any]]:
    if telemetry_validation.get("passed") is True:
        return []
    diagnostics = telemetry_validation_diagnostics(telemetry_validation)
    for item in diagnostics:
        item.update(
            {
                "severity": "warning",
                "blocking_scope": (
                    "claim"
                    if item["code"] in {"CONFIGURATION_DRIFT", "CONFIGURATION_UNEXPECTED"}
                    else "metric"
                ),
                "affected_outputs": ["scorecard.json", "evaluation_report.md"],
                "evidence_tier_impact": "caps_at_partial",
                "remediation_code": (
                    "RECONCILE_CONFIGURATION_IDENTITY"
                    if item["code"] in {"CONFIGURATION_DRIFT", "CONFIGURATION_UNEXPECTED"}
                    else "INSTRUMENT_REQUIRED_SIGNAL"
                ),
                "recommended_action": (
                    "Reconcile the observed and expected configuration matrix."
                    if item["code"] in {"CONFIGURATION_DRIFT", "CONFIGURATION_UNEXPECTED"}
                    else "Instrument the missing signal or withhold the affected metric."
                ),
            }
        )
    return diagnostics


def _mapping_readiness_diagnostics(
    manifest: dict[str, Any], mapping_assessment: dict[str, Any] | None
) -> list[dict[str, Any]]:
    if not manifest["evidence_context"]["mapping_assessment_required"]:
        return []
    readiness = (
        str(mapping_assessment["mapping_readiness"])
        if mapping_assessment is not None
        else "not_assessed"
    )
    if readiness == "verified":
        return []
    return [
        make_diagnostic(
            "MAPPING_ASSESSMENT_REQUIRED",
            f"Enterprise mapping readiness is `{readiness}`.",
            "mapping_assessment",
            severity="warning",
            blocking_scope="mapping",
            affected_outputs=["scorecard.json", "evaluation_report.md"],
            evidence_tier_impact="caps_at_mapping_only",
            remediation_code="VERIFY_ENTERPRISE_MAPPING",
            recommended_action=(
                "Complete source-path, semantic, transformation, boundary, status, and privacy "
                "approval checks in the mapping assessment."
            ),
            field="mapping_readiness",
        )
    ]


def _tier_diagnostics(readiness: dict[str, Any]) -> list[dict[str, Any]]:
    attained = str(readiness["attained_tier"])
    diagnostics: list[dict[str, Any]] = []
    if attained == "blocked":
        diagnostics.append(
            make_diagnostic(
                "EVIDENCE_TIER_BLOCKED",
                "Safety or integrity evidence prevents publication beyond readiness diagnostics.",
                "evidence_readiness",
                blocking_scope="bundle",
                affected_outputs=["scorecard.json", "evaluation_report.md"],
                evidence_tier_impact="blocked",
                remediation_code="RESOLVE_BLOCKING_SAFETY_OR_INTEGRITY_GAP",
                recommended_action=(
                    "Resolve the reported privacy, integrity, identity, or source-population gap."
                ),
            )
        )
    elif attained == "partial":
        diagnostics.append(
            make_diagnostic(
                "EVIDENCE_TIER_PARTIAL",
                "A bounded partial analytical result was produced.",
                "evidence_readiness",
                severity="warning",
                blocking_scope="claim",
                affected_outputs=["scorecard.json", "evaluation_report.md"],
                evidence_tier_impact="partial",
                remediation_code="REVIEW_WITHHELD_METRICS",
                recommended_action=(
                    "Review metric-level coverage and withholding reasons before interpretation."
                ),
            )
        )
    elif attained == "mapping_only":
        diagnostics.append(
            make_diagnostic(
                "EVIDENCE_TIER_MAPPING_ONLY",
                "Only mapping and evidence-readiness outputs are publishable.",
                "evidence_readiness",
                severity="warning",
                blocking_scope="mapping",
                affected_outputs=["scorecard.json", "evaluation_report.md"],
                evidence_tier_impact="mapping_only",
                remediation_code="COMPLETE_MAPPING_OR_TELEMETRY_GAPS",
                recommended_action=(
                    "Resolve mapping approvals, quarantined runs, and missing analytical evidence."
                ),
            )
        )
    if not readiness["minimum_tier_met"]:
        diagnostics.append(
            make_diagnostic(
                "EVIDENCE_MINIMUM_TIER_NOT_MET",
                "The attained evidence tier is below the configured publication minimum.",
                "evidence_readiness",
                blocking_scope="claim",
                affected_outputs=["evaluation_result"],
                evidence_tier_impact="below_configured_minimum",
                remediation_code="MEET_OR_REVISE_MINIMUM_TIER",
                recommended_action=(
                    "Resolve the reported evidence gaps or explicitly revise the minimum tier."
                ),
                field="publication_policy.minimum_evidence_tier",
            )
        )
    return diagnostics


def validate_inputs(
    workflow_config_path: str,
    raw_logs_path: str,
    evaluation_manifest_path: str,
    baseline_context_path: str | None = None,
    mapping_assessment_path: str | None = None,
    minimum_tier_override: str | None = None,
) -> dict[str, object]:
    workflow_config = read_json(workflow_config_path)
    validate_workflow_config(workflow_config)
    evaluation_manifest = read_json(evaluation_manifest_path)
    validated_evaluation_manifest = validate_evaluation_manifest(evaluation_manifest)
    minimum_tier = resolve_minimum_tier(validated_evaluation_manifest, minimum_tier_override)
    mapping_assessment = _load_mapping_assessment(mapping_assessment_path)
    raw_rows = read_jsonl_with_line_numbers(raw_logs_path)
    baseline_context = read_json(baseline_context_path) if baseline_context_path else None
    validate_baseline_context(baseline_context)

    canonical_validation = validate_canonical_rows(raw_rows, workflow_config)
    normalized_events = normalize_logs(raw_rows, workflow_config, "raw_logs")
    all_workflow_runs, quarantined_runs = reconstruct_runs_with_quarantine(
        normalized_events, workflow_config
    )
    workflow_runs, excluded_runs = filter_runs_by_measurement_window(
        all_workflow_runs, validated_evaluation_manifest
    )
    telemetry_validation = validate_telemetry_requirements(
        workflow_runs, validated_evaluation_manifest, workflow_config
    )
    invalid_run_ids = {
        str(item["run_id"])
        for item in telemetry_validation["configuration_consistency"]["invalid_runs"]
        if item.get("run_id") is not None
    }
    invalid_run_ids.update(telemetry_validation["missing_required_by_run"])
    if invalid_run_ids:
        retained_runs = []
        for run in workflow_runs:
            if run.run_id not in invalid_run_ids:
                retained_runs.append(run)
                continue
            missing_signals = telemetry_validation["missing_required_by_run"].get(run.run_id, [])
            quarantined_runs.append(
                {
                    "run_id": run.run_id,
                    "reason": (
                        "required_per_run_telemetry_missing"
                        if missing_signals
                        else "configuration_identity_invalid"
                    ),
                    "missing_required_signals": missing_signals,
                    "event_ids": run.event_ids,
                    "trace_ids": run.trace_ids,
                    "quarantine_stage": "telemetry_validation",
                }
            )
        workflow_runs = retained_runs
        telemetry_validation = validate_telemetry_requirements(
            workflow_runs, validated_evaluation_manifest, workflow_config
        )
    redaction_report = build_redaction_report(normalized_events, workflow_config)
    measurement_window_summary = build_measurement_window_summary(
        workflow_runs, excluded_runs, validated_evaluation_manifest
    )

    readiness = build_evaluation_readiness(
        telemetry_validation,
        validated_evaluation_manifest,
        run_count=len(workflow_runs),
        quarantined_run_count=len(quarantined_runs),
        mapping_assessment=mapping_assessment,
        governance_safe=bool(redaction_report["passed"]),
        minimum_tier=minimum_tier,
    )
    passed = canonical_validation["passed"] and readiness["minimum_tier_met"]
    diagnostics = canonical_validation_diagnostics(canonical_validation, raw_rows, workflow_config)
    diagnostics.extend(_degraded_telemetry_diagnostics(telemetry_validation))
    diagnostics.extend(_quarantine_diagnostics(quarantined_runs))
    diagnostics.extend(
        _mapping_readiness_diagnostics(validated_evaluation_manifest, mapping_assessment)
    )
    diagnostics.extend(_tier_diagnostics(readiness))
    if not redaction_report["passed"]:
        diagnostics.append(
            make_diagnostic(
                "PRIVACY_RESTRICTED_MAPPING",
                "Restricted fields are not protected by redaction.",
                "redaction_validation",
                field="restricted_fields",
            )
        )
    return {
        "passed": passed,
        "canonical_validation": canonical_validation,
        "telemetry_validation": telemetry_validation,
        "redaction_report": redaction_report,
        "measurement_window_summary": measurement_window_summary,
        "taxonomy": validated_evaluation_manifest["taxonomy"],
        "evaluation_readiness": readiness,
        "quarantined_runs": quarantined_runs,
        "diagnostics": diagnostics,
    }


def init_evaluation(output_dir: str) -> None:
    target_root = Path(output_dir)
    target_root.mkdir(parents=True, exist_ok=True)
    for template_entry in _template_entries():
        target_path = target_root / template_entry.name
        if target_path.exists():
            raise DataValidationError(f"Refusing to overwrite existing file: {target_path}")
        target_path.write_bytes(template_entry.read_bytes())


def list_templates() -> list[str]:
    return sorted(template_entry.name for template_entry in _template_entries())


def _template_entries() -> list[Any]:
    templates_root = Path(__file__).resolve().parents[2] / "templates"
    if templates_root.exists():
        return sorted(
            (path for path in templates_root.iterdir() if path.is_file()),
            key=lambda path: path.name,
        )

    try:
        packaged_templates = resources.files("agent_eval_toolkit.resources.templates")
    except ModuleNotFoundError as exc:
        raise DataValidationError(
            "Templates directory was not found in source or package data"
        ) from exc
    return sorted(
        (
            entry
            for entry in packaged_templates.iterdir()
            if entry.is_file() and entry.name != "__init__.py"
        ),
        key=lambda entry: entry.name,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enterprise AI workflow evaluation toolkit")
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level for CLI execution",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the pilot workflow evaluation pipeline")
    run_parser.add_argument("--workflow-config", required=True)
    run_parser.add_argument("--raw-logs", required=True)
    run_parser.add_argument("--evaluation-manifest", required=True)
    run_parser.add_argument("--output-dir", required=True)
    run_parser.add_argument("--baseline-context")
    run_parser.add_argument("--mapping-assessment")
    run_parser.add_argument("--minimum-tier", choices=["mapping_only", "partial", "complete"])
    run_parser.add_argument("--overwrite", action="store_true")

    validate_parser = subparsers.add_parser(
        "validate", help="Validate evaluation inputs without generating scorecards"
    )
    validate_parser.add_argument("--workflow-config", required=True)
    validate_parser.add_argument("--raw-logs", required=True)
    validate_parser.add_argument("--evaluation-manifest", required=True)
    validate_parser.add_argument("--baseline-context")
    validate_parser.add_argument("--mapping-assessment")
    validate_parser.add_argument("--minimum-tier", choices=["mapping_only", "partial", "complete"])
    validate_parser.add_argument("--output-json")

    init_parser = subparsers.add_parser(
        "init-evaluation", help="Scaffold an enterprise evaluation folder"
    )
    init_parser.add_argument("--output-dir", required=True)

    langfuse_parser = subparsers.add_parser(
        "prepare-langfuse",
        help="Prepare an offline Langfuse JSON/JSONL export for evaluation",
    )
    langfuse_parser.add_argument("--input", required=True)
    langfuse_parser.add_argument("--mapping", required=True)
    langfuse_parser.add_argument("--output-dir", required=True)
    langfuse_parser.add_argument("--activity-log")
    langfuse_parser.add_argument("--overwrite", action="store_true")

    opentelemetry_parser = subparsers.add_parser(  # pragma: no cover -- optional adapter glue
        "prepare-opentelemetry",
        help="Prepare an offline OTLP/JSON log export for evaluation",
    )
    opentelemetry_parser.add_argument(  # pragma: no cover -- optional adapter glue
        "--input", required=True
    )
    opentelemetry_parser.add_argument(  # pragma: no cover -- optional adapter glue
        "--mapping", required=True
    )
    opentelemetry_parser.add_argument(  # pragma: no cover -- optional adapter glue
        "--evaluation-manifest", required=True
    )
    opentelemetry_parser.add_argument(  # pragma: no cover -- optional adapter glue
        "--output-dir", required=True
    )
    opentelemetry_parser.add_argument("--activity-log")  # pragma: no cover -- optional adapter glue
    opentelemetry_parser.add_argument(  # pragma: no cover -- optional adapter glue
        "--overwrite", action="store_true"
    )

    mapping_parser = subparsers.add_parser(
        "assess-mapping",
        help="Assess vendor-neutral source-to-canonical mapping readiness and friction",
    )
    mapping_parser.add_argument("--source-profile", required=True)
    mapping_parser.add_argument("--mapping-spec", required=True)
    mapping_parser.add_argument("--evaluation-manifest", required=True)
    mapping_parser.add_argument("--activity-log")
    mapping_parser.add_argument("--output-dir", required=True)
    mapping_parser.add_argument("--overwrite", action="store_true")

    verify_parser = subparsers.add_parser(
        "verify-package",
        help="Recompute all integrity hashes in an evaluation package",
    )
    package_location = verify_parser.add_mutually_exclusive_group(required=True)
    package_location.add_argument("--package", "--evaluation-package", dest="package")
    package_location.add_argument("--package-dir")
    verify_parser.add_argument("--workflow-config", required=True)
    verify_parser.add_argument("--raw-logs", required=True)
    verify_parser.add_argument("--evaluation-manifest", required=True)
    verify_parser.add_argument("--baseline-context")
    verify_parser.add_argument("--mapping-assessment")
    verify_parser.add_argument("--schemas-dir")
    verify_parser.add_argument("--output-json")

    subparsers.add_parser("list-templates", help="List bundled evaluation templates")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()  # pragma: no cover -- public-interface setup
    if argv is None:
        args = parser.parse_args()
    else:
        args = parser.parse_args(argv)
    logging.basicConfig(  # pragma: no cover -- public-interface setup
        level=getattr(logging, args.log_level), format="%(levelname)s %(name)s: %(message)s"
    )

    try:
        if args.command == "run":
            readiness = run_pipeline(
                workflow_config_path=args.workflow_config,
                raw_logs_path=args.raw_logs,
                evaluation_manifest_path=args.evaluation_manifest,
                output_dir=args.output_dir,
                baseline_context_path=args.baseline_context,
                mapping_assessment_path=args.mapping_assessment,
                minimum_tier_override=args.minimum_tier,
                overwrite=args.overwrite,
            )
            if isinstance(readiness, dict):
                print(json.dumps(readiness, indent=2, sort_keys=True))
                return 0 if readiness["minimum_tier_met"] else 1
            return 0
        if args.command == "validate":
            validation_result = validate_inputs(
                workflow_config_path=args.workflow_config,
                raw_logs_path=args.raw_logs,
                evaluation_manifest_path=args.evaluation_manifest,
                baseline_context_path=args.baseline_context,
                mapping_assessment_path=args.mapping_assessment,
                minimum_tier_override=args.minimum_tier,
            )
            if args.output_json:
                write_json(args.output_json, validation_result)
            print(json.dumps(validation_result, indent=2, sort_keys=True))
            return 0 if validation_result["passed"] else 1
        if args.command == "init-evaluation":
            init_evaluation(args.output_dir)
            return 0
        if args.command == "prepare-langfuse":
            result = prepare_langfuse(
                input_path=args.input,
                mapping_path=args.mapping,
                output_dir=args.output_dir,
                activity_log_path=args.activity_log,
                overwrite=args.overwrite,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "prepare-opentelemetry":  # pragma: no cover -- optional adapter glue
            result = prepare_opentelemetry(  # pragma: no cover -- optional adapter glue
                input_path=args.input,
                mapping_path=args.mapping,
                evaluation_manifest_path=args.evaluation_manifest,
                output_dir=args.output_dir,
                activity_log_path=args.activity_log,
                overwrite=args.overwrite,
            )
            print(json.dumps(result, indent=2, sort_keys=True))  # pragma: no cover
            return 0  # pragma: no cover
        if args.command == "assess-mapping":
            result = assess_mapping(
                source_profile_path=args.source_profile,
                mapping_spec_path=args.mapping_spec,
                evaluation_manifest_path=args.evaluation_manifest,
                activity_log_path=args.activity_log,
                output_dir=args.output_dir,
                overwrite=args.overwrite,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "verify-package":
            verification = verify_evaluation_package(
                args.package or args.package_dir,
                workflow_config_path=args.workflow_config,
                raw_logs_path=args.raw_logs,
                evaluation_manifest_path=args.evaluation_manifest,
                baseline_context_path=args.baseline_context,
                mapping_assessment_path=args.mapping_assessment,
                schema_dir=args.schemas_dir,
            )
            if args.output_json:
                write_json(args.output_json, verification)
            print(json.dumps(verification, indent=2, sort_keys=True))
            return 0 if verification["passed"] else 1
        if args.command == "list-templates":
            print("\n".join(list_templates()))
            return 0
    except ToolkitError as exc:
        diagnostics = getattr(exc, "diagnostics", None)
        stage = getattr(exc, "diagnostic_stage", args.command.replace("-", "_"))
        if diagnostics is None:
            diagnostics = [diagnostic_from_exception(exc, stage)]
        LOGGER.error(
            "%s",
            json.dumps(diagnostic_document(diagnostics, stage=stage), sort_keys=True),
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
