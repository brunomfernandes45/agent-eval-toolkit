from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_eval_toolkit.mapping import assess_mapping, build_mapping_assessment_payload
from agent_eval_toolkit.readiness import build_evaluation_readiness
from tests.helpers import sample_evaluation_manifest


def _manifest() -> dict:
    manifest = sample_evaluation_manifest()
    manifest["evaluation_id"] = "mapping-conformance-fixture"
    manifest["evidence_context"] = {
        "source_kind": "synthetic_fixture",
        "mapping_assessment_required": True,
    }
    manifest["publication_policy"] = {"minimum_evidence_tier": "mapping_only"}
    manifest["telemetry_requirements"] = {
        "required": ["run_id", "timestamp", "status"],
        "optional": ["quality_score"],
    }
    return manifest


def _profile() -> dict:
    return {
        "source_profile_schema_version": "1.0.0",
        "logical_source_id": "synthetic-source-001",
        "source_kind": "synthetic_fixture",
        "file_count": 2,
        "record_count": 12,
        "formats": ["json", "jsonl"],
        "field_inventory": [
            {
                "path": "trace.id",
                "observed_count": 12,
                "record_denominator": 12,
                "coverage": 1.0,
                "value_types": ["string"],
            },
            {
                "path": "event.time",
                "observed_count": 12,
                "record_denominator": 12,
                "coverage": 1.0,
                "value_types": ["string"],
            },
            {
                "path": "metadata.business_status",
                "observed_count": 12,
                "record_denominator": 12,
                "coverage": 1.0,
                "value_types": ["string"],
            },
            {
                "path": "scores.quality",
                "observed_count": 9,
                "record_denominator": 12,
                "coverage": 0.75,
                "value_types": ["number"],
            },
        ],
        "manual_review_record_count": 3,
    }


def _approval(status: str = "approved") -> dict:
    return {
        "status": status,
        "reviewer_role": "workflow-owner" if status == "approved" else None,
        "evidence_ref": "decision-record-001" if status == "approved" else None,
    }


def _mapping_spec() -> dict:
    mapping_rows = [
        ("run_id", ["trace.id"], "direct"),
        ("timestamp", ["event.time"], "coercion"),
        ("status", ["metadata.business_status"], "lookup"),
        ("quality_score", ["scores.quality"], "direct"),
    ]
    return {
        "mapping_spec_schema_version": "1.0.0",
        "mapping_id": "synthetic-mapping-001",
        "mapping_version": "v2",
        "logical_source_id": "synthetic-source-001",
        "mappings": [
            {
                "canonical_signal": signal,
                "source_paths": paths,
                "transformation": transformation,
                "semantic_status": "confirmed",
                "transformation_tested": True,
                "approval": _approval(),
            }
            for signal, paths, transformation in mapping_rows
        ],
        "workflow_boundary_approval": _approval(),
        "business_status_approval": _approval(),
        "privacy_approval": _approval(),
        "issues": [
            {
                "issue_id": "MAP-001",
                "severity": "warning",
                "status": "resolved",
                "canonical_signal": "status",
                "evidence_ref": "decision-record-001",
            }
        ],
    }


def _activities() -> list[dict]:
    return [
        {
            "activity_id": "activity-001",
            "timestamp": "2026-04-01T09:00:00Z",
            "actor_role": "data-engineer",
            "activity_type": "source_review",
            "active_effort_minutes": 10,
            "mapping_version": "v1",
            "issue_id": None,
            "outcome": "profiled",
        },
        {
            "activity_id": "activity-002",
            "timestamp": "2026-04-01T09:20:00Z",
            "actor_role": "workflow-owner",
            "activity_type": "semantic_decision",
            "active_effort_minutes": 20,
            "mapping_version": "v1",
            "issue_id": "MAP-001",
            "outcome": "status semantics confirmed",
        },
        {
            "activity_id": "activity-003",
            "timestamp": "2026-04-01T09:45:00Z",
            "actor_role": "data-engineer",
            "activity_type": "mapping_change",
            "active_effort_minutes": 5,
            "mapping_version": "v2",
            "issue_id": "MAP-001",
            "outcome": "lookup added",
        },
        {
            "activity_id": "activity-004",
            "timestamp": "2026-04-01T10:00:00Z",
            "actor_role": "workflow-owner",
            "activity_type": "approval",
            "active_effort_minutes": 10,
            "mapping_version": "v2",
            "issue_id": None,
            "outcome": "approved",
        },
    ]


