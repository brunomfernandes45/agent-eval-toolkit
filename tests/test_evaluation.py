from __future__ import annotations

import unittest

from agent_eval_toolkit.evaluation import (
    build_measurement_window_summary,
    filter_runs_by_measurement_window,
)
from agent_eval_toolkit.normalize import normalize_logs
from agent_eval_toolkit.reconstruct import reconstruct_runs
from tests.helpers import ROOT, sample_evaluation_manifest, sample_raw_rows, sample_workflow_config


class EvaluationTest(unittest.TestCase):
    def test_filter_runs_by_measurement_window_excludes_outside_runs(self) -> None:
        events = normalize_logs(
            sample_raw_rows(),
            _sample_workflow_config(),
            str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
        )
        runs = reconstruct_runs(events)
        manifest = sample_evaluation_manifest()
        manifest["measurement_window"] = {
            "start": "2026-03-20T09:00:00Z",
            "end": "2026-03-20T09:30:00Z",
            "timezone": "UTC",
        }

        included, excluded = filter_runs_by_measurement_window(runs, manifest)
        summary = build_measurement_window_summary(included, excluded)

        self.assertEqual([run.run_id for run in included], ["run-001"])
        self.assertEqual(summary["included_run_count"], 1)
        self.assertEqual(summary["excluded_run_count"], 2)

    def test_structured_exclusions_record_reasons_anchors_and_unmatched_ids(self) -> None:
        workflow_config = _sample_workflow_config()
        events = normalize_logs(
            sample_raw_rows(),
            workflow_config,
            str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
        )
        runs = reconstruct_runs(events)
        manifest = sample_evaluation_manifest()
        manifest["run_exclusions"] = [
            {"run_id": "run-002", "reason": "Known test traffic"},
            {"run_id": "does-not-exist", "reason": "Requested exclusion audit"},
        ]

        included, excluded = filter_runs_by_measurement_window(runs, manifest)
        summary = build_measurement_window_summary(included, excluded, manifest)

        self.assertNotIn("run-002", [run.run_id for run in included])
        run_two = next(item for item in excluded if item["run_id"] == "run-002")
        self.assertEqual(run_two["reason"], "Known test traffic")
        self.assertEqual(run_two["exclusion_type"], "manifest_run_exclusion")
        self.assertTrue(run_two["evidence_anchors"])
        self.assertEqual(summary["unmatched_run_exclusion_ids"], ["does-not-exist"])


def _sample_workflow_config() -> dict:
    config = sample_workflow_config()
    config.setdefault(
        "run_boundaries",
        {
            "start_event_type": "run_started",
            "completed_event_type": "run_completed",
            "failed_event_type": "run_failed",
        },
    )
    config.setdefault("telemetry_capabilities", [])
    return config


if __name__ == "__main__":
    unittest.main()
