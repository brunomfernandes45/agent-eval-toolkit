from __future__ import annotations

import unittest

from agent_eval_toolkit.readiness import build_evaluation_readiness
from tests.helpers import sample_evaluation_manifest


def _coverage(supported: bool, observed: int, denominator: int = 2) -> dict:
    return {
        "supported": supported,
        "runs_with_signal": observed,
        "run_denominator": denominator,
    }


class EvaluationReadinessTest(unittest.TestCase):
    def test_metric_families_are_classified_and_withheld_independently(self) -> None:
        manifest = sample_evaluation_manifest()
        manifest["evaluation_design"] = "ab_test"
        manifest["evidence_context"] = {
            "source_kind": "synthetic_fixture",
            "mapping_assessment_required": False,
        }
        manifest["publication_policy"] = {"minimum_evidence_tier": "partial"}
        manifest["telemetry_requirements"] = {
            "required": ["run_id", "timestamp", "status", "quality_score"],
            "optional": ["observed_cost_usd"],
        }
        telemetry = {
            "passed": False,
            "configuration_consistency": {"passed": True},
            "coverage_by_signal": {
                "run_id": _coverage(True, 2),
                "event_type": _coverage(True, 2),
                "status": _coverage(True, 2),
                "timestamp": _coverage(True, 2),
                "quality_score": _coverage(True, 0),
                "observed_cost_usd": _coverage(False, 0),
            },
        }

        readiness = build_evaluation_readiness(
            telemetry,
            manifest,
            run_count=2,
            quarantined_run_count=0,
            mapping_assessment=None,
            governance_safe=True,
            minimum_tier="partial",
        )

        statuses = readiness["metric_statuses"]
        self.assertEqual(readiness["attained_tier"], "partial")
        self.assertEqual(statuses["duration"]["status"], "available")
        self.assertEqual(statuses["reliability"]["status"], "available")
        self.assertEqual(statuses["comparator"]["status"], "available")
        self.assertEqual(statuses["quality"]["status"], "withheld")
        self.assertEqual(statuses["observed_cost"]["status"], "unsupported")
        self.assertEqual(statuses["economic_estimates"]["status"], "withheld")

    def test_quarantine_changes_population_denominator_without_becoming_zero(self) -> None:
        manifest = sample_evaluation_manifest()
        manifest["evaluation_design"] = "ab_test"
        manifest["evidence_context"] = {
            "source_kind": "synthetic_fixture",
            "mapping_assessment_required": False,
        }
        manifest["publication_policy"] = {"minimum_evidence_tier": "partial"}
        manifest["telemetry_requirements"] = {
            "required": ["run_id", "timestamp", "status"],
            "optional": [],
        }
        telemetry = {
            "passed": True,
            "configuration_consistency": {"passed": True},
            "coverage_by_signal": {
                "run_id": _coverage(True, 2),
                "event_type": _coverage(True, 2),
                "status": _coverage(True, 2),
                "timestamp": _coverage(True, 2),
            },
        }

        readiness = build_evaluation_readiness(
            telemetry,
            manifest,
            run_count=2,
            quarantined_run_count=1,
            mapping_assessment=None,
            governance_safe=True,
            minimum_tier="partial",
        )

        reliability = readiness["metric_statuses"]["reliability"]
        self.assertEqual(readiness["attained_tier"], "partial")
        self.assertEqual(reliability["status"], "partial")
        self.assertEqual(reliability["covered_run_count"], 2)
        self.assertEqual(reliability["eligible_run_count"], 2)
        self.assertEqual(reliability["population_denominator"], 3)
        self.assertEqual(readiness["metric_statuses"]["comparator"]["status"], "withheld")


if __name__ == "__main__":
    unittest.main()
