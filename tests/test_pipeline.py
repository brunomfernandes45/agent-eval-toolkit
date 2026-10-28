from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import agent_eval_toolkit
from agent_eval_toolkit.cli import run_pipeline
from agent_eval_toolkit.io_utils import write_json
from agent_eval_toolkit.langfuse import prepare_langfuse
from agent_eval_toolkit.mapping import assess_mapping

ROOT = Path(__file__).resolve().parents[1]


class PipelineTest(unittest.TestCase):
    def test_sample_pipeline_outputs_expected_files_and_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_pipeline(
                workflow_config_path=str(ROOT / "data/sample_inputs/pilot_workflow.json"),
                raw_logs_path=str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
                evaluation_manifest_path=str(ROOT / "data/sample_inputs/evaluation_manifest.json"),
                baseline_context_path=str(ROOT / "data/sample_inputs/baseline_context.json"),
                output_dir=temp_dir,
            )

            scorecard = json.loads(Path(temp_dir, "scorecard.json").read_text(encoding="utf-8"))
            workflow_runs = json.loads(
                Path(temp_dir, "workflow_runs.json").read_text(encoding="utf-8")
            )
            report_text = Path(temp_dir, "pilot_report.md").read_text(encoding="utf-8")
            package = json.loads(
                Path(temp_dir, "evaluation_package.json").read_text(encoding="utf-8")
            )
            redaction_report = json.loads(
                Path(temp_dir, "redaction_report.json").read_text(encoding="utf-8")
            )

            runs_by_id = {run["run_id"]: run for run in workflow_runs}

            self.assertEqual(scorecard["run_count"], 3)
            self.assertEqual(scorecard["observed_summary"]["completion_rate"], 0.667)
            self.assertTrue(scorecard["telemetry_validation"]["passed"])
            self.assertEqual(scorecard["missing_telemetry_summary"], {"quality_score": 1})
            self.assertIn("comparator_summary", scorecard)
            self.assertIn("measurement_window_summary", scorecard)
            self.assertEqual(package["evidence"]["raw_payload_retention"], "disabled")
            self.assertIn("ticket_text", redaction_report["redacted_field_counts"])
            self.assertEqual(runs_by_id["run-002"]["retry_count"], 1)
            self.assertIn("Assumption-Based Summary", report_text)
            self.assertIn("Telemetry Coverage", report_text)
            self.assertIn("Taxonomy Classification", report_text)
            self.assertEqual(agent_eval_toolkit.__version__, "0.4.0")

    def test_prepared_langfuse_export_runs_after_recorded_manual_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            prepared = root / "prepared"
            results = root / "results"
            prepare_langfuse(
                ROOT / "data/langfuse_samples/observations.camel.json",
                ROOT / "data/langfuse_samples/langfuse_mapping.approved.json",
                prepared,
            )

            manifest = json.loads(
                (prepared / "evaluation_manifest.monitoring.json").read_text(encoding="utf-8")
            )
            manifest["onboarding"].update(
                {
                    "trace_as_workflow_run_approved": True,
                    "enterprise_evaluation_gate": "approved",
                    "reviewer": "synthetic-readiness-reviewer",
                    "review_evidence": "All synthetic trace audit rows reviewed",
                }
            )
            manifest["evidence_context"] = {
                "source_kind": "synthetic_fixture",
                "mapping_assessment_required": True,
            }
            approved_manifest = root / "approved-manifest.json"
            write_json(approved_manifest, manifest)

            mapping_spec = json.loads((prepared / "mapping_spec.json").read_text(encoding="utf-8"))
            approval = {
                "status": "approved",
                "reviewer_role": "synthetic-readiness-reviewer",
                "evidence_ref": "reviewed-trace-audit-sample",
            }
            mapping_spec["workflow_boundary_approval"] = approval
            for mapping_row in mapping_spec["mappings"]:
                if mapping_row["semantic_status"] != "confirmed":
                    mapping_row["semantic_status"] = "confirmed"
                    mapping_row["approval"] = approval
            approved_mapping_spec = root / "approved-mapping-spec.json"
            write_json(approved_mapping_spec, mapping_spec)
            mapping_output = root / "mapping-assessment"
            assess_mapping(
                prepared / "source_profile.json",
                approved_mapping_spec,
                approved_manifest,
                mapping_output,
            )

            run_pipeline(
                workflow_config_path=str(prepared / "workflow_config.json"),
                raw_logs_path=str(prepared / "canonical_events.jsonl"),
                evaluation_manifest_path=str(approved_manifest),
                mapping_assessment_path=str(mapping_output / "mapping_assessment.json"),
                output_dir=str(results),
            )

            scorecard = json.loads((results / "scorecard.json").read_text(encoding="utf-8"))
            observed = scorecard["observed_summary"]
            run = scorecard["runs"][0]["observed_metrics"]
            self.assertEqual(scorecard["evaluation"]["design_claim_ceiling"], "descriptive")
            self.assertEqual(
                scorecard["evaluation"]["attained_claim_strength"],
                "conformance_readiness",
            )
            self.assertEqual(scorecard["run_count"], 1)
            self.assertEqual(run["duration_seconds"], 180.0)
            self.assertEqual(observed["total_input_tokens"], 700)
            self.assertEqual(observed["total_output_tokens"], 140)
            self.assertEqual(observed["total_observed_cost_usd"], 0.03)
            self.assertEqual(run["avg_quality_score"], 0.91)
            self.assertEqual(run["tool_call_count"], 1)
            self.assertEqual(
                scorecard["assumption_based_summary"]["values"],
                {
                    "avg_estimated_labor_value_delta_usd": 0.0,
                    "avg_estimated_net_value_delta_usd": -0.03,
                    "total_estimated_labor_value_delta_usd": 0.0,
                    "total_estimated_net_value_delta_usd": -0.03,
                },
            )

            rendered_results = "\n".join(
                path.read_text(encoding="utf-8") for path in results.iterdir() if path.is_file()
            )
            self.assertNotIn("PRIVATE CUSTOMER MESSAGE", rendered_results)
            self.assertNotIn("PRIVATE GENERATED ANSWER", rendered_results)


if __name__ == "__main__":
    unittest.main()
