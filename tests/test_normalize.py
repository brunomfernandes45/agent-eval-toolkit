from __future__ import annotations

import unittest

from agent_eval_toolkit.normalize import _first_present, normalize_logs
from tests.helpers import ROOT, sample_raw_rows, sample_workflow_config


class NormalizeTest(unittest.TestCase):
    def test_first_present_returns_first_non_empty_value(self) -> None:
        payload = {"a": "", "b": None, "c": "value"}
        self.assertEqual(_first_present(payload, ["a", "b", "c"]), "value")

    def test_normalize_logs_maps_fields_and_evidence(self) -> None:
        events = normalize_logs(
            sample_raw_rows(),
            sample_workflow_config(),
            str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
        )

        first = events[0]
        intervention = next(event for event in events if event.event_id == "evt-010")
        redacted = next(event for event in events if event.event_id == "evt-001")
        tool_event = next(event for event in events if event.event_id == "evt-002")

        self.assertEqual(len(events), 14)
        self.assertEqual(first.run_id, "fallback-run-003")
        self.assertEqual(first.timestamp, "2026-03-20T11:40:00Z")
        self.assertEqual(intervention.event_type, "intervention")
        self.assertTrue(intervention.human_intervention)
        self.assertEqual(intervention.handoff_target, "human_approver")
        self.assertEqual(tool_event.tool_name, "ticket_lookup")
        self.assertEqual(tool_event.routing_decision, "route-to-triage")
        self.assertEqual(redacted.metadata["ticket_priority"], "high")
        self.assertNotIn("ticket_text", redacted.metadata)
        self.assertEqual(redacted.evidence.captured_fields["ticket_text"], "[REDACTED]")
        self.assertEqual(len(redacted.evidence.raw_line_hash or ""), 64)
        self.assertIn("ticket_text", redacted.evidence.redacted_fields)

    def test_anchor_hash_only_retains_no_evidence_snapshot(self) -> None:
        config = sample_workflow_config()
        config["evidence_mode"] = "anchor_hash_only"

        events = normalize_logs(sample_raw_rows(), config, "pilot_logs.jsonl")

        self.assertTrue(events)
        self.assertTrue(all(event.evidence is not None for event in events))
        self.assertTrue(
            all(event.evidence.captured_fields == {} for event in events if event.evidence)
        )
        self.assertTrue(
            all(event.evidence.redacted_fields == [] for event in events if event.evidence)
        )
        self.assertTrue(all(event.metadata == {} for event in events))


if __name__ == "__main__":
    unittest.main()
