from __future__ import annotations

import copy
import gzip
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from typing import Any

from agent_eval_toolkit.evaluation import filter_runs_by_measurement_window
from agent_eval_toolkit.io_utils import read_json, read_jsonl_with_line_numbers
from agent_eval_toolkit.langfuse import prepare_langfuse
from agent_eval_toolkit.mapping import build_mapping_assessment_payload
from agent_eval_toolkit.metrics import build_scorecard
from agent_eval_toolkit.models import parse_timestamp
from agent_eval_toolkit.normalize import normalize_logs
from agent_eval_toolkit.reconstruct import reconstruct_runs
from agent_eval_toolkit.validation import validate_telemetry_requirements

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "data/readiness_samples/enterprise_readiness_pack"
LANGFUSE = ROOT / "data/langfuse_samples"
MAPPING = ROOT / "data/mapping_samples"


def _rows(name: str) -> list[tuple[int, dict[str, Any], str]]:
    return read_jsonl_with_line_numbers(PACK / name)


def _config(name: str = "workflow_config.json") -> dict[str, Any]:
    return read_json(PACK / name)


def _manifest(name: str) -> dict[str, Any]:
    return read_json(PACK / name)


def _runs(
    rows: list[tuple[int, dict[str, Any], str]],
    config: dict[str, Any] | None = None,
) -> list[Any]:
    effective = config or _config()
    return reconstruct_runs(normalize_logs(rows, effective, "raw_logs"), effective)


def _run_metrics(run: Any) -> dict[str, Any]:
    return {
        "status": run.status,
        "duration_seconds": run.duration_seconds,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "observed_cost_usd": run.observed_cost_usd,
        "retry_count": run.retry_count,
        "intervention_count": run.intervention_count,
        "tool_call_count": run.tool_call_count,
        "handoff_count": run.handoff_count,
        "routing_decision_count": run.routing_decision_count,
        "avg_quality_score": run.avg_quality_score,
        "business_success": run.business_success,
        "human_effort_seconds": run.human_effort_seconds,
    }


def _scorecard(rows: list[tuple[int, dict[str, Any], str]], manifest_name: str) -> Any:
    config = _config()
    manifest = _manifest(manifest_name)
    runs = _runs(rows, config)
    telemetry = validate_telemetry_requirements(runs, manifest, config)
    return build_scorecard(runs, manifest, telemetry_validation=telemetry)


