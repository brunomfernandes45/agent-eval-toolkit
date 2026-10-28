from __future__ import annotations

import csv
import io
import json
from typing import Any

from .models import PilotScorecard


def render_monitoring_summary_csv(scorecard: PilotScorecard) -> str:
    """Render a compact, report-ready summary without changing claim strength."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["category", "metric", "value"])
    for key in (
        "evaluation_id",
        "evaluation_design",
        "design_claim_ceiling",
        "design_evidence_status",
        "attained_claim_strength",
        "withheld_reasons",
    ):
        writer.writerow(["evaluation", key, _csv_value(scorecard.evaluation.get(key))])
    writer.writerow(["evaluation", "workflow_family", scorecard.workflow_family])
    writer.writerow(["evaluation", "workflow_definition_id", scorecard.workflow_definition_id])
    for key, value in sorted(scorecard.evaluation_readiness.items()):
        if key != "metric_statuses":
            writer.writerow(["evidence_readiness", key, _csv_value(value)])
    for metric, details in sorted(scorecard.metric_statuses.items()):
        writer.writerow(["metric_status", metric, _csv_value(details)])
    for metric, value in sorted(scorecard.observed_summary.items()):
        writer.writerow(["observed", metric, _csv_value(value)])
    for metric, value in sorted(scorecard.assumption_based_summary["values"].items()):
        writer.writerow(["assumption_based", metric, _csv_value(value)])
    return output.getvalue()


def render_telemetry_coverage_csv(telemetry_validation: dict[str, Any]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "signal",
            "requirement",
            "supported",
            "runs_with_signal",
            "run_denominator",
            "coverage",
        ]
    )
    required = set(telemetry_validation.get("required_signals", []))
    coverage = telemetry_validation.get("coverage_by_signal", {})
    for signal, details in sorted(coverage.items()):
        writer.writerow(
            [
                signal,
                "required" if signal in required else "optional",
                str(bool(details["supported"])).lower(),
                details["runs_with_signal"],
                details["run_denominator"],
                _csv_value(details["coverage"]),
            ]
        )
    return output.getvalue()


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)
