from __future__ import annotations

import unittest

from agent_eval_toolkit.metrics import _safe_mean, build_scorecard
from agent_eval_toolkit.normalize import normalize_logs
from agent_eval_toolkit.reconstruct import reconstruct_runs
from agent_eval_toolkit.validation import validate_telemetry_requirements
from tests.helpers import (
    ROOT,
    sample_baseline_context,
    sample_evaluation_manifest,
    sample_raw_rows,
    sample_workflow_config,
)


class MetricsTest(unittest.TestCase):
    def test_safe_mean_handles_empty_lists(self) -> None:
        self.assertIsNone(_safe_mean([]))
        self.assertEqual(_safe_mean([1.0, 2.0]), 1.5)

    def test_build_scorecard_separates_observed_and_assumption_metrics(self) -> None:
        events = normalize_logs(
            sample_raw_rows(),
            sample_workflow_config(),
            str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
        )
        runs = reconstruct_runs(events, sample_workflow_config())
        manifest = sample_evaluation_manifest()
        workflow_config = sample_workflow_config()
        telemetry_validation = validate_telemetry_requirements(runs, manifest, workflow_config)
        scorecard = build_scorecard(runs, manifest, sample_baseline_context(), telemetry_validation)

        self.assertEqual(scorecard.run_count, 3)
        self.assertEqual(scorecard.observed_summary["completion_rate"], 0.667)
        self.assertTrue(scorecard.telemetry_validation["passed"])
        self.assertEqual(scorecard.missing_telemetry_summary, {"quality_score": 1})
        self.assertEqual(scorecard.observed_summary["avg_tool_call_count"], 1.667)
        self.assertEqual(scorecard.observed_summary["runs_with_quality_score"], 2)
        self.assertIn(
            "avg_estimated_cycle_time_difference",
            scorecard.assumption_based_summary["values"],
        )
        self.assertIn(
            "avg_estimated_net_value_delta_usd", scorecard.assumption_based_summary["values"]
        )
        self.assertIn(
            "total_estimated_net_value_delta_usd",
            scorecard.assumption_based_summary["sensitivity"],
        )
        self.assertIn("support-agent.v1", scorecard.comparator_summary["by_agent_configuration"])
        run_two = next(run for run in scorecard.runs if run.run_id == "run-002")
        failed_run = next(run for run in scorecard.runs if run.run_id == "fallback-run-003")
        self.assertEqual(run_two.observed_metrics["retry_count"], 1)
        self.assertEqual(run_two.observed_metrics["handoff_count"], 1)
        self.assertIn(
            "manual_baseline_cycle_time_seconds",
            run_two.assumption_based_estimates["assumptions_used"],
        )
        self.assertFalse(failed_run.assumption_based_estimates["eligible"])
        self.assertEqual(
            failed_run.assumption_based_estimates["ineligibility_reason"],
            "run_not_completed",
        )
        self.assertEqual(
            failed_run.assumption_based_estimates["values"]["estimated_labor_value_delta_usd"],
            0.0,
        )
        self.assertEqual(
            failed_run.assumption_based_estimates["values"]["estimated_net_value_delta_usd"],
            -0.181,
        )
        self.assertEqual(scorecard.assumption_based_summary["eligible_run_count"], 2)
        self.assertEqual(
            scorecard.assumption_based_summary["values"]["total_estimated_cycle_time_difference"],
            651.0,
        )
        self.assertEqual(
            scorecard.assumption_based_summary["values"][
                "total_estimated_human_effort_saved_seconds_vs_manual_baseline"
            ],
            510.0,
        )
        self.assertEqual(
            scorecard.assumption_based_summary["values"]["total_estimated_net_value_delta_usd"],
            8.997,
        )

    def test_build_scorecard_reports_comparator_deltas_and_percentiles(self) -> None:
        workflow_config = sample_workflow_config()
        raw_rows = [
            (
                1,
                {
                    "event_id": "cmp-001",
                    "timestamp": "2026-04-02T09:00:00Z",
                    "event_name": "workflow_started",
                    "workflow_run_id": "cmp-run-001",
                    "trace_id": "cmp-trace-001",
                    "step_name": "intake",
                    "status": "in_progress",
                    "agent_config_id": "control-agent.v1",
                    "agent_version": "2026-04-01",
                    "config_hash": "sha256:control-agent-v1",
                    "input_tokens": 100,
                    "output_tokens": 20,
                },
            ),
            (
                2,
                {
                    "event_id": "cmp-002",
                    "timestamp": "2026-04-02T09:05:00Z",
                    "event_name": "workflow_completed",
                    "workflow_run_id": "cmp-run-001",
                    "trace_id": "cmp-trace-001",
                    "step_name": "resolution",
                    "status": "completed",
                    "agent_config_id": "control-agent.v1",
                    "agent_version": "2026-04-01",
                    "config_hash": "sha256:control-agent-v1",
                    "input_tokens": 800,
                    "output_tokens": 160,
                    "quality_score": 0.84,
                },
            ),
            (
                3,
                {
                    "event_id": "cmp-003",
                    "timestamp": "2026-04-02T09:10:00Z",
                    "event_name": "workflow_started",
                    "workflow_run_id": "cmp-run-002",
                    "trace_id": "cmp-trace-002",
                    "step_name": "intake",
                    "status": "in_progress",
                    "agent_config_id": "treatment-agent.v1",
                    "agent_version": "2026-04-01",
                    "config_hash": "sha256:treatment-agent-v1",
                    "input_tokens": 100,
                    "output_tokens": 20,
                },
            ),
            (
                4,
                {
                    "event_id": "cmp-004",
                    "timestamp": "2026-04-02T09:13:00Z",
                    "event_name": "workflow_completed",
                    "workflow_run_id": "cmp-run-002",
                    "trace_id": "cmp-trace-002",
                    "step_name": "resolution",
                    "status": "completed",
                    "agent_config_id": "treatment-agent.v1",
                    "agent_version": "2026-04-01",
                    "config_hash": "sha256:treatment-agent-v1",
                    "input_tokens": 700,
                    "output_tokens": 140,
                    "quality_score": 0.9,
                },
            ),
        ]
        manifest = sample_evaluation_manifest()
        manifest["measurement_window"] = {
            "start": "2026-04-02T00:00:00Z",
            "end": "2026-04-03T00:00:00Z",
            "timezone": "UTC",
        }
        manifest["baseline"]["agent_configuration_id"] = "control-agent.v1"
        manifest["comparator"]["agent_configuration_id"] = "treatment-agent.v1"
        manifest["evaluation_design"] = "scenario_suite"
        manifest["expected_configurations"] = [
            {
                "group": "control",
                "agent_configuration_id": "control-agent.v1",
                "agent_version": "2026-04-01",
                "configuration_hash": "sha256:control-agent-v1",
                "required": True,
            },
            {
                "group": "treatment",
                "agent_configuration_id": "treatment-agent.v1",
                "agent_version": "2026-04-01",
                "configuration_hash": "sha256:treatment-agent-v1",
                "required": True,
            },
        ]
        manifest["telemetry_requirements"]["required"] = [
            signal
            for signal in manifest["telemetry_requirements"]["required"]
            if signal not in {"business_success", "human_effort_seconds"}
        ]
        events = normalize_logs(raw_rows, workflow_config, "comparator_telemetry.jsonl")
        runs = reconstruct_runs(events, workflow_config)
        telemetry_validation = validate_telemetry_requirements(runs, manifest, workflow_config)

        scorecard = build_scorecard(runs, manifest, telemetry_validation=telemetry_validation)

        self.assertEqual(scorecard.observed_summary["p50_duration_seconds"], 240.0)
        self.assertEqual(scorecard.observed_summary["p90_duration_seconds"], 288.0)
        self.assertEqual(
            scorecard.comparator_summary["deltas"]["values"]["avg_duration_seconds_delta"],
            -120.0,
        )
        self.assertEqual(
            scorecard.comparator_summary["design_claim_ceiling"],
            "conformance",
        )
        self.assertEqual(
            scorecard.comparator_summary["attained_claim_strength"],
            "conformance_readiness",
        )

        runs[1].output_tokens = None
        incomplete_scorecard = build_scorecard(
            runs, manifest, telemetry_validation=telemetry_validation
        )
        incomplete_deltas = incomplete_scorecard.comparator_summary["deltas"]
        self.assertNotIn("avg_output_tokens_delta", incomplete_deltas["values"])
        self.assertEqual(
            incomplete_deltas["withheld_metrics"]["avg_output_tokens"],
            "incomplete_coverage",
        )
        treatment = incomplete_scorecard.comparator_summary["by_agent_configuration"][
            "treatment-agent.v1"
        ]
        self.assertIsNone(treatment["total_output_tokens"])
        self.assertIsNone(treatment["partial_output_tokens"])

    def test_net_value_is_withheld_when_eligible_cost_is_missing(self) -> None:
        workflow_config = sample_workflow_config()
        events = normalize_logs(sample_raw_rows(), workflow_config, "pilot_logs.jsonl")
        runs = reconstruct_runs(events, workflow_config)
        completed = next(run for run in runs if run.run_id == "run-001")
        completed.observed_cost_usd = None

        scorecard = build_scorecard(runs, sample_evaluation_manifest(), sample_baseline_context())

        self.assertNotIn(
            "avg_estimated_net_value_delta_usd",
            scorecard.assumption_based_summary["values"],
        )
        self.assertIsNone(scorecard.observed_summary["total_observed_cost_usd"])
        self.assertEqual(scorecard.observed_summary["partial_observed_cost_usd"], 0.074)
        self.assertEqual(
            scorecard.observed_summary["aggregate_coverage"]["observed_cost_usd"],
            {
                "observed_run_count": 2,
                "run_denominator": 3,
                "coverage": 0.667,
                "complete": False,
                "partial_sum": 0.074,
            },
        )

    def test_completed_run_without_success_evidence_receives_zero_benefit(self) -> None:
        workflow_config = sample_workflow_config()
        events = normalize_logs(sample_raw_rows(), workflow_config, "pilot_logs.jsonl")
        runs = reconstruct_runs(events, workflow_config)
        run = next(item for item in runs if item.run_id == "run-001")
        run.business_success = None

        scorecard = build_scorecard(runs, sample_evaluation_manifest(), sample_baseline_context())
        estimate = next(item for item in scorecard.runs if item.run_id == "run-001")

        self.assertFalse(estimate.assumption_based_estimates["benefit_eligible"])
        self.assertEqual(
            estimate.assumption_based_estimates["ineligibility_reason"],
            "business_success_or_approved_quality_evidence_unavailable",
        )
        self.assertEqual(
            estimate.assumption_based_estimates["values"]["estimated_labor_value_delta_usd"],
            0.0,
        )

    def test_failed_run_with_success_marker_still_receives_zero_benefit(self) -> None:
        workflow_config = sample_workflow_config()
        events = normalize_logs(sample_raw_rows(), workflow_config, "pilot_logs.jsonl")
        runs = reconstruct_runs(events, workflow_config)
        failed = next(run for run in runs if run.status == "failed")
        failed.business_success = True

        scorecard = build_scorecard(
            runs,
            sample_evaluation_manifest(),
            sample_baseline_context(),
        )
        estimate = next(item for item in scorecard.runs if item.run_id == failed.run_id)

        self.assertFalse(estimate.assumption_based_estimates["benefit_eligible"])
        self.assertEqual(
            estimate.assumption_based_estimates["values"]["estimated_labor_value_delta_usd"],
            0.0,
        )
        self.assertLess(
            estimate.assumption_based_estimates["values"]["estimated_net_value_delta_usd"],
            0.0,
        )

    def test_population_totals_are_withheld_under_partial_coverage(self) -> None:
        workflow_config = sample_workflow_config()
        events = normalize_logs(sample_raw_rows(), workflow_config, "pilot_logs.jsonl")
        runs = reconstruct_runs(events, workflow_config)
        runs[0].input_tokens = None
        runs[0].observed_cost_usd = None

        scorecard = build_scorecard(
            runs,
            sample_evaluation_manifest(),
            sample_baseline_context(),
        )

        self.assertIsNone(scorecard.observed_summary["total_input_tokens"])
        self.assertGreater(scorecard.observed_summary["partial_input_tokens"], 0)
        self.assertIsNone(scorecard.observed_summary["total_observed_cost_usd"])
        self.assertGreater(scorecard.observed_summary["partial_observed_cost_usd"], 0)
        self.assertEqual(
            scorecard.observed_summary["aggregate_coverage"]["input_tokens"]["observed_run_count"],
            2,
        )

    def test_approved_declared_quality_gate_can_supply_benefit_evidence(self) -> None:
        workflow_config = sample_workflow_config()
        events = normalize_logs(sample_raw_rows(), workflow_config, "pilot_logs.jsonl")
        runs = reconstruct_runs(events, workflow_config)
        run = next(item for item in runs if item.run_id == "run-001")
        run.business_success = None
        manifest = sample_evaluation_manifest()
        manifest["quality_gate"] = {"approved": True, "minimum_score": 0.9}

        scorecard = build_scorecard(runs, manifest, sample_baseline_context())
        estimate = next(item for item in scorecard.runs if item.run_id == "run-001")

        self.assertTrue(estimate.assumption_based_estimates["benefit_eligible"])
        self.assertEqual(
            estimate.assumption_based_estimates["benefit_evidence"]["basis"],
            "approved_quality_gate",
        )

    def test_deprecated_cycle_duration_is_never_used_as_human_effort(self) -> None:
        workflow_config = sample_workflow_config()
        events = normalize_logs(sample_raw_rows(), workflow_config, "pilot_logs.jsonl")
        runs = reconstruct_runs(events, workflow_config)
        manifest = sample_evaluation_manifest()
        manifest["assumptions"] = {
            "manual_baseline_duration_seconds": 420,
            "loaded_labor_rate_usd_per_hour": 68,
        }

        scorecard = build_scorecard(runs, manifest)
        estimate = next(item for item in scorecard.runs if item.run_id == "run-001")

        self.assertIn(
            "estimated_cycle_time_difference",
            estimate.assumption_based_estimates["values"],
        )
        self.assertNotIn(
            "estimated_labor_value_delta_usd",
            estimate.assumption_based_estimates["values"],
        )
        self.assertIn(
            "labor_value_withheld_manual_human_effort_unavailable",
            estimate.assumption_based_estimates["withheld_reasons"],
        )


if __name__ == "__main__":
    unittest.main()
