from __future__ import annotations

import unittest

from agent_eval_toolkit.models import EvidenceRef, NormalizedEvent, parse_timestamp, to_dict


class ModelsTest(unittest.TestCase):
    def test_parse_timestamp_normalizes_to_utc(self) -> None:
        parsed = parse_timestamp("2026-03-20T09:00:00Z")
        self.assertEqual(parsed.isoformat(), "2026-03-20T09:00:00+00:00")

    def test_parse_timestamp_rejects_naive_timestamp(self) -> None:
        with self.assertRaises(ValueError):
            parse_timestamp("2026-03-20T09:00:00")

    def test_to_dict_serializes_nested_dataclasses(self) -> None:
        event = NormalizedEvent(
            event_id="evt-1",
            workflow_family="family",
            workflow_definition_id="def.v1",
            timestamp="2026-03-20T09:00:00Z",
            run_id="run-1",
            trace_id="trace-1",
            event_type="run_started",
            status="in_progress",
            step_name="intake",
            actor_type="system",
            attempt_index=1,
            human_intervention=False,
            observed_cost_usd=None,
            input_tokens=None,
            output_tokens=None,
            evidence=EvidenceRef(
                source_file="logs.jsonl",
                source_line=1,
                raw_event_id="raw-1",
                raw_event_hash="abc123",
                captured_fields={"event_name": "workflow_started"},
            ),
        )

        payload = to_dict(event)
        self.assertEqual(payload["event_id"], "evt-1")
        self.assertEqual(payload["evidence"]["source_line"], 1)
        self.assertEqual(payload["evidence"]["raw_event_hash"], "abc123")

    def test_normalized_event_rejects_coerced_identifier_and_boolean_types(self) -> None:
        values = {
            "event_id": 123,
            "workflow_family": "family",
            "workflow_definition_id": "def.v1",
            "timestamp": "2026-03-20T09:00:00Z",
            "run_id": "run-1",
            "trace_id": "trace-1",
            "event_type": "run_started",
            "status": "in_progress",
            "step_name": "intake",
            "actor_type": "system",
            "attempt_index": 1,
            "human_intervention": False,
            "observed_cost_usd": None,
            "input_tokens": None,
            "output_tokens": None,
        }
        with self.assertRaisesRegex(TypeError, "event_id"):
            NormalizedEvent(**values)  # type: ignore[arg-type]

        values["event_id"] = "evt-1"
        values["business_success"] = 1
        with self.assertRaisesRegex(TypeError, "business_success"):
            NormalizedEvent(**values)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
