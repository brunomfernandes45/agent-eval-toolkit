from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_eval_toolkit.exceptions import (
    ConfigurationError,
    DataValidationError,
)
from agent_eval_toolkit.io_utils import read_jsonl_with_line_numbers
from agent_eval_toolkit.normalize import normalize_logs
from agent_eval_toolkit.opentelemetry import (
    GENERATED_ARTIFACTS,
    prepare_opentelemetry,
)
from agent_eval_toolkit.reconstruct import reconstruct_runs
from agent_eval_toolkit.validation import validate_canonical_rows, validate_workflow_config

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data" / "opentelemetry_samples"


class OpenTelemetryPreparationTest(unittest.TestCase):
    def _mapping(self) -> dict[str, object]:
        return json.loads(
            (SAMPLES / "opentelemetry_mapping.approved.json").read_text(encoding="utf-8")
        )

    def _prepare_with_mapping(self, mapping: object, root: Path) -> None:
        mapping_path = root / "mapping.json"
        mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
        prepare_opentelemetry(
            SAMPLES / "logs.otlp.json",
            mapping_path,
            SAMPLES / "manifest.monitoring.json",
            root / "prepared",
        )

    def test_otlp_json_logs_prepare_canonical_events_without_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "prepared"
            summary = prepare_opentelemetry(
                SAMPLES / "logs.otlp.json",
                SAMPLES / "opentelemetry_mapping.approved.json",
                SAMPLES / "manifest.monitoring.json",
                output,
            )

            self.assertEqual(summary["source_format"], "otlp_json_logs")
            self.assertEqual(summary["log_record_count"], 3)
            self.assertEqual(summary["canonical_event_count"], 3)
            self.assertEqual({path.name for path in output.iterdir()}, set(GENERATED_ARTIFACTS))

            rendered = "\n".join(path.read_text(encoding="utf-8") for path in output.iterdir())
            self.assertNotIn("PRIVATE OTEL CUSTOMER PAYLOAD", rendered)
            redaction = json.loads((output / "redaction_report.json").read_text(encoding="utf-8"))
            self.assertEqual(redaction["log_bodies_present"], 1)
            self.assertEqual(redaction["log_bodies_retained"], 0)

            config = json.loads((output / "workflow_config.json").read_text(encoding="utf-8"))
            validate_workflow_config(config)
            rows = read_jsonl_with_line_numbers(output / "canonical_events.jsonl")
            self.assertTrue(validate_canonical_rows(rows, config)["passed"])
            events = normalize_logs(rows, config, "canonical_events.jsonl")
            runs = reconstruct_runs(events, config)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].run_id, "workflow-run-1")
            self.assertEqual(runs[0].completion_state, "completed")
            self.assertEqual(runs[0].input_tokens, 700)
            self.assertEqual(runs[0].output_tokens, 140)
            self.assertAlmostEqual(runs[0].observed_cost_usd or 0, 0.03)

    def test_trace_id_fallback_requires_explicit_approval(self) -> None:
        mapping = self._mapping()
        mapping["attribute_map"].pop("workflow_run_id")
        mapping["trace_as_workflow_run_approved"] = False
        with tempfile.TemporaryDirectory() as temp:
            mapping_path = Path(temp) / "mapping.json"
            mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "workflow-run key"):
                prepare_opentelemetry(
                    SAMPLES / "logs.otlp.json",
                    mapping_path,
                    SAMPLES / "manifest.monitoring.json",
                    Path(temp) / "prepared",
                )

    def test_unmapped_event_name_is_rejected_without_outputs(self) -> None:
        payload = json.loads((SAMPLES / "logs.otlp.json").read_text(encoding="utf-8"))
        payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0][
            "eventName"
        ] = "workflow.unmapped"
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "logs.json"
            output = Path(temp) / "prepared"
            source.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(DataValidationError, "no approved mapping"):
                prepare_opentelemetry(
                    source,
                    SAMPLES / "opentelemetry_mapping.approved.json",
                    SAMPLES / "manifest.monitoring.json",
                    output,
                )
            self.assertFalse(output.exists())

    def test_trace_id_fallback_and_generated_event_ids_are_supported_when_approved(self) -> None:
        mapping = self._mapping()
        mapping["attribute_map"].pop("workflow_run_id")  # type: ignore[index,union-attr]
        mapping["attribute_map"].pop("event_id")  # type: ignore[index,union-attr]
        mapping["trace_as_workflow_run_approved"] = True
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "prepared"
            mapping_path = Path(temp) / "mapping.json"
            mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
            prepare_opentelemetry(
                SAMPLES / "logs.otlp.json",
                mapping_path,
                SAMPLES / "manifest.monitoring.json",
                output,
            )
            events = [
                json.loads(line)
                for line in (output / "canonical_events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertTrue(all(event["event_id"].startswith("otel:") for event in events))
            self.assertEqual(
                {event["workflow_run_id"] for event in events},
                {"5b8efff798038103d269b633813fc60c"},
            )

    def test_mapping_contract_rejects_invalid_shapes(self) -> None:
        base = self._mapping()
        cases: list[tuple[str, object, str]] = [
            ("not_object", [], "JSON object"),
            ("missing_identity", {**base, "mapping_id": ""}, "mapping_id"),
            ("attribute_map_type", {**base, "attribute_map": []}, "attribute_map"),
            (
                "unknown_canonical_field",
                {**base, "attribute_map": {"not_a_field": "source.value"}},
                "Unsupported canonical fields",
            ),
            (
                "empty_attribute_name",
                {**base, "attribute_map": {"workflow_run_id": ""}},
                "attribute_map.workflow_run_id",
            ),
            ("event_map_empty", {**base, "event_name_map": {}}, "event_name_map"),
            (
                "event_type_unknown",
                {**base, "event_name_map": {"workflow.started": "not_supported"}},
                "Unsupported normalized event type",
            ),
            (
                "boundary_missing",
                {
                    **base,
                    "event_name_map": {
                        "workflow.started": "run_started",
                        "workflow.completed": "run_completed",
                    },
                },
                "run_failed",
            ),
            ("status_map_empty", {**base, "status_map": {}}, "status_map"),
            (
                "normalized_status_unknown",
                {**base, "status_map": {"unknown": ["value"]}},
                "Unsupported normalized status",
            ),
            (
                "source_status_empty",
                {**base, "status_map": {"completed": []}},
                "non-empty list",
            ),
            (
                "source_status_invalid",
                {**base, "status_map": {"completed": [1]}},
                "invalid value",
            ),
            (
                "source_status_duplicate",
                {**base, "status_map": {"completed": ["same"], "failed": ["same"]}},
                "Duplicate OpenTelemetry status",
            ),
            (
                "approval_type",
                {**base, "mapping_approval": "approved"},
                "mapping_approval",
            ),
            (
                "approval_status",
                {**base, "mapping_approval": {"status": "invalid"}},
                "status",
            ),
            (
                "approval_evidence",
                {**base, "mapping_approval": {"status": "approved"}},
                "reviewer_role",
            ),
            (
                "trace_fallback_type",
                {**base, "trace_as_workflow_run_approved": "yes"},
                "must be boolean",
            ),
        ]
        for name, mapping, message in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ConfigurationError, message):
                    self._prepare_with_mapping(mapping, Path(temp))

    def test_otlp_contract_rejects_invalid_envelopes_and_attributes(self) -> None:
        base = json.loads((SAMPLES / "logs.otlp.json").read_text(encoding="utf-8"))
        cases: list[tuple[str, object, str]] = [
            ("not_export", {}, "ExportLogsServiceRequest"),
            ("resource_entry", {"resourceLogs": [1]}, "resourceLogs"),
            (
                "scope_logs_type",
                {"resourceLogs": [{"resource": {}, "scopeLogs": {}}]},
                "scopeLogs",
            ),
            (
                "scope_entry",
                {"resourceLogs": [{"resource": {}, "scopeLogs": [1]}]},
                "scopeLogs",
            ),
        ]
        bad_attributes = json.loads(json.dumps(base))
        bad_attributes["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"] = {}
        cases.append(("attributes_type", bad_attributes, "attributes must be a list"))
        bad_entry = json.loads(json.dumps(base))
        bad_entry["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"] = [
            {"value": {"stringValue": "missing-key"}}
        ]
        cases.append(("attribute_entry", bad_entry, "require a string key"))
        duplicate = json.loads(json.dumps(base))
        attributes = duplicate["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"]
        attributes.append(json.loads(json.dumps(attributes[0])))
        cases.append(("duplicate_attribute", duplicate, "Duplicate OTLP attribute"))
        bad_any_value = json.loads(json.dumps(base))
        bad_any_value["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"][0][
            "value"
        ] = {}
        cases.append(("any_value_shape", bad_any_value, "exactly one value field"))
        bad_integer = json.loads(json.dumps(base))
        bad_integer["resourceLogs"][0]["scopeLogs"][0]["logRecords"][1]["attributes"][7][
            "value"
        ] = {"intValue": "not-an-integer"}
        cases.append(("integer_value", bad_integer, "Invalid OTLP integer"))

        for name, payload, message in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as temp:
                source = Path(temp) / "logs.json"
                source.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(DataValidationError, message):
                    prepare_opentelemetry(
                        source,
                        SAMPLES / "opentelemetry_mapping.approved.json",
                        SAMPLES / "manifest.monitoring.json",
                        Path(temp) / "prepared",
                    )


if __name__ == "__main__":
    unittest.main()
