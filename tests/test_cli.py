from __future__ import annotations

import io
import json
import logging
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from importlib import resources
from pathlib import Path

from agent_eval_toolkit.cli import build_parser, main, run_pipeline
from agent_eval_toolkit.exceptions import DataValidationError, ToolkitError
from tests.helpers import ROOT


class CliTest(unittest.TestCase):
    def test_build_parser_accepts_run_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "run",
                "--workflow-config",
                "workflow.json",
                "--raw-logs",
                "logs.jsonl",
                "--evaluation-manifest",
                "evaluation.json",
                "--output-dir",
                "out",
            ]
        )

        self.assertEqual(args.command, "run")
        self.assertEqual(args.output_dir, "out")

    def test_validate_command_reports_success(self) -> None:
        with redirect_stderr(io.StringIO()):
            with tempfile.TemporaryDirectory() as temp_dir:
                with redirect_stdout(io.StringIO()):
                    exit_code = main(
                        [
                            "validate",
                            "--workflow-config",
                            str(ROOT / "data/sample_inputs/pilot_workflow.json"),
                            "--raw-logs",
                            str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
                            "--evaluation-manifest",
                            str(ROOT / "data/sample_inputs/evaluation_manifest.json"),
                            "--output-json",
                            str(Path(temp_dir, "validation.json")),
                        ]
                    )

        self.assertEqual(exit_code, 0)

    def test_validate_command_reports_failure_for_missing_telemetry(self) -> None:
        missing_telemetry_path = (
            ROOT / "data/readiness_samples/enterprise_readiness_pack/missing_telemetry.jsonl"
        )
        with redirect_stderr(io.StringIO()):
            with redirect_stdout(io.StringIO()):
                exit_code = main(
                    [
                        "validate",
                        "--workflow-config",
                        str(ROOT / "data/sample_inputs/pilot_workflow.json"),
                        "--raw-logs",
                        str(missing_telemetry_path),
                        "--evaluation-manifest",
                        str(ROOT / "data/sample_inputs/evaluation_manifest.json"),
                    ]
                )

        self.assertEqual(exit_code, 1)

    def test_init_evaluation_scaffolds_templates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            exit_code = main(["init-evaluation", "--output-dir", temp_dir])
            files = {path.name for path in Path(temp_dir).iterdir()}

        self.assertEqual(exit_code, 0)
        self.assertIn("workflow_config.template.json", files)
        self.assertIn("evaluation_manifest.monitoring.template.json", files)

    def test_packaged_templates_match_source_templates(self) -> None:
        source_names = {path.name for path in Path(ROOT, "templates").iterdir() if path.is_file()}
        packaged_names = {
            entry.name
            for entry in resources.files("agent_eval_toolkit.resources.templates").iterdir()
            if entry.is_file() and entry.name != "__init__.py"
        }

        self.assertEqual(packaged_names, source_names)

    def test_packaged_docs_match_source_docs(self) -> None:
        source_names = {path.name for path in Path(ROOT, "docs").iterdir() if path.is_file()}
        packaged_names = {
            entry.name
            for entry in resources.files("agent_eval_toolkit.resources.docs").iterdir()
            if entry.is_file() and entry.name != "__init__.py"
        }

        self.assertEqual(packaged_names, source_names)

    def test_main_returns_error_code_for_invalid_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid_config = Path(temp_dir, "workflow.json")
            invalid_config.write_text('{"workflow_family": ""}', encoding="utf-8")
            raw_logs = ROOT / "data/sample_inputs/pilot_logs.jsonl"
            evaluation_manifest = ROOT / "data/sample_inputs/evaluation_manifest.json"
            stderr_buffer = io.StringIO()

            previous_disable_level = logging.root.manager.disable
            logging.disable(logging.CRITICAL)
            try:
                with redirect_stderr(stderr_buffer):
                    exit_code = main(
                        [
                            "--log-level",
                            "ERROR",
                            "run",
                            "--workflow-config",
                            str(invalid_config),
                            "--raw-logs",
                            str(raw_logs),
                            "--evaluation-manifest",
                            str(evaluation_manifest),
                            "--output-dir",
                            temp_dir,
                        ]
                    )
            finally:
                logging.disable(previous_disable_level)

            self.assertEqual(exit_code, 1)

    def test_python_module_propagates_nonzero_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid_config = Path(temp_dir, "workflow.json")
            invalid_config.write_text('{"workflow_family": ""}', encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agent_eval_toolkit",
                    "run",
                    "--workflow-config",
                    str(invalid_config),
                    "--raw-logs",
                    str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
                    "--evaluation-manifest",
                    str(ROOT / "data/sample_inputs/evaluation_manifest.json"),
                    "--output-dir",
                    str(Path(temp_dir, "results")),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 1)

    def test_run_pipeline_writes_expected_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_pipeline(
                workflow_config_path=str(ROOT / "data/sample_inputs/pilot_workflow.json"),
                raw_logs_path=str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
                evaluation_manifest_path=str(ROOT / "data/sample_inputs/evaluation_manifest.json"),
                baseline_context_path=str(ROOT / "data/sample_inputs/baseline_context.json"),
                output_dir=temp_dir,
            )

            files = sorted(path.name for path in Path(temp_dir).iterdir())
            scorecard = json.loads(Path(temp_dir, "scorecard.json").read_text(encoding="utf-8"))

            self.assertEqual(
                files,
                [
                    "diagnostics.json",
                    "enterprise_evaluation_report.md",
                    "evaluation_package.json",
                    "evaluation_readiness.json",
                    "excluded_runs.json",
                    "input_validation.json",
                    "monitoring_summary.csv",
                    "normalized_events.json",
                    "pilot_report.md",
                    "quarantined_runs.json",
                    "redaction_report.json",
                    "scorecard.json",
                    "telemetry_coverage.csv",
                    "telemetry_validation.json",
                    "workflow_runs.json",
                ],
            )
            self.assertEqual(scorecard["workflow_family"], "support_ticket_resolution")
            self.assertTrue(scorecard["telemetry_validation"]["passed"])

    def test_run_pipeline_refuses_stale_outputs_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            arguments = {
                "workflow_config_path": str(ROOT / "data/sample_inputs/pilot_workflow.json"),
                "raw_logs_path": str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
                "evaluation_manifest_path": str(
                    ROOT / "data/sample_inputs/evaluation_manifest.json"
                ),
                "output_dir": temp_dir,
            }
            run_pipeline(**arguments)

            with self.assertRaises(DataValidationError):
                run_pipeline(**arguments)

            run_pipeline(**arguments, overwrite=True)
            self.assertTrue(Path(temp_dir, "scorecard.json").is_file())

    def test_overwrite_removes_stale_success_before_a_failed_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            arguments = {
                "workflow_config_path": str(ROOT / "data/sample_inputs/pilot_workflow.json"),
                "raw_logs_path": str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
                "evaluation_manifest_path": str(
                    ROOT / "data/sample_inputs/evaluation_manifest.json"
                ),
                "output_dir": temp_dir,
            }
            run_pipeline(**arguments)
            self.assertTrue(Path(temp_dir, "scorecard.json").is_file())

            invalid_config = Path(temp_dir).parent / f"{Path(temp_dir).name}-invalid.json"
            invalid_config.write_text('{"workflow_family": ""}', encoding="utf-8")
            try:
                with self.assertRaises(ToolkitError):
                    run_pipeline(
                        **{**arguments, "workflow_config_path": str(invalid_config)},
                        overwrite=True,
                    )
            finally:
                invalid_config.unlink(missing_ok=True)

            self.assertFalse(Path(temp_dir, "scorecard.json").exists())
            self.assertFalse(Path(temp_dir, "enterprise_evaluation_report.md").exists())

    def test_failed_run_emits_diagnostics_but_no_scorecard(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            exit_code = main(
                [
                    "run",
                    "--workflow-config",
                    str(ROOT / "data/sample_inputs/pilot_workflow.json"),
                    "--raw-logs",
                    str(
                        ROOT
                        / "data/readiness_samples/enterprise_readiness_pack/missing_telemetry.jsonl"
                    ),
                    "--evaluation-manifest",
                    str(ROOT / "data/sample_inputs/evaluation_manifest.json"),
                    "--output-dir",
                    temp_dir,
                ]
            )

            self.assertEqual(exit_code, 1)
            self.assertTrue(Path(temp_dir, "input_validation.json").is_file())
            self.assertFalse(Path(temp_dir, "scorecard.json").exists())
            self.assertFalse(Path(temp_dir, "enterprise_evaluation_report.md").exists())

    def test_late_telemetry_failure_cannot_publish_staged_success_artifacts(self) -> None:
        pack = ROOT / "data/readiness_samples/enterprise_readiness_pack"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_logs = root / "missing-quality.jsonl"
            rows = [
                json.loads(line)
                for line in (pack / "complete_telemetry.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            for row in rows:
                row.pop("quality_score", None)
            raw_logs.write_text(
                "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
                encoding="utf-8",
            )
            output = root / "results"

            readiness = run_pipeline(
                workflow_config_path=str(pack / "workflow_config.json"),
                raw_logs_path=str(raw_logs),
                evaluation_manifest_path=str(pack / "manifest_complete.json"),
                output_dir=str(output),
            )

            files = {path.name for path in output.iterdir()}
            diagnostics = json.loads((output / "diagnostics.json").read_text(encoding="utf-8"))

        self.assertEqual(readiness["attained_tier"], "mapping_only")
        self.assertFalse(readiness["minimum_tier_met"])
        self.assertNotIn("scorecard.json", files)
        self.assertEqual(diagnostics["stage"], "evidence_readiness")
        self.assertIn(
            "TELEMETRY_REQUIRED_SIGNAL_MISSING",
            {item["code"] for item in diagnostics["diagnostics"]},
        )

    def test_success_artifacts_use_logical_source_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir, "results")
            run_pipeline(
                workflow_config_path=str(
                    (ROOT / "data/sample_inputs/pilot_workflow.json").resolve()
                ),
                raw_logs_path=str((ROOT / "data/sample_inputs/pilot_logs.jsonl").resolve()),
                evaluation_manifest_path=str(
                    (ROOT / "data/sample_inputs/evaluation_manifest.json").resolve()
                ),
                output_dir=str(output),
            )
            rendered = "\n".join(path.read_text(encoding="utf-8") for path in output.iterdir())
            normalized = json.loads((output / "normalized_events.json").read_text(encoding="utf-8"))

        self.assertNotIn(str(ROOT.resolve()), rendered)
        self.assertEqual({event["evidence"]["source_file"] for event in normalized}, {"raw_logs"})

    def test_verify_package_detects_artifact_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir, "results")
            arguments = {
                "workflow_config_path": str(ROOT / "data/sample_inputs/pilot_workflow.json"),
                "raw_logs_path": str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
                "evaluation_manifest_path": str(
                    ROOT / "data/sample_inputs/evaluation_manifest.json"
                ),
                "baseline_context_path": str(ROOT / "data/sample_inputs/baseline_context.json"),
                "output_dir": str(output),
            }
            run_pipeline(**arguments)
            command = [
                "verify-package",
                "--package-dir",
                str(output),
                "--workflow-config",
                arguments["workflow_config_path"],
                "--raw-logs",
                arguments["raw_logs_path"],
                "--evaluation-manifest",
                arguments["evaluation_manifest_path"],
                "--baseline-context",
                arguments["baseline_context_path"],
            ]
            with redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main(command), 0)
                self.assertTrue(json.loads(stdout.getvalue())["passed"])

            (output / "scorecard.json").write_text("{}\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main(command), 1)
                verification = json.loads(stdout.getvalue())

        self.assertFalse(verification["checks"]["artifact_hashes"]["passed"])
        self.assertIn(
            "PACKAGE_HASH_MISMATCH",
            {item["code"] for item in verification["diagnostics"]},
        )

    def test_failure_bundle_has_structured_code_and_no_success_artifacts(self) -> None:
        pack = ROOT / "data/readiness_samples/enterprise_readiness_pack"
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(DataValidationError):
                run_pipeline(
                    workflow_config_path=str(pack / "workflow_config.json"),
                    raw_logs_path=str(pack / "anomaly_duplicate_event_id.jsonl"),
                    evaluation_manifest_path=str(pack / "manifest_complete.json"),
                    output_dir=temp_dir,
                )
            files = {path.name for path in Path(temp_dir).iterdir()}
            diagnostics = json.loads(Path(temp_dir, "diagnostics.json").read_text(encoding="utf-8"))

        self.assertEqual(files, {"diagnostics.json", "evaluation_readiness.json"})
        duplicate = next(
            item
            for item in diagnostics["diagnostics"]
            if item["code"] == "CANONICAL_DUPLICATE_EVENT_ID"
        )
        self.assertEqual(duplicate["stage"], "canonical_validation")
        self.assertIn("event_id", duplicate)

    def test_captured_output_is_included_in_restricted_canary_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = json.loads(
                (ROOT / "data/sample_inputs/pilot_workflow.json").read_text(encoding="utf-8")
            )
            canary = "PRIVATE-CAPTURED-OUTPUT-CANARY"
            config["restricted_canaries"] = {"captured": canary}
            config_path = root / "workflow.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            output = root / "results"
            with self.assertRaisesRegex(DataValidationError, "Restricted canary"):
                run_pipeline(
                    workflow_config_path=str(config_path),
                    raw_logs_path=str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
                    evaluation_manifest_path=str(
                        ROOT / "data/sample_inputs/evaluation_manifest.json"
                    ),
                    output_dir=str(output),
                    captured_output={"stderr": canary},
                )
            rendered = "\n".join(path.read_text(encoding="utf-8") for path in output.iterdir())

        self.assertNotIn(canary, rendered)
        self.assertFalse((output / "scorecard.json").exists())


if __name__ == "__main__":
    unittest.main()
