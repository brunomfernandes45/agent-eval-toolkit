from __future__ import annotations

import csv
import gzip
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agent_eval_toolkit.exceptions import ConfigurationError, DataValidationError
from agent_eval_toolkit.io_utils import read_jsonl_with_line_numbers
from agent_eval_toolkit.langfuse import GENERATED_ARTIFACTS, prepare_langfuse
from agent_eval_toolkit.normalize import normalize_logs
from agent_eval_toolkit.reconstruct import reconstruct_runs
from agent_eval_toolkit.validation import (
    validate_canonical_rows,
    validate_evaluation_manifest,
    validate_workflow_config,
)


def _mapping() -> dict[str, Any]:
    return {
        "workflow_family": "support_resolution",
        "workflow_definition_id": "support-resolution.v1",
        "metadata_paths": {
            "status": "metadata.business_status",
            "agent_configuration_id": "metadata.configuration.id",
            "agent_version": "metadata.configuration.version",
            "configuration_hash": "metadata.configuration.hash",
            "retry": "metadata.retry",
            "intervention": "metadata.intervention",
            "handoff": "metadata.handoff",
            "routing": "metadata.routing",
            "quality": "metadata.quality",
        },
        "status_mapping": {
            "approved": True,
            "completed": ["done"],
            "failed": ["failed"],
        },
    }


def _metadata(status: str | None = "done") -> dict[str, Any]:
    return {
        "business_status": status,
        "configuration": {
            "id": "support-agent.v1",
            "version": "2026-07-01",
            "hash": "sha256:configuration-v1",
        },
        "retry": False,
        "intervention": False,
        "handoff": None,
        "routing": "standard",
        "quality": 0.91,
    }


def _camel_observation(
    observation_id: str = "obs-1",
    trace_id: str = "trace-1",
    *,
    status: str | None = "done",
    secret: str = "PRIVATE CUSTOMER MESSAGE",
) -> dict[str, Any]:
    return {
        "id": observation_id,
        "traceId": trace_id,
        "startTime": "2026-07-01T10:00:00Z",
        "endTime": "2026-07-01T10:03:00Z",
        "type": "GENERATION",
        "name": "resolve-ticket",
        "traceName": "support-resolution",
        "environment": "production",
        "metadata": _metadata(status),
        "input": secret,
        "output": {"answer": "PRIVATE GENERATED ANSWER"},
        "providedModelName": "gpt-test",
        "promptVersion": 3,
        "usageDetails": {"input": 700, "output": 140, "total": 840},
        "inputUsage": 700,
        "outputUsage": 140,
        "costDetails": {"input": 0.01, "output": 0.02, "total": 0.03},
        "totalCost": 0.03,
        "toolCallNames": ["ticket_lookup"],
    }


