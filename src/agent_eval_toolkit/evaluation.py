from __future__ import annotations

from typing import Any

from .models import WorkflowRun, parse_timestamp


def filter_runs_by_measurement_window(
    runs: list[WorkflowRun], evaluation_manifest: dict[str, Any]
) -> tuple[list[WorkflowRun], list[dict[str, Any]]]:
    measurement_window = evaluation_manifest["measurement_window"]
    window_start = parse_timestamp(measurement_window["start"])
    window_end = parse_timestamp(measurement_window["end"])
    run_exclusions = {
        str(exclusion["run_id"]): str(exclusion["reason"])
        for exclusion in evaluation_manifest.get("run_exclusions", [])
    }

    included: list[WorkflowRun] = []
    excluded: list[dict[str, Any]] = []
    for run in runs:
        if run.run_id in run_exclusions:
            excluded.append(
                _excluded_run(
                    run,
                    run_exclusions[run.run_id],
                    exclusion_type="manifest_run_exclusion",
                )
            )
            continue
        if run.started_at is None or run.ended_at is None:
            excluded.append(_excluded_run(run, "missing_measurement_timestamp"))
            continue
        started_at = parse_timestamp(run.started_at)
        ended_at = parse_timestamp(run.ended_at)
        if started_at < window_start or ended_at > window_end:
            excluded.append(_excluded_run(run, "outside_measurement_window"))
            continue
        included.append(run)
    return included, excluded


def build_measurement_window_summary(
    included_runs: list[WorkflowRun],
    excluded_runs: list[dict[str, Any]],
    evaluation_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    total_runs = len(included_runs) + len(excluded_runs)
    exclusion_counts: dict[str, int] = {}
    for excluded in excluded_runs:
        reason = str(excluded["reason"])
        exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1

    configured_exclusions = (
        list(evaluation_manifest.get("run_exclusions", [])) if evaluation_manifest else []
    )
    reconstructed_run_ids = {run.run_id for run in included_runs} | {
        str(excluded["run_id"]) for excluded in excluded_runs
    }
    unmatched_exclusions = [
        exclusion
        for exclusion in configured_exclusions
        if str(exclusion["run_id"]) not in reconstructed_run_ids
    ]
    matched_exclusions = [
        excluded
        for excluded in excluded_runs
        if excluded.get("exclusion_type") == "manifest_run_exclusion"
    ]

    return {
        "included_run_count": len(included_runs),
        "excluded_run_count": len(excluded_runs),
        "total_reconstructed_run_count": total_runs,
        "run_coverage": round(len(included_runs) / total_runs, 3) if total_runs else None,
        "exclusion_reasons": dict(sorted(exclusion_counts.items())),
        "configured_run_exclusion_count": len(configured_exclusions),
        "matched_run_exclusions": matched_exclusions,
        "unmatched_run_exclusions": unmatched_exclusions,
        "unmatched_run_exclusion_ids": sorted(
            str(exclusion["run_id"]) for exclusion in unmatched_exclusions
        ),
    }


def _excluded_run(
    run: WorkflowRun, reason: str, exclusion_type: str = "measurement_window"
) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "reason": reason,
        "exclusion_type": exclusion_type,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "status": run.status,
        "event_ids": run.event_ids,
        "trace_ids": run.trace_ids,
        "evidence_anchors": [
            {
                "event_id": event.event_id,
                "source_file": event.evidence.source_file,
                "source_line": event.evidence.source_line,
                "raw_event_id": event.evidence.raw_event_id,
                "raw_event_hash": event.evidence.raw_event_hash,
                "raw_line_hash": event.evidence.raw_line_hash,
            }
            for event in run.events
            if event.evidence is not None
        ],
    }