def _telemetry_validation() -> dict:
    coverage = {
        signal: {
            "supported": True,
            "runs_with_signal": 2,
            "run_denominator": 2,
        }
        for signal in ("run_id", "status", "event_type", "timestamp")
    }
    return {
        "passed": True,
        "coverage_by_signal": coverage,
        "configuration_consistency": {"passed": True},
    }


class MappingAssessmentTest(unittest.TestCase):
    def test_mapping_row_and_activity_row_permutations_preserve_assessment(self) -> None:
        spec = _mapping_spec()
        assessment_a, friction_a, diagnostics_a = build_mapping_assessment_payload(
            _profile(), spec, _manifest(), activity_rows=_activities()
        )
        spec["mappings"] = list(reversed(spec["mappings"]))
        assessment_b, friction_b, diagnostics_b = build_mapping_assessment_payload(
            _profile(), spec, _manifest(), activity_rows=list(reversed(_activities()))
        )

        self.assertEqual(assessment_a, assessment_b)
        self.assertEqual(friction_a, friction_b)
        self.assertEqual(diagnostics_a, diagnostics_b)

    def test_split_activity_entries_preserve_observed_effort_total(self) -> None:
        activities = _activities()
        split = [dict(row) for row in activities]
        original = split.pop(0)
        split.extend(
            [
                {**original, "activity_id": "activity-001a", "active_effort_minutes": 4},
                {**original, "activity_id": "activity-001b", "active_effort_minutes": 6},
            ]
        )
        _assessment_a, friction_a, _diagnostics_a = build_mapping_assessment_payload(
            _profile(), _mapping_spec(), _manifest(), activity_rows=activities
        )
        _assessment_b, friction_b, _diagnostics_b = build_mapping_assessment_payload(
            _profile(), _mapping_spec(), _manifest(), activity_rows=split
        )

        self.assertEqual(
            friction_a["active_effort"]["observed_total_minutes"],
            friction_b["active_effort"]["observed_total_minutes"],
        )
        self.assertEqual(friction_b["active_effort"]["observed_total_minutes"], 45.0)

    def test_confirmed_mapping_has_exact_multidimensional_friction_oracle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            activity_path = root / "activity.jsonl"
            activity_path.write_text(
                "\n".join(json.dumps(row, sort_keys=True) for row in _activities()) + "\n",
                encoding="utf-8",
            )
            output = root / "assessment"
            result = assess_mapping(
                _profile(),
                _mapping_spec(),
                _manifest(),
                output,
                activity_log_path=activity_path,
            )
            assessment = json.loads(
                (output / "mapping_assessment.json").read_text(encoding="utf-8")
            )
            friction = json.loads(
                (output / "mapping_friction_summary.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result["mapping_readiness"], "verified")
        self.assertEqual(assessment["required_signal_summary"]["observed_count"], 3)
        self.assertEqual(
            friction["transformation_counts"], {"coercion": 1, "direct": 2, "lookup": 1}
        )
        self.assertEqual(friction["mapping_iteration_count"], 2)
        self.assertEqual(friction["decision_count"], 1)
        self.assertEqual(friction["approval_activity_count"], 1)
        self.assertEqual(friction["elapsed_onboarding_cycle_seconds"], 3600.0)
        self.assertEqual(friction["active_effort"]["measurement_status"], "complete")
        self.assertEqual(friction["active_effort"]["complete_total_minutes"], 45.0)
        self.assertEqual(friction["active_effort"]["activity_denominator"], 4)

    def test_absent_activity_log_keeps_effort_null_without_extrapolation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "assessment"
            assess_mapping(_profile(), _mapping_spec(), _manifest(), output)
            friction = json.loads(
                (output / "mapping_friction_summary.json").read_text(encoding="utf-8")
            )

        self.assertEqual(friction["active_effort"]["measurement_status"], "not_recorded")
        self.assertIsNone(friction["active_effort"]["observed_total_minutes"])
        self.assertIsNone(friction["active_effort"]["complete_total_minutes"])
        self.assertEqual(friction["active_effort"]["activity_denominator"], 0)

    def test_partial_activity_effort_is_observed_only_and_denominated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            activities = _activities()
            activities[-1]["active_effort_minutes"] = None
            activity_path = root / "activity.jsonl"
            activity_path.write_text(
                "\n".join(json.dumps(row, sort_keys=True) for row in activities) + "\n",
                encoding="utf-8",
            )
            output = root / "assessment"
            assess_mapping(
                _profile(),
                _mapping_spec(),
                _manifest(),
                output,
                activity_log_path=activity_path,
            )
            friction = json.loads(
                (output / "mapping_friction_summary.json").read_text(encoding="utf-8")
            )
            diagnostics = json.loads((output / "diagnostics.json").read_text(encoding="utf-8"))

        self.assertEqual(friction["active_effort"]["measurement_status"], "partial")
        self.assertEqual(friction["active_effort"]["observed_total_minutes"], 35.0)
        self.assertIsNone(friction["active_effort"]["complete_total_minutes"])
        self.assertEqual(friction["active_effort"]["observed_activity_count"], 3)
        self.assertEqual(friction["active_effort"]["activity_denominator"], 4)
        self.assertIn(
            "MAPPING_ACTIVITY_EFFORT_INCOMPLETE",
            {row["code"] for row in diagnostics["diagnostics"]},
        )

    def test_unmapped_signal_caps_analytics_at_mapping_only(self) -> None:
        spec = _mapping_spec()
        status_mapping = next(
            row for row in spec["mappings"] if row["canonical_signal"] == "status"
        )
        status_mapping.update(
            {
                "source_paths": [],
                "transformation": "unmapped",
                "semantic_status": "unresolved",
                "transformation_tested": False,
                "approval": _approval("unresolved"),
            }
        )
        assessment, _friction, diagnostics = build_mapping_assessment_payload(
            _profile(), spec, _manifest()
        )
        readiness = build_evaluation_readiness(
            _telemetry_validation(),
            _manifest(),
            run_count=2,
            quarantined_run_count=0,
            mapping_assessment=assessment,
            governance_safe=True,
            minimum_tier="mapping_only",
        )

        self.assertEqual(assessment["mapping_readiness"], "provisional")
        self.assertIn("MAPPING_REQUIRED_SIGNAL_UNMAPPED", {row["code"] for row in diagnostics})
        self.assertEqual(readiness["attained_tier"], "mapping_only")
        self.assertTrue(readiness["minimum_tier_met"])

    def test_confirmed_semantics_without_signal_approval_remain_provisional(self) -> None:
        spec = _mapping_spec()
        status_mapping = next(
            row for row in spec["mappings"] if row["canonical_signal"] == "status"
        )
        status_mapping["approval"] = _approval("unresolved")

        assessment, _friction, diagnostics = build_mapping_assessment_payload(
            _profile(), spec, _manifest()
        )

        self.assertEqual(assessment["mapping_readiness"], "provisional")
        self.assertFalse(assessment["signals"]["status"]["semantically_approved"])
        self.assertIn("MAPPING_SEMANTICS_UNRESOLVED", {row["code"] for row in diagnostics})

    def test_unapproved_mapping_privacy_is_a_bundle_blocker(self) -> None:
        spec = _mapping_spec()
        spec["privacy_approval"] = _approval("unresolved")
        assessment, _friction, _diagnostics = build_mapping_assessment_payload(
            _profile(), spec, _manifest()
        )
        readiness = build_evaluation_readiness(
            _telemetry_validation(),
            _manifest(),
            run_count=2,
            quarantined_run_count=0,
            mapping_assessment=assessment,
            governance_safe=True,
            minimum_tier="mapping_only",
        )

        self.assertEqual(assessment["mapping_readiness"], "blocked")
        self.assertEqual(readiness["attained_tier"], "blocked")
        self.assertFalse(readiness["minimum_tier_met"])


if __name__ == "__main__":
    unittest.main()
