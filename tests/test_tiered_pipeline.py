from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_eval_toolkit.cli import MAPPING_ONLY_ARTIFACTS, run_pipeline
from agent_eval_toolkit.exceptions import ConfigurationError, DataValidationError
from agent_eval_toolkit.package import verify_evaluation_package
from tests.helpers import ROOT, sample_evaluation_manifest


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _partial_logs(path: Path) -> None:
    rows = [
        json.loads(line)
        for line in (ROOT / "data/sample_inputs/pilot_logs.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    rows = [row for row in rows if row["event_id"] != "evt-011"]
    path.write_text(
        "\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )


class TieredPipelineTest(unittest.TestCase):
    def test_one_boundary_anomaly_is_quarantined_and_partial_package_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs = root / "partial.jsonl"
            _partial_logs(logs)
            manifest = sample_evaluation_manifest()
            manifest["evidence_context"] = {
                "source_kind": "synthetic_fixture",
                "mapping_assessment_required": False,
            }
            manifest["publication_policy"] = {"minimum_evidence_tier": "partial"}
            manifest_path = root / "manifest.json"
            _write_json(manifest_path, manifest)
            output = root / "results"

            readiness = run_pipeline(
                workflow_config_path=str(ROOT / "data/sample_inputs/pilot_workflow.json"),
                raw_logs_path=str(logs),
                evaluation_manifest_path=str(manifest_path),
                output_dir=str(output),
            )
            scorecard = json.loads((output / "scorecard.json").read_text(encoding="utf-8"))
            quarantine = json.loads((output / "quarantined_runs.json").read_text(encoding="utf-8"))
            verification = verify_evaluation_package(
                output,
                workflow_config_path=ROOT / "data/sample_inputs/pilot_workflow.json",
                raw_logs_path=logs,
                evaluation_manifest_path=manifest_path,
                schema_dir=ROOT / "schemas",
            )

        self.assertEqual(readiness["attained_tier"], "partial")
        self.assertTrue(readiness["minimum_tier_met"])
        self.assertEqual([row["run_id"] for row in quarantine], ["run-002"])
        duration = scorecard["metric_statuses"]["duration"]
        self.assertEqual(duration["status"], "partial")
        self.assertEqual(duration["covered_run_count"], 2)
        self.assertEqual(duration["eligible_run_count"], 2)
        self.assertEqual(duration["population_denominator"], 3)
        self.assertEqual(scorecard["metric_statuses"]["economic_estimates"]["status"], "withheld")
        self.assertEqual(scorecard["assumption_based_summary"]["values"], {})
        self.assertIn(
            "claim_scope_excludes_one_or_more_quarantined_source_runs",
            scorecard["evaluation"]["withheld_reasons"],
        )
        self.assertTrue(verification["passed"])

    def test_missing_mapping_assessment_publishes_mapping_only_without_scorecard(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = sample_evaluation_manifest()
            manifest["evidence_context"] = {
                "source_kind": "enterprise_operational",
                "mapping_assessment_required": True,
            }
            manifest["publication_policy"] = {"minimum_evidence_tier": "mapping_only"}
            manifest_path = root / "manifest.json"
            _write_json(manifest_path, manifest)
            output = root / "results"

            readiness = run_pipeline(
                workflow_config_path=str(ROOT / "data/sample_inputs/pilot_workflow.json"),
                raw_logs_path=str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
                evaluation_manifest_path=str(manifest_path),
                output_dir=str(output),
            )
            published = {path.name for path in output.iterdir()}

        self.assertEqual(readiness["attained_tier"], "mapping_only")
        self.assertEqual(published, set(MAPPING_ONLY_ARTIFACTS))
        self.assertNotIn("scorecard.json", published)

    def test_unapproved_mapping_privacy_publishes_only_blocked_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = sample_evaluation_manifest()
            manifest["evidence_context"] = {
                "source_kind": "enterprise_operational",
                "mapping_assessment_required": True,
            }
            manifest["publication_policy"] = {"minimum_evidence_tier": "mapping_only"}
            manifest_path = root / "manifest.json"
            _write_json(manifest_path, manifest)
            mapping_path = root / "mapping_assessment.json"
            _write_json(
                mapping_path,
                {
                    "mapping_readiness": "blocked",
                    "mapping_spec_sha256": "a" * 64,
                    "source_profile_sha256": "b" * 64,
                    "approvals": {"privacy_approval": "unresolved"},
                },
            )
            output = root / "results"

            readiness = run_pipeline(
                workflow_config_path=str(ROOT / "data/sample_inputs/pilot_workflow.json"),
                raw_logs_path=str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
                evaluation_manifest_path=str(manifest_path),
                mapping_assessment_path=str(mapping_path),
                output_dir=str(output),
            )
            published = {path.name for path in output.iterdir()}

        self.assertEqual(readiness["attained_tier"], "blocked")
        self.assertEqual(published, {"diagnostics.json", "evaluation_readiness.json"})

    def test_unsafe_aggregation_semantics_remain_a_global_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = json.loads(
                (ROOT / "data/sample_inputs/pilot_workflow.json").read_text(encoding="utf-8")
            )
            config["aggregation_semantics"]["input_tokens"] = "cumulative"
            config_path = root / "workflow.json"
            _write_json(config_path, config)
            output = root / "results"

            with self.assertRaises((ConfigurationError, DataValidationError)):
                run_pipeline(
                    workflow_config_path=str(config_path),
                    raw_logs_path=str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
                    evaluation_manifest_path=str(
                        ROOT / "data/sample_inputs/evaluation_manifest.json"
                    ),
                    output_dir=str(output),
                )
            published = {path.name for path in output.iterdir()}
            diagnostics = json.loads((output / "diagnostics.json").read_text(encoding="utf-8"))

        self.assertEqual(published, {"diagnostics.json", "evaluation_readiness.json"})
        self.assertIn(
            "AGGREGATION_SEMANTICS_UNSUPPORTED",
            {row["code"] for row in diagnostics["diagnostics"]},
        )


if __name__ == "__main__":
    unittest.main()
