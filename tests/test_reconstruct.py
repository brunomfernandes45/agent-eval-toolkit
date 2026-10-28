from __future__ import annotations

import unittest

from agent_eval_toolkit.exceptions import DataValidationError
from agent_eval_toolkit.models import NormalizedEvent
from agent_eval_toolkit.normalize import normalize_logs
from agent_eval_toolkit.reconstruct import reconstruct_runs
from tests.helpers import ROOT, sample_raw_rows, sample_workflow_config


class ReconstructTest(unittest.TestCase):
    def test_reconstruct_runs_groups_and_aggregates_workflow_runs(self) -> None:
        events = normalize_logs(
            sample_raw_rows(),
            _sample_workflow_config(),
            str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
        )

        runs = reconstruct_runs(events)
        runs_by_id = {run.run_id: run for run in runs}

        self.assertEqual(len(runs), 3)
        self.assertEqual(runs_by_id["run-001"].status, "completed")
        self.assertEqual(runs_by_id["run-002"].retry_count, 1)
        self.assertEqual(runs_by_id["run-002"].intervention_count, 1)
        self.assertEqual(runs_by_id["run-002"].handoff_count, 1)
        self.assertAlmostEqual(runs_by_id["run-001"].observed_cost_usd, 0.052)
        self.assertEqual(runs_by_id["run-001"].tool_call_count, 2)
        self.assertEqual(runs_by_id["run-001"].avg_quality_score, 0.94)
        self.assertEqual(
            runs_by_id["fallback-run-003"].failure_modes,
            ["missing_required_customer_record"],
        )
        self.assertEqual(
            runs_by_id["run-001"].agent_configuration_ids,
            ["support-agent.v1"],
        )

    def test_terminal_event_semantics_override_raw_status(self) -> None:
        events = [
            _event("start", "run_started", "2026-03-20T09:00:00Z", status="completed"),
            _event("end", "run_failed", "2026-03-20T09:01:00Z", status="in_progress"),
        ]

        run = reconstruct_runs(events)[0]

        self.assertEqual(run.status, "failed")
        self.assertEqual(run.started_at, "2026-03-20T09:00:00Z")
        self.assertEqual(run.ended_at, "2026-03-20T09:01:00Z")

    def test_terminal_event_rejects_conflicting_raw_status(self) -> None:
        events = [
            _event("start", "run_started", "2026-03-20T09:00:00Z"),
            _event("end", "run_completed", "2026-03-20T09:01:00Z", status="failed"),
        ]

        with self.assertRaisesRegex(DataValidationError, "terminal event/status conflict"):
            reconstruct_runs(events)

    def test_capabilities_make_unsupported_values_null(self) -> None:
        events = [
            _event("start", "run_started", "2026-03-20T09:00:00Z"),
            _event(
                "usage",
                "step_completed",
                "2026-03-20T09:00:30Z",
                input_tokens=10,
                output_tokens=5,
                observed_cost_usd=0.2,
                tool_call_id="call-1",
            ),
            _event("end", "run_completed", "2026-03-20T09:01:00Z"),
        ]

        run = reconstruct_runs(events, {"telemetry_capabilities": []})[0]

        self.assertIsNone(run.input_tokens)
        self.assertIsNone(run.output_tokens)
        self.assertIsNone(run.observed_cost_usd)
        self.assertIsNone(run.retry_count)
        self.assertIsNone(run.tool_call_count)
        self.assertIn("input_tokens", run.unsupported_telemetry)
        self.assertIn("tool_call_trace", run.unsupported_telemetry)

    def test_tool_calls_and_retries_are_deduplicated(self) -> None:
        events = [
            _event("start", "run_started", "2026-03-20T09:00:00Z"),
            _event(
                "call-1a",
                "tool_call",
                "2026-03-20T09:00:10Z",
                tool_call_id="same-call",
            ),
            _event(
                "call-1b",
                "tool_call",
                "2026-03-20T09:00:11Z",
                tool_call_id="same-call",
            ),
            _event(
                "retry-1a",
                "retry",
                "2026-03-20T09:00:20Z",
                step_name="triage",
                attempt_index=2,
            ),
            _event(
                "retry-1b",
                "retry",
                "2026-03-20T09:00:21Z",
                step_name="triage",
                attempt_index=2,
            ),
            _event("end", "run_completed", "2026-03-20T09:01:00Z"),
        ]

        run = reconstruct_runs(events)[0]

        self.assertEqual(run.tool_call_count, 1)
        self.assertEqual(run.retry_count, 1)

    def test_reconstruct_rejects_duplicate_event_ids(self) -> None:
        events = [
            _event("duplicate", "run_started", "2026-03-20T09:00:00Z"),
            _event("duplicate", "run_completed", "2026-03-20T09:01:00Z"),
        ]

        with self.assertRaisesRegex(DataValidationError, "duplicate event IDs"):
            reconstruct_runs(events)

    def test_reconstruct_rejects_missing_and_conflicting_boundaries(self) -> None:
        with self.assertRaisesRegex(DataValidationError, "missing.*run_started"):
            reconstruct_runs([_event("end", "run_completed", "2026-03-20T09:01:00Z")])

        with self.assertRaisesRegex(DataValidationError, "missing a terminal"):
            reconstruct_runs([_event("start", "run_started", "2026-03-20T09:00:00Z")])

        with self.assertRaisesRegex(DataValidationError, "conflicting"):
            reconstruct_runs(
                [
                    _event("start", "run_started", "2026-03-20T09:00:00Z"),
                    _event("complete", "run_completed", "2026-03-20T09:01:00Z"),
                    _event("fail", "run_failed", "2026-03-20T09:01:01Z"),
                ]
            )

    def test_reconstruct_rejects_duplicate_and_out_of_order_boundaries(self) -> None:
        with self.assertRaisesRegex(DataValidationError, "duplicate.*run_started"):
            reconstruct_runs(
                [
                    _event("start-1", "run_started", "2026-03-20T09:00:00Z"),
                    _event("start-2", "run_started", "2026-03-20T09:00:01Z"),
                    _event("end", "run_completed", "2026-03-20T09:01:00Z"),
                ]
            )

        with self.assertRaisesRegex(DataValidationError, "out-of-order"):
            reconstruct_runs(
                [
                    _event("start", "run_started", "2026-03-20T09:01:00Z"),
                    _event("end", "run_completed", "2026-03-20T09:00:00Z"),
                ]
            )

    def test_reconstruct_rejects_post_terminal_events(self) -> None:
        events = [
            _event("start", "run_started", "2026-03-20T09:00:00Z"),
            _event("end", "run_completed", "2026-03-20T09:01:00Z"),
            _event("late", "step_completed", "2026-03-20T09:02:00Z"),
        ]

        with self.assertRaisesRegex(DataValidationError, "after the terminal"):
            reconstruct_runs(events)

    def test_equal_timestamp_terminal_must_be_last_in_source_order(self) -> None:
        events = [
            _event("start", "run_started", "2026-03-20T09:00:00Z"),
            _event("end", "run_completed", "2026-03-20T09:01:00Z"),
            _event("same-time-late", "step_completed", "2026-03-20T09:01:00Z"),
        ]

        with self.assertRaisesRegex(DataValidationError, "terminal boundary must be last"):
            reconstruct_runs(events)

    def test_equal_timestamp_start_must_be_first_in_source_order(self) -> None:
        events = [
            _event("same-time-early", "step_started", "2026-03-20T09:00:00Z"),
            _event("start", "run_started", "2026-03-20T09:00:00Z"),
            _event("end", "run_completed", "2026-03-20T09:01:00Z"),
        ]

        with self.assertRaisesRegex(DataValidationError, "start boundary must be first"):
            reconstruct_runs(events)

    def test_incremental_money_tokens_and_effort_are_aggregated_explicitly(self) -> None:
        events = [
            _event("start", "run_started", "2026-03-20T09:00:00Z"),
            _event(
                "usage-1",
                "step_completed",
                "2026-03-20T09:00:10Z",
                observed_cost_usd=0.1,
                input_tokens=10,
                output_tokens=2,
                human_effort_seconds=12.25,
            ),
            _event(
                "end",
                "run_completed",
                "2026-03-20T09:01:00Z",
                observed_cost_usd=0.2,
                input_tokens=20,
                output_tokens=3,
                human_effort_seconds=7.75,
                business_success=True,
            ),
        ]
        config = {
            "telemetry_capabilities": [
                "observed_cost_usd",
                "input_tokens",
                "output_tokens",
                "human_effort_seconds",
                "business_success",
            ],
            "cost_field": "cost",
            "input_tokens_field": "input",
            "output_tokens_field": "output",
            "aggregation_semantics": {
                "observed_cost_usd": "per_event_increment",
                "input_tokens": "per_event_increment",
                "output_tokens": "per_event_increment",
            },
        }

        run = reconstruct_runs(events, config)[0]

        self.assertEqual(run.observed_cost_usd, 0.3)
        self.assertEqual(run.input_tokens, 30)
        self.assertEqual(run.output_tokens, 5)
        self.assertEqual(run.human_effort_seconds, 20.0)
        self.assertTrue(run.business_success)
        self.assertEqual(
            run.aggregation_semantics,
            {
                "observed_cost_usd": "per_event_increment",
                "input_tokens": "per_event_increment",
                "output_tokens": "per_event_increment",
            },
        )

    def test_reconstruct_rejects_cumulative_aggregation_semantics(self) -> None:
        events = [
            _event("start", "run_started", "2026-03-20T09:00:00Z"),
            _event("end", "run_completed", "2026-03-20T09:01:00Z", input_tokens=10),
        ]
        config = {
            "telemetry_capabilities": ["input_tokens"],
            "input_tokens_field": "input",
            "aggregation_semantics": {"input_tokens": "cumulative_snapshot"},
        }

        with self.assertRaisesRegex(DataValidationError, "per_event_increment"):
            reconstruct_runs(events, config)


def _event(event_id: str, event_type: str, timestamp: str, **overrides: object) -> NormalizedEvent:
    values: dict[str, object] = {
        "event_id": event_id,
        "workflow_family": "family",
        "workflow_definition_id": "workflow.v1",
        "timestamp": timestamp,
        "run_id": "run-1",
        "trace_id": "trace-1",
        "event_type": event_type,
        "status": "in_progress",
        "step_name": None,
        "actor_type": "agent",
        "attempt_index": None,
        "human_intervention": False,
        "observed_cost_usd": None,
        "input_tokens": None,
        "output_tokens": None,
        "agent_configuration_id": "config-1",
        "agent_version": "v1",
        "configuration_hash": "sha256:1",
    }
    values.update(overrides)
    return NormalizedEvent(**values)  # type: ignore[arg-type]


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
    config.setdefault(
        "telemetry_capabilities",
        [
            "failure_modes",
            "handoff_targets",
            "input_tokens",
            "intervention_markers",
            "observed_cost_usd",
            "output_tokens",
            "quality_score",
            "retry_markers",
            "routing_decisions",
            "tool_call_trace",
        ],
    )
    return config


if __name__ == "__main__":
    unittest.main()