def _snake_observation() -> dict[str, Any]:
    camel = _camel_observation()
    aliases = {
        "traceId": "trace_id",
        "startTime": "start_time",
        "endTime": "end_time",
        "traceName": "trace_name",
        "providedModelName": "provided_model_name",
        "promptVersion": "prompt_version",
        "usageDetails": "usage_details",
        "inputUsage": "input_usage",
        "outputUsage": "output_usage",
        "costDetails": "cost_details",
        "totalCost": "total_cost",
        "toolCallNames": "tool_call_names",
    }
    return {aliases.get(key, key): value for key, value in camel.items()}


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class LangfusePreparationTest(unittest.TestCase):
    def test_api_capture_is_normalized_once_and_input_output_are_masked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "api-response.json"
            mapping = root / "mapping.json"
            output = root / "prepared"
            _write_json(source, {"data": [_camel_observation()], "meta": {"cursor": None}})
            _write_json(mapping, _mapping())

            summary = prepare_langfuse(source, mapping, output)
            events = _read_events(output / "canonical_events.jsonl")
            profile = _read_json(output / "langfuse_source_profile.json")
            coverage = _read_json(output / "telemetry_coverage.json")
            redaction = _read_json(output / "redaction_report.json")

            self.assertEqual(summary["observation_count"], 1)
            self.assertEqual({path.name for path in output.iterdir()}, set(GENERATED_ARTIFACTS))
            self.assertEqual(sum(event.get("input_tokens", 0) for event in events), 700)
            self.assertEqual(sum(event.get("output_tokens", 0) for event in events), 140)
            self.assertAlmostEqual(sum(event.get("observed_cost_usd", 0) for event in events), 0.03)
            self.assertEqual(
                [
                    event["status"]
                    for event in events
                    if event["event_name"] == "workflow_completed"
                ],
                ["completed"],
            )
            self.assertEqual(profile["usage_totals"]["input_tokens"], 700)
            self.assertTrue(coverage["preparation_gate"]["passed"])
            self.assertFalse(coverage["enterprise_evaluation_gate"]["passed"])
            self.assertEqual(coverage["optional"]["retry_markers"]["coverage_percent"], 100.0)
            self.assertEqual(coverage["optional"]["handoff_targets"]["coverage_percent"], 100.0)
            self.assertEqual(redaction["raw_input_output_values_retained"], 0)
            self.assertEqual(redaction["raw_body_sha256_values_retained"], 0)
            self.assertEqual(redaction["policy"]["mode"], "omit")
            self.assertEqual(redaction["redacted_field_counts"], {"input": 1, "output": 1})

            rendered_outputs = "\n".join(
                path.read_text(encoding="utf-8") for path in output.iterdir()
            )
            self.assertNotIn("PRIVATE CUSTOMER MESSAGE", rendered_outputs)
            self.assertNotIn("PRIVATE GENERATED ANSWER", rendered_outputs)
            self.assertIn("[REDACTED]", rendered_outputs)
            raw_body_sha256 = hashlib.sha256(b"PRIVATE CUSTOMER MESSAGE").hexdigest()
            self.assertNotIn(raw_body_sha256, rendered_outputs)
            raw_observation_sha256 = hashlib.sha256(
                json.dumps(
                    _camel_observation(),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            self.assertNotIn(raw_observation_sha256, rendered_outputs)
            evidence_event = next(event for event in events if "langfuse_input_evidence" in event)
            self.assertNotIn("sha256", evidence_event["langfuse_input_evidence"])

            workflow_config = _read_json(output / "workflow_config.json")
            validate_workflow_config(workflow_config)
            rows = read_jsonl_with_line_numbers(output / "canonical_events.jsonl")
            self.assertTrue(validate_canonical_rows(rows, workflow_config)["passed"])
            normalized = normalize_logs(
                rows, workflow_config, str(output / "canonical_events.jsonl")
            )
            runs = reconstruct_runs(normalized, workflow_config)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].duration_seconds, 180.0)
            self.assertEqual(runs[0].input_tokens, 700)
            self.assertEqual(runs[0].output_tokens, 140)
            self.assertEqual(runs[0].tool_call_count, 1)
            self.assertEqual(runs[0].avg_quality_score, 0.91)

            starter_manifest = _read_json(output / "evaluation_manifest.monitoring.json")
            with self.assertRaisesRegex(ConfigurationError, "trace-as-workflow-run"):
                validate_evaluation_manifest(starter_manifest)
            starter_manifest["onboarding"].update(
                {
                    "trace_as_workflow_run_approved": True,
                    "enterprise_evaluation_gate": "approved",
                    "reviewer": "synthetic-fixture-reviewer",
                    "review_evidence": "All synthetic trace audit rows reviewed",
                }
            )
            validate_evaluation_manifest(starter_manifest)

    def test_body_hmac_requires_explicit_mode_and_environment_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "observations.json"
            _write_json(source, [_camel_observation()])
            mapping = _mapping()
            mapping["body_evidence"] = {
                "mode": "hmac_sha256",
                "hmac_key_env": "AET_TEST_BODY_HMAC_KEY",
                "key_id": "test-key-v1",
            }
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AET_TEST_BODY_HMAC_KEY", None)
                with self.assertRaisesRegex(ConfigurationError, "is not set"):
                    prepare_langfuse(source, mapping, root / "missing-key")

            with patch.dict(
                os.environ,
                {"AET_TEST_BODY_HMAC_KEY": "private-test-key"},
                clear=False,
            ):
                output = root / "prepared"
                prepare_langfuse(source, mapping, output)

            events = _read_events(output / "canonical_events.jsonl")
            evidence = next(
                event["langfuse_input_evidence"]
                for event in events
                if "langfuse_input_evidence" in event
            )
            expected = hmac.new(
                b"private-test-key", b"PRIVATE CUSTOMER MESSAGE", hashlib.sha256
            ).hexdigest()
            self.assertEqual(evidence["hmac_sha256"], expected)
            self.assertEqual(evidence["key_id"], "test-key-v1")
            self.assertNotIn("sha256", {key for key in evidence if key != "hmac_sha256"})

    def test_camel_and_snake_case_have_same_semantic_canonical_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mapping = root / "mapping.json"
            _write_json(mapping, _mapping())
            semantic_outputs = []
            for style, observation in (
                ("camel", _camel_observation()),
                ("snake", _snake_observation()),
            ):
                source = root / f"{style}.json"
                output = root / f"{style}-prepared"
                _write_json(source, [observation])
                prepare_langfuse(source, mapping, output)
                events = _read_events(output / "canonical_events.jsonl")
                for event in events:
                    event.pop("source_name", None)
                    event.pop("source_observation_hash", None)
                semantic_outputs.append(events)

            self.assertEqual(semantic_outputs[0], semantic_outputs[1])

    def test_json_jsonl_gzip_and_directory_sources_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mapping = root / "mapping.json"
            _write_json(mapping, _mapping())

            sources = {
                "array.json": json.dumps([_camel_observation()]),
                "rows.jsonl": json.dumps(_camel_observation()),
                "array.json.gz": json.dumps([_camel_observation()]),
                "rows.jsonl.gz": json.dumps(_camel_observation()),
            }
            for index, (name, content) in enumerate(sources.items()):
                source = root / name
                if name.endswith(".gz"):
                    with gzip.open(source, "wt", encoding="utf-8") as handle:
                        handle.write(content + "\n")
                else:
                    source.write_text(content + "\n", encoding="utf-8")
                output = root / f"prepared-{index}"
                result = prepare_langfuse(source, mapping, output)
                self.assertEqual(result["observation_count"], 1)

            directory = root / "bulk"
            directory.mkdir()
            _write_json(directory / "observations.json", [_camel_observation()])
            _write_json(
                directory / "scores.json",
                [{"id": "score-1", "trace_id": "trace-1", "timestamp": "2026-07-01"}],
            )
            output = root / "directory-prepared"
            result = prepare_langfuse(directory, mapping, output)
            profile = _read_json(output / "langfuse_source_profile.json")
            self.assertEqual(result["observation_count"], 1)
            self.assertEqual(profile["skipped_non_observation_row_count"], 1)

    def test_non_null_api_cursor_is_rejected_without_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "api.json"
            output = root / "prepared"
            _write_json(
                source,
                {"data": [_camel_observation()], "meta": {"cursor": "next-page"}},
            )

            with self.assertRaisesRegex(DataValidationError, "non-null"):
                prepare_langfuse(source, _mapping(), output)

            self.assertFalse(output.exists())

    def test_duplicate_observation_ids_across_directory_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources = root / "observations"
            sources.mkdir()
            _write_json(sources / "one.json", [_camel_observation()])
            _write_json(sources / "two.json", [_camel_observation()])

            with self.assertRaisesRegex(DataValidationError, "Duplicate"):
                prepare_langfuse(sources, _mapping(), root / "prepared")

    def test_missing_explicit_business_status_creates_no_terminal_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "observations.json"
            output = root / "prepared"
            _write_json(source, [_camel_observation(status=None)])

            prepare_langfuse(source, _mapping(), output)
            events = _read_events(output / "canonical_events.jsonl")
            coverage = _read_json(output / "telemetry_coverage.json")

            terminal_names = {"workflow_completed", "workflow_failed"}
            self.assertFalse(any(event["event_name"] in terminal_names for event in events))
            self.assertFalse(coverage["preparation_gate"]["passed"])
            self.assertEqual(
                coverage["preparation_gate"]["missing_required_signals"],
                ["run_boundary", "status"],
            )

    def test_more_than_thirty_traces_get_deterministic_stratified_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            observations = []
            for index in range(40):
                observation = _camel_observation(
                    observation_id=f"obs-{index:02d}", trace_id=f"trace-{index:02d}"
                )
                observation["traceName"] = f"workflow-{index % 2}"
                observation["environment"] = "production" if index % 3 else "staging"
                observations.append(observation)
            source = root / "observations.json"
            _write_json(source, observations)

            audit_contents = []
            for name in ("prepared-a", "prepared-b"):
                output = root / name
                prepare_langfuse(source, _mapping(), output)
                audit_contents.append((output / "trace_audit_sample.csv").read_text())
            rows = list(csv.DictReader(io_lines(audit_contents[0])))

            self.assertEqual(audit_contents[0], audit_contents[1])
            self.assertEqual(len(rows), 30)
            self.assertEqual(
                {row["selection_mode"] for row in rows}, {"deterministic_stratified_30"}
            )
            self.assertEqual({row["environment"] for row in rows}, {"production", "staging"})
            self.assertEqual({row["trace_name"] for row in rows}, {"workflow-0", "workflow-1"})

    def test_mapping_approval_and_overwrite_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "observations.json"
            output = root / "prepared"
            _write_json(source, [_camel_observation()])
            unapproved = _mapping()
            unapproved["status_mapping"]["approved"] = False

            with self.assertRaisesRegex(ConfigurationError, "approved"):
                prepare_langfuse(source, unapproved, output)

            prepare_langfuse(source, _mapping(), output)
            with self.assertRaisesRegex(DataValidationError, "overwrite"):
                prepare_langfuse(source, _mapping(), output)
            prepare_langfuse(source, _mapping(), output, overwrite=True)

            workflow_config = _read_json(output / "workflow_config.json")
            self.assertEqual(
                workflow_config["telemetry_capabilities"],
                [
                    "retry_markers",
                    "intervention_markers",
                    "handoff_targets",
                    "routing_decisions",
                    "tool_call_trace",
                    "input_tokens",
                    "output_tokens",
                    "observed_cost_usd",
                    "quality_score",
                ],
            )

    def test_absent_optional_counter_fields_are_not_declared_as_zero_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "observations.json"
            output = root / "prepared"
            observation = _camel_observation()
            for field_name in ("retry", "intervention", "handoff", "routing", "quality"):
                observation["metadata"].pop(field_name)
            _write_json(source, [observation])

            prepare_langfuse(source, _mapping(), output)
            workflow_config = _read_json(output / "workflow_config.json")
            self.assertEqual(
                workflow_config["telemetry_capabilities"],
                [
                    "tool_call_trace",
                    "input_tokens",
                    "output_tokens",
                    "observed_cost_usd",
                ],
            )

            rows = read_jsonl_with_line_numbers(output / "canonical_events.jsonl")
            normalized = normalize_logs(
                rows, workflow_config, str(output / "canonical_events.jsonl")
            )
            run = reconstruct_runs(normalized, workflow_config)[0]
            self.assertIsNone(run.retry_count)
            self.assertIsNone(run.intervention_count)
            self.assertIsNone(run.handoff_count)
            self.assertIsNone(run.routing_decision_count)
            self.assertIsNone(run.avg_quality_score)


def io_lines(value: str) -> list[str]:
    return value.splitlines(keepends=True)


if __name__ == "__main__":
    unittest.main()
