from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent_eval_toolkit import cli
from agent_eval_toolkit.exceptions import DataValidationError
from tests.helpers import ROOT


class CliEdgeTest(unittest.TestCase):
    def test_pipeline_wraps_unexpected_exception_and_publishes_safe_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "results"
            with patch.object(cli, "read_json", side_effect=RuntimeError("unexpected")):
                with self.assertRaisesRegex(DataValidationError, "workflow_config") as raised:
                    cli.run_pipeline(
                        workflow_config_path="workflow.json",
                        raw_logs_path="logs.jsonl",
                        evaluation_manifest_path="manifest.json",
                        output_dir=str(output),
                    )
            document = json.loads((output / "diagnostics.json").read_text(encoding="utf-8"))

        self.assertEqual(raised.exception.diagnostic_stage, "workflow_config")
        self.assertEqual(document["stage"], "workflow_config")
        self.assertFalse((output / "scorecard.json").exists())

    def test_pipeline_handles_a_late_redaction_gate_failure_transactionally(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "results"
            redaction_result = {
                "passed": False,
                "policy": {"evidence_mode": "anchor_hash_only"},
                "redacted_field_counts": {},
                "unprotected_restricted_fields": [],
            }
            with patch.object(cli, "build_redaction_report", return_value=redaction_result):
                with self.assertRaisesRegex(DataValidationError, "Restricted fields"):
                    cli.run_pipeline(
                        workflow_config_path=str(ROOT / "data/sample_inputs/pilot_workflow.json"),
                        raw_logs_path=str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
                        evaluation_manifest_path=str(
                            ROOT / "data/sample_inputs/evaluation_manifest.json"
                        ),
                        output_dir=str(output),
                    )
            diagnostics = json.loads((output / "diagnostics.json").read_text(encoding="utf-8"))
            output_files = {path.name for path in output.iterdir()}

        self.assertIn(
            "PRIVACY_RESTRICTED_MAPPING",
            {item["code"] for item in diagnostics["diagnostics"]},
        )
        self.assertEqual(output_files, {"diagnostics.json", "evaluation_readiness.json"})

    def test_prepare_output_root_rejects_file_and_requires_overwrite_for_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_file = root / "output"
            output_file.write_text("not a directory", encoding="utf-8")
            with self.assertRaisesRegex(DataValidationError, "not a directory"):
                cli._prepare_output_root(output_file, overwrite=False)

            output_dir = root / "results"
            output_dir.mkdir()
            (output_dir / "diagnostics.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(DataValidationError, "use --overwrite"):
                cli._prepare_output_root(output_dir, overwrite=False)
            cli._prepare_output_root(output_dir, overwrite=True)

    def test_publish_bundle_preserves_unrelated_files_directories_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stage = root / "stage"
            stage.mkdir()
            (stage / "scorecard.json").write_text('{"fresh":true}\n', encoding="utf-8")
            output = root / "output"
            output.mkdir()
            (output / "scorecard.json").write_text('{"stale":true}\n', encoding="utf-8")
            (output / "notes.txt").write_text("keep", encoding="utf-8")
            (output / "attachments").mkdir()
            (output / "attachments" / "readme.txt").write_text("keep", encoding="utf-8")
            link_target = output / "notes.txt"
            link = output / "notes-link"
            link.symlink_to(link_target)

            cli._publish_bundle(stage, output, ["scorecard.json"])

            self.assertEqual(
                json.loads((output / "scorecard.json").read_text(encoding="utf-8")),
                {"fresh": True},
            )
            self.assertEqual((output / "notes.txt").read_text(encoding="utf-8"), "keep")
            self.assertTrue((output / "attachments" / "readme.txt").is_file())
            self.assertTrue((output / "notes-link").is_symlink())

            with self.assertRaisesRegex(DataValidationError, "incomplete"):
                cli._publish_bundle(root / "missing-stage", output, ["absent.json"])

    def test_input_path_aliases_include_relative_resolved_and_optional_paths(self) -> None:
        aliases = cli._input_path_aliases("workflow.json", "logs.jsonl", "manifest.json", None)
        self.assertEqual(aliases["workflow.json"], "workflow_config")
        self.assertEqual(aliases[str(Path("logs.jsonl").resolve())], "raw_logs")
        self.assertNotIn("baseline_context", aliases.values())
        with_baseline = cli._input_path_aliases(
            "workflow.json", "logs.jsonl", "manifest.json", "baseline.json"
        )
        self.assertIn("baseline_context", with_baseline.values())

    def test_validate_inputs_adds_redaction_diagnostic_when_policy_gate_fails(self) -> None:
        redaction_result = {
            "passed": False,
            "policy": {},
            "redacted_field_counts": {},
            "unprotected_restricted_fields": ["secret"],
        }
        with patch.object(cli, "build_redaction_report", return_value=redaction_result):
            result = cli.validate_inputs(
                str(ROOT / "data/sample_inputs/pilot_workflow.json"),
                str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
                str(ROOT / "data/sample_inputs/evaluation_manifest.json"),
                str(ROOT / "data/sample_inputs/baseline_context.json"),
            )
        self.assertFalse(result["passed"])
        self.assertIn(
            "PRIVACY_RESTRICTED_MAPPING",
            {item["code"] for item in result["diagnostics"]},
        )

    def test_init_refuses_overwrite_and_template_resource_fallback_is_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cli.init_evaluation(temp_dir)
            with self.assertRaisesRegex(DataValidationError, "Refusing to overwrite"):
                cli.init_evaluation(temp_dir)

        original_exists = Path.exists

        def source_templates_absent(path: Path) -> bool:
            if path.name == "templates":
                return False
            return original_exists(path)

        with tempfile.TemporaryDirectory() as temp_dir:
            resources_root = Path(temp_dir)
            (resources_root / "__init__.py").write_text("", encoding="utf-8")
            (resources_root / "b.json").write_text("{}", encoding="utf-8")
            (resources_root / "a.json").write_text("{}", encoding="utf-8")
            with (
                patch.object(Path, "exists", source_templates_absent),
                patch.object(cli.resources, "files", return_value=resources_root),
            ):
                self.assertEqual(cli.list_templates(), ["a.json", "b.json"])

        with (
            patch.object(Path, "exists", source_templates_absent),
            patch.object(
                cli.resources,
                "files",
                side_effect=ModuleNotFoundError("templates missing"),
            ),
        ):
            with self.assertRaisesRegex(DataValidationError, "Templates directory"):
                cli.list_templates()

    def test_main_dispatches_mocked_commands_and_writes_verification_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.object(cli, "run_pipeline") as run:
                self.assertEqual(
                    cli.main(
                        [
                            "run",
                            "--workflow-config",
                            "workflow.json",
                            "--raw-logs",
                            "logs.jsonl",
                            "--evaluation-manifest",
                            "manifest.json",
                            "--output-dir",
                            "out",
                            "--overwrite",
                        ]
                    ),
                    0,
                )
                run.assert_called_once()

            with patch.object(
                cli,
                "prepare_langfuse",
                return_value={"trace_count": 1, "preparation_gate_passed": True},
            ):
                with redirect_stdout(io.StringIO()) as stdout:
                    code = cli.main(
                        [
                            "prepare-langfuse",
                            "--input",
                            "export.json",
                            "--mapping",
                            "mapping.json",
                            "--output-dir",
                            "prepared",
                            "--overwrite",
                        ]
                    )
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(stdout.getvalue())["trace_count"], 1)

            verification_path = root / "verification.json"
            with patch.object(
                cli,
                "verify_evaluation_package",
                return_value={"passed": False, "diagnostics": []},
            ):
                with redirect_stdout(io.StringIO()):
                    code = cli.main(
                        [
                            "verify-package",
                            "--package",
                            "evaluation_package.json",
                            "--workflow-config",
                            "workflow.json",
                            "--raw-logs",
                            "logs.jsonl",
                            "--evaluation-manifest",
                            "manifest.json",
                            "--schemas-dir",
                            "schemas",
                            "--output-json",
                            str(verification_path),
                        ]
                    )
            self.assertEqual(code, 1)
            self.assertFalse(json.loads(verification_path.read_text(encoding="utf-8"))["passed"])

            with patch.object(cli, "list_templates", return_value=["b", "a"]):
                with redirect_stdout(io.StringIO()) as stdout:
                    self.assertEqual(cli.main(["list-templates"]), 0)
                self.assertEqual(stdout.getvalue(), "b\na\n")

    def test_main_converts_direct_toolkit_error_and_unknown_dispatch_to_exit_codes(self) -> None:
        with patch.object(
            cli, "prepare_langfuse", side_effect=DataValidationError("invalid capture")
        ):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(
                    cli.main(
                        [
                            "prepare-langfuse",
                            "--input",
                            "export.json",
                            "--mapping",
                            "mapping.json",
                            "--output-dir",
                            "prepared",
                        ]
                    ),
                    1,
                )

        parser = argparse.Namespace(
            parse_args=lambda argv=None: argparse.Namespace(command="unknown", log_level="WARNING")
        )
        with patch.object(cli, "build_parser", return_value=parser):
            self.assertEqual(cli.main([]), 0)
            self.assertEqual(cli.main(None), 0)


if __name__ == "__main__":
    unittest.main()
