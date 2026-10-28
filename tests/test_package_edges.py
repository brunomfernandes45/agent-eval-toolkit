from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_eval_toolkit import package as package_module
from agent_eval_toolkit.exceptions import DataValidationError
from agent_eval_toolkit.traceability import file_fingerprint, hash_payload, hash_raw_line


class PackageEdgeTest(unittest.TestCase):
    def test_build_package_records_optional_baseline_and_all_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inputs: dict[str, Path] = {}
            for logical_name in ("workflow", "logs", "manifest", "baseline"):
                target = root / f"{logical_name}.json"
                target.write_text("{}\n", encoding="utf-8")
                inputs[logical_name] = target
            artifact = root / "scorecard.json"
            artifact.write_text("{}\n", encoding="utf-8")
            scorecard = {
                "evaluation": {
                    "evaluation_id": "edge-evaluation",
                    "evaluation_design": "monitoring",
                    "configuration": {},
                },
                "workflow_family": "edge-workflow",
                "workflow_definition_id": "edge-workflow.v1",
                "assumption_based_summary": {"assumptions_used": {}},
                "measurement_window_summary": {},
            }
            with (
                patch.object(package_module, "_find_dependency_lock", return_value=None),
                patch.object(package_module, "build_schema_index", return_value=[]),
            ):
                result = package_module.build_evaluation_package(
                    workflow_config_path=str(inputs["workflow"]),
                    raw_logs_path=str(inputs["logs"]),
                    evaluation_manifest_path=str(inputs["manifest"]),
                    baseline_context_path=str(inputs["baseline"]),
                    output_dir=str(root),
                    scorecard=scorecard,
                    telemetry_validation={"passed": False},
                    redaction_report={
                        "policy": {"evidence_mode": "anchor_hash_only"},
                        "redacted_field_counts": {"secret": 1},
                        "unprotected_restricted_fields": ["unsafe"],
                    },
                    excluded_runs=[{"run_id": "run-1", "reason": "outside window"}],
                    artifact_names=["scorecard.json"],
                )

        self.assertEqual(result["dependency_lock"]["status"], "unavailable")
        self.assertIn("baseline_context", result["input_fingerprints"])
        self.assertEqual(result["taxonomy"], {})
        self.assertEqual(len(result["warnings"]), 4)

    def test_schema_index_rejects_missing_directory_and_sorts_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaises(DataValidationError):
                package_module.build_schema_index(root / "missing")
            (root / "z.schema.json").write_text("{}\n", encoding="utf-8")
            (root / "a.schema.json").write_text("{}\n", encoding="utf-8")
            (root / "ignored.json").write_text("{}\n", encoding="utf-8")
            index = package_module.build_schema_index(root)

        self.assertEqual(
            [item["path"] for item in index],
            ["schemas/a.schema.json", "schemas/z.schema.json"],
        )

    def test_schema_index_uses_packaged_resources_and_rejects_absent_resources(self) -> None:
        original_is_dir = Path.is_dir

        def no_filesystem_schema(path: Path) -> bool:
            if path.name == "schemas":
                return False
            return original_is_dir(path)

        with tempfile.TemporaryDirectory() as temp_dir:
            resource_root = Path(temp_dir)
            (resource_root / "b.schema.json").write_text('{"b":2}\n', encoding="utf-8")
            (resource_root / "a.schema.json").write_text('{"a":1}\n', encoding="utf-8")
            (resource_root / "ignored.txt").write_text("ignored", encoding="utf-8")
            with (
                patch.object(Path, "is_dir", no_filesystem_schema),
                patch.object(package_module.resources, "files", return_value=resource_root),
            ):
                index = package_module.build_schema_index()
        self.assertEqual(
            [item["path"] for item in index],
            ["schemas/a.schema.json", "schemas/b.schema.json"],
        )

        with (
            patch.object(Path, "is_dir", no_filesystem_schema),
            patch.object(
                package_module.resources,
                "files",
                side_effect=ModuleNotFoundError("missing resource package"),
            ),
        ):
            with self.assertRaisesRegex(DataValidationError, "Schemas were not found"):
                package_module.build_schema_index()

        with tempfile.TemporaryDirectory() as empty_dir:
            with (
                patch.object(Path, "is_dir", no_filesystem_schema),
                patch.object(package_module.resources, "files", return_value=Path(empty_dir)),
            ):
                with self.assertRaisesRegex(DataValidationError, "Schemas were not found"):
                    package_module.build_schema_index()

    def test_schema_index_uses_installed_schema_directory(self) -> None:
        original_is_dir = Path.is_dir

        with tempfile.TemporaryDirectory() as temp_dir:
            prefix = Path(temp_dir)
            installed = prefix / "schemas"
            installed.mkdir()
            (installed / "installed.schema.json").write_text("{}\n", encoding="utf-8")

            def only_installed_schema(path: Path) -> bool:
                if path == installed:
                    return True
                if path.name == "schemas":
                    return False
                return original_is_dir(path)

            with (
                patch.object(package_module.sys, "prefix", str(prefix)),
                patch.object(Path, "is_dir", only_installed_schema),
            ):
                result = package_module.build_schema_index()

        self.assertEqual(result[0]["path"], "schemas/installed.schema.json")

    def test_verifier_rejects_missing_invalid_and_non_object_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(DataValidationError, "not found"):
                package_module.verify_evaluation_package(root)
            target = root / "evaluation_package.json"
            target.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(DataValidationError, "valid JSON"):
                package_module.verify_evaluation_package(target)
            target.write_text("[]\n", encoding="utf-8")
            with self.assertRaisesRegex(DataValidationError, "JSON object"):
                package_module.verify_evaluation_package(target)

    def test_input_fingerprint_checks_cover_invalid_missing_unavailable_and_changed(self) -> None:
        diagnostics: list[dict[str, object]] = []
        invalid = package_module._verify_fingerprint_group(
            [], {}, stage="inputs", diagnostics=diagnostics
        )
        self.assertFalse(invalid["passed"])

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            present = root / "present.json"
            present.write_text("actual\n", encoding="utf-8")
            expected_present = file_fingerprint(present, logical_path="changed")
            expected_present["sha256"] = "0" * 64
            expected = {
                "bad_identifier": "not-a-fingerprint",
                "not_supplied": {
                    "path": "not_supplied",
                    "sha256": "0" * 64,
                    "size_bytes": 0,
                },
                "unavailable": {
                    "path": "unavailable",
                    "sha256": "0" * 64,
                    "size_bytes": 0,
                },
                "changed": expected_present,
            }
            result = package_module._verify_fingerprint_group(
                expected,
                {
                    "bad_identifier": present,
                    "not_supplied": None,
                    "unavailable": root / "missing.json",
                    "changed": present,
                },
                stage="inputs",
                diagnostics=diagnostics,
            )

        self.assertFalse(result["passed"])
        self.assertEqual(result["verified_count"], 0)
        self.assertEqual(len(result["mismatches"]), 4)
        self.assertGreaterEqual(len(diagnostics), 4)

    def test_artifact_fingerprint_checks_cover_shape_path_absence_and_mismatch(self) -> None:
        diagnostics: list[dict[str, object]] = []
        malformed = package_module._verify_artifact_fingerprints({}, Path("."), diagnostics)
        self.assertFalse(malformed["passed"])

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact = root / "artifact.json"
            artifact.write_text("actual\n", encoding="utf-8")
            expected_changed = file_fingerprint(artifact, logical_path="artifact.json")
            expected_changed["size_bytes"] += 1
            result = package_module._verify_artifact_fingerprints(
                [
                    "not-an-object",
                    {"path": "../escape", "sha256": "0" * 64, "size_bytes": 0},
                    {"path": "missing.json", "sha256": "0" * 64, "size_bytes": 0},
                    expected_changed,
                ],
                root,
                diagnostics,
            )

        self.assertFalse(result["passed"])
        self.assertEqual(len(result["mismatches"]), 4)
        self.assertEqual(result["verified_count"], 0)

    def test_schema_fingerprint_checks_cover_missing_group_and_mismatch(self) -> None:
        diagnostics: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "actual.schema.json").write_text("{}\n", encoding="utf-8")
            result = package_module._verify_schema_fingerprints(
                {
                    "wrong": "shape",
                },
                root,
                diagnostics,
            )
            missing_expected = package_module._verify_schema_fingerprints(
                [
                    {
                        "path": "schemas/missing.schema.json",
                        "sha256": "0" * 64,
                        "size_bytes": 0,
                    }
                ],
                root,
                diagnostics,
            )

        self.assertFalse(result["passed"])
        self.assertFalse(missing_expected["passed"])
        self.assertTrue(diagnostics)

        with patch.object(
            package_module, "build_schema_index", side_effect=DataValidationError("missing")
        ):
            unavailable = package_module._verify_schema_fingerprints([], None, diagnostics)
        self.assertTrue(unavailable["passed"])

    def test_event_anchor_checks_cover_absent_sources_and_invalid_shapes(self) -> None:
        diagnostics: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            no_input = package_module._verify_event_anchors(root, None, diagnostics)
            self.assertFalse(no_input[0]["passed"])
            self.assertFalse(no_input[1]["passed"])

            raw = root / "raw.jsonl"
            raw.write_text('{"event_id":"evt-1"}\n', encoding="utf-8")
            (root / "normalized_events.json").write_text("{", encoding="utf-8")
            unavailable = package_module._verify_event_anchors(root, raw, diagnostics)
            self.assertFalse(unavailable[0]["passed"])

            (root / "normalized_events.json").write_text("{}\n", encoding="utf-8")
            invalid_shape = package_module._verify_event_anchors(root, raw, diagnostics)

        self.assertEqual(invalid_shape[0]["mismatches"][0]["reason"], "invalid_artifact_shape")

    def test_event_anchor_checks_cover_logical_line_event_and_raw_line_failures(self) -> None:
        diagnostics: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw = root / "raw.jsonl"
            raw_line = '{"event_id":"evt-1","value":1}'
            raw.write_text(raw_line + "\n", encoding="utf-8")
            normalized = [
                "ignored",
                {"event_id": "ignored-without-evidence"},
                {
                    "event_id": "evt-missing-line",
                    "evidence": {
                        "source_file": "/absolute/raw.jsonl",
                        "source_line": 99,
                        "raw_event_hash": "0" * 64,
                        "raw_line_hash": "0" * 64,
                    },
                },
                {
                    "event_id": "evt-1",
                    "evidence": {
                        "source_file": "raw_logs",
                        "source_line": 1,
                        "raw_event_hash": "0" * 64,
                        "raw_line_hash": "f" * 64,
                    },
                },
            ]
            (root / "normalized_events.json").write_text(json.dumps(normalized), encoding="utf-8")
            event_result, line_result = package_module._verify_event_anchors(root, raw, diagnostics)

        self.assertFalse(event_result["passed"])
        self.assertFalse(line_result["passed"])
        self.assertTrue(
            any(item.get("field") == "source_file" for item in event_result["mismatches"])
        )
        self.assertTrue(any("actual_sha256" in item for item in event_result["mismatches"]))
        self.assertTrue(any("actual_sha256" in item for item in line_result["mismatches"]))

    def test_anchor_count_and_small_fingerprint_helpers_cover_edge_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.assertEqual(package_module._anchor_verified_count(root, []), 0)
            target = root / "normalized_events.json"
            target.write_text("{}\n", encoding="utf-8")
            self.assertEqual(package_module._anchor_verified_count(root, []), 0)
            target.write_text(
                json.dumps([{"evidence": {}}, {"evidence": {}}, {}]), encoding="utf-8"
            )
            self.assertEqual(package_module._anchor_verified_count(root, [{}]), 1)

        self.assertFalse(package_module._fingerprint_matches(None, {}))
        self.assertEqual(package_module._expected_hash(None), None)
        mismatch = package_module._fingerprint_mismatch("x", None, None)
        self.assertIsNone(mismatch["expected_size_bytes"])
        self.assertFalse(package_module._safe_logical_path(None))
        self.assertFalse(package_module._safe_logical_path(""))
        self.assertFalse(package_module._safe_logical_path("/absolute"))
        self.assertFalse(package_module._safe_logical_path("a/../b"))
        self.assertTrue(package_module._safe_logical_path("nested/artifact.json"))

    def test_event_hash_helpers_match_valid_anchor_values(self) -> None:
        diagnostics: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = {"event_id": "evt-1", "value": 1}
            raw_line = json.dumps(payload, separators=(",", ":"))
            raw = root / "raw.jsonl"
            raw.write_text(raw_line + "\n", encoding="utf-8")
            (root / "normalized_events.json").write_text(
                json.dumps(
                    [
                        {
                            "event_id": "evt-1",
                            "evidence": {
                                "source_file": "raw_logs",
                                "source_line": 1,
                                "raw_event_hash": hash_payload(payload),
                                "raw_line_hash": hash_raw_line(raw_line),
                            },
                        }
                    ]
                ),
                encoding="utf-8",
            )
            event_result, line_result = package_module._verify_event_anchors(root, raw, diagnostics)

        self.assertTrue(event_result["passed"])
        self.assertTrue(line_result["passed"])
        self.assertEqual(event_result["verified_count"], 1)
        self.assertEqual(diagnostics, [])

    def test_dependency_lock_search_and_warning_empty_path(self) -> None:
        with patch.object(Path, "is_file", return_value=False):
            self.assertIsNone(package_module._find_dependency_lock())
        self.assertEqual(
            package_module._build_warnings(
                {"passed": True},
                {"redacted_field_counts": {}, "unprotected_restricted_fields": []},
                [],
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