class MetamorphicVerificationTest(unittest.TestCase):
    def test_mapping_and_activity_row_permutation_preserves_friction_profile(self) -> None:
        profile = read_json(MAPPING / "source_profile.complete.json")
        spec = read_json(MAPPING / "mapping_spec.confirmed.json")
        manifest = read_json(MAPPING / "manifest.mapping.json")
        activities = [
            payload
            for _line, payload, _raw in read_jsonl_with_line_numbers(
                MAPPING / "mapping_activity.complete.jsonl"
            )
        ]
        assessment_a, friction_a, diagnostics_a = build_mapping_assessment_payload(
            profile, spec, manifest, activity_rows=activities
        )
        spec["mappings"] = list(reversed(spec["mappings"]))
        assessment_b, friction_b, diagnostics_b = build_mapping_assessment_payload(
            profile, spec, manifest, activity_rows=list(reversed(activities))
        )

        self.assertEqual(assessment_a, assessment_b)
        self.assertEqual(friction_a, friction_b)
        self.assertEqual(diagnostics_a, diagnostics_b)

    def test_split_mapping_activity_preserves_observed_effort(self) -> None:
        profile = read_json(MAPPING / "source_profile.complete.json")
        spec = read_json(MAPPING / "mapping_spec.confirmed.json")
        manifest = read_json(MAPPING / "manifest.mapping.json")
        activities = [
            payload
            for _line, payload, _raw in read_jsonl_with_line_numbers(
                MAPPING / "mapping_activity.complete.jsonl"
            )
        ]
        split = copy.deepcopy(activities)
        original = split.pop(0)
        split.extend(
            [
                {**original, "activity_id": "map-activity-001a", "active_effort_minutes": 4},
                {**original, "activity_id": "map-activity-001b", "active_effort_minutes": 6},
            ]
        )
        _assessment_a, friction_a, _diagnostics_a = build_mapping_assessment_payload(
            profile, spec, manifest, activity_rows=activities
        )
        _assessment_b, friction_b, _diagnostics_b = build_mapping_assessment_payload(
            profile, spec, manifest, activity_rows=split
        )

        self.assertEqual(
            friction_a["active_effort"]["observed_total_minutes"],
            friction_b["active_effort"]["observed_total_minutes"],
        )

    def test_unambiguous_row_permutation_preserves_run_metrics(self) -> None:
        original = _rows("complete_telemetry.jsonl")
        permuted = [original[2], original[0], original[1]]

        self.assertEqual(_run_metrics(_runs(original)[0]), _run_metrics(_runs(permuted)[0]))

    def test_timezone_equivalence_and_uniform_shift_preserve_duration(self) -> None:
        original = _rows("complete_telemetry.jsonl")
        equivalent = copy.deepcopy(original)
        shifted = copy.deepcopy(original)
        for _line, payload, _raw in equivalent:
            timestamp = parse_timestamp(payload["timestamp"])
            payload["timestamp"] = timestamp.astimezone().isoformat()
        for _line, payload, _raw in shifted:
            timestamp = parse_timestamp(payload["timestamp"]) + timedelta(days=17)
            payload["timestamp"] = timestamp.isoformat()

        expected = _runs(original)[0].duration_seconds
        self.assertEqual(_runs(equivalent)[0].duration_seconds, expected)
        self.assertEqual(_runs(shifted)[0].duration_seconds, expected)

    def test_split_token_and_cost_increments_preserve_totals(self) -> None:
        base = [
            (
                1,
                {
                    "event_id": "split-start",
                    "timestamp": "2026-04-01T09:00:00Z",
                    "event_name": "workflow_started",
                    "workflow_run_id": "split-run",
                    "trace_id": "split-trace",
                    "step_name": "start",
                    "status": "in_progress",
                    "agent_config_id": "pilot-agent.v1",
                    "agent_version": "2026-04-01",
                    "config_hash": "sha256:pilot-agent-v1",
                },
                "",
            ),
            (
                2,
                {
                    "event_id": "split-usage",
                    "timestamp": "2026-04-01T09:00:30Z",
                    "event_name": "step_completed",
                    "workflow_run_id": "split-run",
                    "trace_id": "split-trace",
                    "step_name": "work",
                    "status": "in_progress",
                    "input_tokens": 300,
                    "output_tokens": 60,
                    "observed_cost_usd": 0.03,
                },
                "",
            ),
            (
                3,
                {
                    "event_id": "split-end",
                    "timestamp": "2026-04-01T09:01:00Z",
                    "event_name": "workflow_completed",
                    "workflow_run_id": "split-run",
                    "trace_id": "split-trace",
                    "step_name": "end",
                    "status": "completed",
                    "business_success": True,
                    "human_effort_seconds": 10,
                    "quality_score": 0.9,
                },
                "",
            ),
        ]
        split = copy.deepcopy(base)
        split[1][1].update(
            {
                "event_id": "split-usage-a",
                "input_tokens": 100,
                "output_tokens": 20,
                "observed_cost_usd": 0.01,
            }
        )
        split.insert(
            2,
            (
                4,
                {
                    **base[1][1],
                    "event_id": "split-usage-b",
                    "timestamp": "2026-04-01T09:00:45Z",
                    "input_tokens": 200,
                    "output_tokens": 40,
                    "observed_cost_usd": 0.02,
                },
                "",
            ),
        )

        left = _runs(base)[0]
        right = _runs(split)[0]
        self.assertEqual(left.input_tokens, right.input_tokens)
        self.assertEqual(left.output_tokens, right.output_tokens)
        self.assertEqual(left.observed_cost_usd, right.observed_cost_usd)

    def test_exclusion_filter_matches_direct_recomputation_on_retained_runs(self) -> None:
        config = _config()
        manifest = _manifest("manifest_window.json")
        all_runs = _runs(_rows("window_exclusions.jsonl"), config)
        retained, _excluded = filter_runs_by_measurement_window(all_runs, manifest)
        direct = [run for run in all_runs if run.run_id == "window-run-included"]

        self.assertEqual([run.run_id for run in retained], [run.run_id for run in direct])
        self.assertEqual(_run_metrics(retained[0]), _run_metrics(direct[0]))

    def test_failed_run_changes_reliability_and_cost_but_never_creates_benefit(self) -> None:
        config = _config()
        manifest = _manifest("manifest_pilot.json")
        runs = _runs(_rows("pilot_telemetry.jsonl"), config)
        all_telemetry = validate_telemetry_requirements(runs, manifest, config)
        completed = [run for run in runs if run.status == "completed"]
        completed_telemetry = validate_telemetry_requirements(completed, manifest, config)
        all_score = build_scorecard(runs, manifest, telemetry_validation=all_telemetry)
        completed_score = build_scorecard(
            completed, manifest, telemetry_validation=completed_telemetry
        )
        failed = next(item for item in all_score.runs if item.run_id == "pilot-run-003")

        self.assertGreater(
            all_score.observed_summary["failure_rate"],
            completed_score.observed_summary["failure_rate"],
        )
        self.assertGreater(
            all_score.observed_summary["total_observed_cost_usd"],
            completed_score.observed_summary["total_observed_cost_usd"],
        )
        self.assertFalse(failed.assumption_based_estimates["benefit_eligible"])
        self.assertEqual(
            failed.assumption_based_estimates["values"]["estimated_labor_value_delta_usd"],
            0.0,
        )
        self.assertLess(
            failed.assumption_based_estimates["values"]["estimated_net_value_delta_usd"],
            0.0,
        )

    def test_missing_capability_propagates_to_null_even_when_raw_values_exist(self) -> None:
        config = _config()
        config["telemetry_capabilities"] = [
            item
            for item in config["telemetry_capabilities"]
            if item not in {"input_tokens", "observed_cost_usd", "retry_markers"}
        ]
        run = _runs(_rows("complete_telemetry.jsonl"), config)[0]

        self.assertIsNone(run.input_tokens)
        self.assertIsNone(run.observed_cost_usd)
        self.assertIsNone(run.retry_count)

    def test_comparator_reversal_changes_delta_signs(self) -> None:
        config = _config()
        rows = _rows("comparator_telemetry.jsonl")
        runs = _runs(rows, config)
        forward_manifest = _manifest("manifest_comparator.json")
        reverse_manifest = copy.deepcopy(forward_manifest)
        reverse_manifest["baseline"], reverse_manifest["comparator"] = (
            reverse_manifest["comparator"],
            reverse_manifest["baseline"],
        )
        telemetry = validate_telemetry_requirements(runs, forward_manifest, config)
        forward = build_scorecard(
            runs, forward_manifest, telemetry_validation=telemetry
        ).comparator_summary["deltas"]["values"]
        reverse = build_scorecard(
            runs, reverse_manifest, telemetry_validation=telemetry
        ).comparator_summary["deltas"]["values"]

        for metric in (
            "avg_duration_seconds_delta",
            "avg_quality_score_delta",
            "avg_input_tokens_delta",
            "avg_output_tokens_delta",
        ):
            self.assertEqual(forward[metric], -reverse[metric])

    def test_langfuse_casing_and_container_formats_are_semantically_equivalent(self) -> None:
        mapping = LANGFUSE / "langfuse_mapping.approved.json"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources = [
                LANGFUSE / "observations.camel.json",
                LANGFUSE / "observations.snake.json",
            ]
            camel_payload = json.loads(sources[0].read_text(encoding="utf-8"))
            gzip_path = root / "observations.json.gz"
            with gzip.open(gzip_path, "wt", encoding="utf-8") as handle:
                json.dump(camel_payload, handle)
            jsonl_gzip_path = root / "observations.jsonl.gz"
            with gzip.open(jsonl_gzip_path, "wt", encoding="utf-8") as handle:
                for observation in camel_payload:
                    handle.write(json.dumps(observation, sort_keys=True) + "\n")
            sources.extend((gzip_path, jsonl_gzip_path))
            semantic: list[list[dict[str, Any]]] = []
            for index, source in enumerate(sources):
                output = root / f"prepared-{index}"
                prepare_langfuse(source, mapping, output)
                events = [
                    json.loads(line)
                    for line in (output / "canonical_events.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ]
                for event in events:
                    for field in ("source_name", "source_line", "source_observation_hash"):
                        event.pop(field, None)
                semantic.append(events)

            self.assertEqual(semantic[0], semantic[1])
            self.assertEqual(semantic[0], semantic[2])
            self.assertEqual(semantic[0], semantic[3])

    def test_irrelevant_metadata_does_not_change_analytical_results(self) -> None:
        original = _rows("complete_telemetry.jsonl")
        augmented = copy.deepcopy(original)
        for _line, payload, _raw in augmented:
            payload["irrelevant_unmapped_metadata"] = {
                "label": "ignored",
                "number": 17,
            }

        original_score = _scorecard(original, "manifest_complete.json")
        augmented_score = _scorecard(augmented, "manifest_complete.json")
        self.assertEqual(original_score.observed_summary, augmented_score.observed_summary)
        self.assertEqual(
            original_score.assumption_based_summary,
            augmented_score.assumption_based_summary,
        )


if __name__ == "__main__":
    unittest.main()
