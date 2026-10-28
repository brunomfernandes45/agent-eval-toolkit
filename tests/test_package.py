from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_eval_toolkit.cli import run_pipeline
from agent_eval_toolkit.package import (
    build_evaluation_package,
    compute_bundle_hash,
    verify_evaluation_package,
)
from tests.helpers import ROOT


class EvaluationPackageTest(unittest.TestCase):
    def test_package_contains_deterministic_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inputs = {}
            for name in ("workflow.json", "logs.jsonl", "manifest.json"):
                path = root / name
                path.write_text("{}\n", encoding="utf-8")
                inputs[name] = path
            (root / "scorecard.json").write_text("{}\n", encoding="utf-8")
            scorecard = {
                "evaluation": {
                    "evaluation_id": "evaluation-1",
                    "evaluation_design": "monitoring",
                    "configuration": {},
                    "taxonomy": {},
                },
                "workflow_family": "workflow",
                "workflow_definition_id": "workflow.v1",
                "assumption_based_summary": {"assumptions_used": {}},
                "measurement_window_summary": {},
            }

            package = build_evaluation_package(
                workflow_config_path=str(inputs["workflow.json"]),
                raw_logs_path=str(inputs["logs.jsonl"]),
                evaluation_manifest_path=str(inputs["manifest.json"]),
                baseline_context_path=None,
                output_dir=temp_dir,
                scorecard=scorecard,
                telemetry_validation={"passed": True},
                redaction_report={
                    "policy": {"evidence_mode": "anchor_hash_only"},
                    "redacted_field_counts": {},
                    "unprotected_restricted_fields": [],
                },
                excluded_runs=[],
                artifact_names=["scorecard.json"],
            )

        self.assertEqual(package["toolkit_version"], "0.4.0")
        self.assertEqual(package["artifact_schema_version"], "2.0.0")
        self.assertEqual(package["artifact_fingerprints"][0]["path"], "scorecard.json")
        self.assertTrue(package["schema_fingerprints"])
        self.assertEqual(package["bundle_sha256"], compute_bundle_hash(package))
        self.assertEqual(
            package["input_fingerprints"]["workflow_config"]["path"],
            "workflow_config",
        )
        json.dumps(package, sort_keys=True)

    def test_verifier_distinguishes_canonical_event_and_exact_raw_line_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_logs = root / "logs.jsonl"
            shutil.copy2(ROOT / "data/sample_inputs/pilot_logs.jsonl", raw_logs)
            output = root / "results"
            workflow_config = ROOT / "data/sample_inputs/pilot_workflow.json"
            manifest = ROOT / "data/sample_inputs/evaluation_manifest.json"
            run_pipeline(
                workflow_config_path=str(workflow_config),
                raw_logs_path=str(raw_logs),
                evaluation_manifest_path=str(manifest),
                output_dir=str(output),
            )

            lines = raw_logs.read_text(encoding="utf-8").splitlines()
            lines[0] += " "
            raw_logs.write_text("\n".join(lines) + "\n", encoding="utf-8")
            whitespace_result = verify_evaluation_package(
                output,
                workflow_config_path=workflow_config,
                raw_logs_path=raw_logs,
                evaluation_manifest_path=manifest,
            )
            self.assertTrue(whitespace_result["checks"]["event_hashes"]["passed"])
            self.assertFalse(whitespace_result["checks"]["raw_line_hashes"]["passed"])

            first = json.loads(lines[0])
            first["status"] = "tampered"
            lines[0] = json.dumps(first, separators=(",", ":"))
            raw_logs.write_text("\n".join(lines) + "\n", encoding="utf-8")
            payload_result = verify_evaluation_package(
                output,
                workflow_config_path=workflow_config,
                raw_logs_path=raw_logs,
                evaluation_manifest_path=manifest,
            )

        self.assertFalse(payload_result["checks"]["event_hashes"]["passed"])
        self.assertFalse(payload_result["checks"]["raw_line_hashes"]["passed"])

    def test_verifier_rejects_edited_package_bundle_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "results"
            workflow_config = ROOT / "data/sample_inputs/pilot_workflow.json"
            raw_logs = ROOT / "data/sample_inputs/pilot_logs.jsonl"
            manifest = ROOT / "data/sample_inputs/evaluation_manifest.json"
            run_pipeline(
                workflow_config_path=str(workflow_config),
                raw_logs_path=str(raw_logs),
                evaluation_manifest_path=str(manifest),
                output_dir=str(output),
            )
            package_path = output / "evaluation_package.json"
            package = json.loads(package_path.read_text(encoding="utf-8"))
            package["bundle_sha256"] = "0" * 64
            package_path.write_text(json.dumps(package, sort_keys=True), encoding="utf-8")

            verification = verify_evaluation_package(
                output,
                workflow_config_path=workflow_config,
                raw_logs_path=raw_logs,
                evaluation_manifest_path=manifest,
            )

        self.assertFalse(verification["passed"])
        self.assertFalse(verification["checks"]["bundle_hash"]["passed"])
        self.assertIn(
            "PACKAGE_HASH_MISMATCH",
            {item["code"] for item in verification["diagnostics"]},
        )


if __name__ == "__main__":
    unittest.main()
