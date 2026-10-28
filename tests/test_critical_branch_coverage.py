from __future__ import annotations

import math
import unittest
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

from agent_eval_toolkit.exceptions import ConfigurationError, DataValidationError
from agent_eval_toolkit.metrics import (
    _benefit_eligibility,
    _build_comparator_deltas,
    _build_sensitivity_summary,
    _claim_assessment,
    _config_metric_summary,
    _percentile,
    _run_estimate,
    build_scorecard,
)
from agent_eval_toolkit.models import EvidenceRef, NormalizedEvent, to_dict
from agent_eval_toolkit.normalize import _first_present, normalize_logs
from agent_eval_toolkit.reconstruct import (
    _aggregation_semantics,
    _count_retries,
    _telemetry_capabilities,
    _validate_run_boundaries,
    reconstruct_runs,
)
from agent_eval_toolkit.validation import (
    _declared_telemetry_capabilities,
    _run_has_signal,
    _validate_aggregation_semantics,
    _validate_expected_configurations,
    _validate_onboarding,
    _validate_run_exclusions,
    _validate_sensitivity,
    coerce_optional_float,
    coerce_optional_quality_score,
    coerce_optional_string,
    normalize_timestamp,
    validate_baseline_context,
    validate_canonical_rows,
    validate_configuration_drift,
    validate_evaluation_manifest,
    validate_workflow_config,
)
from tests.helpers import (
    sample_baseline_context,
    sample_evaluation_manifest,
    sample_raw_rows,
    sample_workflow_config,
)


def _normalized_events() -> list[NormalizedEvent]:
    return normalize_logs(sample_raw_rows(), sample_workflow_config(), "pilot_logs.jsonl")


def _workflow_runs():
    return reconstruct_runs(_normalized_events(), sample_workflow_config())


def _event(
    event_id: str,
    event_type: str,
    timestamp: str | None,
    *,
    source_line: int,
    status: str | None = None,
    step_name: str | None = None,
    attempt_index: int | None = None,
) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=event_id,
        workflow_family="coverage_workflow",
        workflow_definition_id="coverage-workflow.v1",
        timestamp=timestamp,
        run_id="coverage-run",
        trace_id="coverage-trace",
        event_type=event_type,
        status=status,
        step_name=step_name,
        actor_type="agent",
        attempt_index=attempt_index,
        human_intervention=False,
        observed_cost_usd=None,
        input_tokens=None,
        output_tokens=None,
        evidence=EvidenceRef(
            source_file="coverage.jsonl",
            source_line=source_line,
            raw_event_id=event_id,
            raw_event_hash=f"hash-{event_id}",
            captured_fields={},
        ),
    )


class ModelDecisionCoverageTest(unittest.TestCase):
    def test_model_runtime_type_and_range_guards(self) -> None:
        events = _normalized_events()
        event = events[0]
        evidence = event.evidence
        self.assertIsNotNone(evidence)
        assert evidence is not None
        run = _workflow_runs()[0]

        self.assertEqual(to_dict(Decimal("1.25")), 1.25)

        invalid_evidence = (
            ("source_line", True, TypeError),
            ("source_line", 0, ValueError),
            ("captured_fields", [], TypeError),
            ("redacted_fields", [""], TypeError),
        )
        for field_name, value, error in invalid_evidence:
            with self.subTest(evidence_field=field_name), self.assertRaises(error):
                replace(evidence, **{field_name: value})

        invalid_events = (
            ("observed_cost_usd", True, TypeError),
            ("observed_cost_usd", math.inf, ValueError),
            ("input_tokens", True, TypeError),
            ("input_tokens", -1, ValueError),
            ("quality_score", 1.1, ValueError),
            ("event_type", "unsupported", ValueError),
            ("human_intervention", 1, TypeError),
            ("metadata", [], TypeError),
            ("missing_fields", [""], TypeError),
        )
        for field_name, value, error in invalid_events:
            with self.subTest(event_field=field_name), self.assertRaises(error):
                replace(event, **{field_name: value})

        with self.assertRaisesRegex(ValueError, "missing event timestamp"):
            replace(event, timestamp=None).timestamp_dt()

        invalid_runs = (
            ("avg_quality_score", 1.1, ValueError),
            ("step_count", True, TypeError),
            ("step_count", -1, ValueError),
            ("business_success", 1, TypeError),
            ("agent_configuration_ids", "not-a-list", TypeError),
            ("aggregation_semantics", [], TypeError),
            ("aggregation_semantics", {"input_tokens": 1}, TypeError),
        )
        for field_name, value, error in invalid_runs:
            with self.subTest(run_field=field_name), self.assertRaises(error):
                replace(run, **{field_name: value})


class NormalizeDecisionCoverageTest(unittest.TestCase):
    def test_missing_and_unknown_source_fields_are_explicit(self) -> None:
        config = sample_workflow_config()
        payload = deepcopy(sample_raw_rows()[0][1])
        payload.pop("timestamp")
        payload.pop("workflow_run_id")
        payload["event_name"] = "unmapped-event"

        event = normalize_logs([(1, payload)], config, "coverage.jsonl")[0]

        self.assertIsNone(event.timestamp)
        self.assertIsNone(event.run_id)
        self.assertEqual(event.event_type, "unknown")
        self.assertIn("timestamp", event.missing_fields)
        self.assertIn("run_id", event.missing_fields)
        self.assertIn("unknown_event_type_mapping", event.missing_fields)
        self.assertIsNone(_first_present({}, ["one", "two"]))

        missing_type = deepcopy(sample_raw_rows()[0][1])
        missing_type.pop("event_name")
        missing_event = normalize_logs([(2, missing_type)], config, "coverage.jsonl")[0]
        self.assertIn("event_name", missing_event.missing_fields)

        missing_step = deepcopy(sample_raw_rows()[2][1])
        missing_step.pop("step_name")
        step_event = normalize_logs([(3, missing_step)], config, "coverage.jsonl")[0]
        self.assertIn("step_name", step_event.missing_fields)

    def test_non_object_row_and_defensive_event_id_fallback(self) -> None:
        with self.assertRaisesRegex(DataValidationError, "must be a JSON object"):
            normalize_logs([(1, [])], sample_workflow_config(), "coverage.jsonl")

        payload = deepcopy(sample_raw_rows()[0][1])
        original = coerce_optional_string

        def return_none_for_event_id(value, field_name, event_id):
            if field_name == "event_id":
                return None
            return original(value, field_name, event_id)

        with patch(
            "agent_eval_toolkit.normalize.coerce_optional_string",
            side_effect=return_none_for_event_id,
        ):
            event = normalize_logs([(9, payload)], sample_workflow_config(), "coverage.jsonl")[0]
        self.assertEqual(event.event_id, "line-9")


class ReconstructionDecisionCoverageTest(unittest.TestCase):
    def test_reconstruction_rejects_invalid_identity_and_type(self) -> None:
        self.assertEqual(reconstruct_runs([]), [])
        event = _normalized_events()[0]
        with self.assertRaisesRegex(DataValidationError, "missing a run ID"):
            reconstruct_runs([replace(event, run_id=None)])
        with self.assertRaisesRegex(DataValidationError, "unknown event type"):
            reconstruct_runs([replace(event, event_type="unknown")])

    def test_capability_forms_drift_conflict_and_semantics(self) -> None:
        events = _normalized_events()
        config = sample_workflow_config()
        config["telemetry_capabilities"] = {name: True for name in config["telemetry_capabilities"]}
        runs = reconstruct_runs(events, config)
        self.assertTrue(runs)
        self.assertEqual(
            _telemetry_capabilities({"telemetry_capabilities": "invalid"}, events), set()
        )

        drifted = list(events)
        drift_index = next(
            index
            for index, event in enumerate(drifted)
            if event.run_id == "run-001" and event.event_type != "run_started"
        )
        drifted[drift_index] = replace(drifted[drift_index], configuration_hash="sha256:different")
        self.assertIn(
            "configuration_drift",
            next(
                run
                for run in reconstruct_runs(drifted, sample_workflow_config())
                if run.run_id == "run-001"
            ).missing_telemetry,
        )

        run_one = [event for event in events if event.run_id == "run-001"]
        conflicted = list(run_one)
        conflicted[0] = replace(conflicted[0], business_success=False)
        conflicted[-1] = replace(conflicted[-1], business_success=True)
        conflict_run = reconstruct_runs(conflicted, sample_workflow_config())[0]
        self.assertIn("business_success_conflict", conflict_run.missing_telemetry)

        invalid_semantics = sample_workflow_config()
        invalid_semantics["aggregation_semantics"] = []
        with self.assertRaisesRegex(DataValidationError, "must be an object"):
            _aggregation_semantics(invalid_semantics, {"input_tokens"})

    def test_boundary_and_retry_defensive_paths(self) -> None:
        start = _event("start", "run_started", "2026-01-01T00:00:00Z", source_line=1)
        terminal = _event(
            "terminal",
            "run_completed",
            "2026-01-01T00:01:00Z",
            source_line=3,
            status="completed",
        )
        middle = _event("middle", "step_completed", "2026-01-01T00:00:30Z", source_line=2)
        boundaries = {
            "start_event_type": "run_started",
            "completed_event_type": "run_completed",
            "failed_event_type": "run_failed",
        }

        with self.assertRaisesRegex(DataValidationError, "duplicate terminal"):
            _validate_run_boundaries(
                "coverage-run",
                [start, terminal, replace(terminal, event_id="terminal-2")],
                boundaries,
            )
        with self.assertRaisesRegex(DataValidationError, "boundary with a missing timestamp"):
            _validate_run_boundaries(
                "coverage-run", [replace(start, timestamp=None), terminal], boundaries
            )
        with self.assertRaisesRegex(DataValidationError, "missing a timestamp"):
            _validate_run_boundaries(
                "coverage-run", [start, replace(middle, timestamp=None), terminal], boundaries
            )
        with self.assertRaisesRegex(DataValidationError, "before the start boundary"):
            _validate_run_boundaries(
                "coverage-run",
                [
                    start,
                    replace(middle, timestamp="2025-12-31T23:59:59Z"),
                    terminal,
                ],
                boundaries,
            )

        with (
            patch(
                "agent_eval_toolkit.reconstruct._ordered_events",
                return_value=[middle, start, terminal],
            ),
            self.assertRaisesRegex(DataValidationError, "start boundary is not the first"),
        ):
            _validate_run_boundaries("coverage-run", [start, middle, terminal], boundaries)
        with (
            patch(
                "agent_eval_toolkit.reconstruct._ordered_events",
                return_value=[start, terminal, middle],
            ),
            self.assertRaisesRegex(DataValidationError, "terminal boundary is not the last"),
        ):
            _validate_run_boundaries("coverage-run", [start, middle, terminal], boundaries)

        retries = [
            _event(
                "retry-keyed",
                "retry",
                "2026-01-01T00:00:10Z",
                source_line=2,
                step_name="work",
                attempt_index=2,
            ),
            _event("retry-fallback", "retry", "2026-01-01T00:00:20Z", source_line=3),
        ]
        self.assertEqual(_count_retries(retries), 2)


class MetricsDecisionCoverageTest(unittest.TestCase):
    def test_claim_strength_covers_each_design_and_gate_outcome(self) -> None:
        base = sample_evaluation_manifest()
        base["evaluation_id"] = "production-evaluation"
        base["taxonomy"] = {"deployment_mode": "production"}
        base["interpretation_boundaries"] = ["Observed operational evidence."]
        base.pop("evidence_scope", None)
        base.pop("data_provenance", None)

        cases = (
            (
                "monitoring",
                {"passed": False, "configuration_consistency": {"passed": False}},
                "insufficient_evidence",
            ),
            (
                "monitoring",
                {"passed": True, "configuration_consistency": {"passed": True}},
                "descriptive",
            ),
            (
                "before_after",
                {"passed": True, "configuration_consistency": {"passed": True}},
                "associational",
            ),
            (
                "scenario_suite",
                {"passed": True, "configuration_consistency": {"passed": True}},
                "conformance",
            ),
            (
                "ab_test",
                {"passed": True, "configuration_consistency": {"passed": True}},
                "causal",
            ),
            (
                "staged_rollout",
                {"passed": True, "configuration_consistency": {"passed": True}},
                "comparative",
            ),
        )
        for design, telemetry, expected in cases:
            manifest = deepcopy(base)
            manifest["evaluation_design"] = design
            manifest["design_validity"] = {"approved": design == "ab_test"}
            with self.subTest(design=design):
                result = _claim_assessment(manifest, telemetry)
                self.assertEqual(result["attained_claim_strength"], expected)

        withheld = _claim_assessment(base, None)
        self.assertEqual(withheld["attained_claim_strength"], "not_assessed")

    def test_quality_threshold_and_run_estimate_withholding_paths(self) -> None:
        run = next(item for item in _workflow_runs() if item.run_id == "run-001")
        manifest = sample_evaluation_manifest()
        manifest["quality_gate"] = {"approved": True, "minimum_score": 0.99}
        ineligible = replace(run, business_success=None, avg_quality_score=0.9)
        self.assertEqual(
            _benefit_eligibility(ineligible, manifest)[2],
            "approved_quality_threshold_not_met",
        )

        assumptions = sample_baseline_context()
        no_duration = _run_estimate(replace(run, duration_seconds=None), manifest, assumptions)
        self.assertIn(
            "cycle_time_delta_withheld_duration_unavailable",
            no_duration["withheld_reasons"],
        )

        no_cycle_baseline = dict(assumptions)
        no_cycle_baseline.pop("manual_baseline_cycle_time_seconds", None)
        no_cycle_baseline.pop("manual_baseline_duration_seconds", None)
        no_cycle = _run_estimate(run, manifest, no_cycle_baseline)
        self.assertIn(
            "cycle_time_delta_withheld_manual_cycle_time_unavailable",
            no_cycle["withheld_reasons"],
        )

        no_effort = _run_estimate(replace(run, human_effort_seconds=None), manifest, assumptions)
        self.assertIn(
            "labor_value_withheld_agent_human_effort_unavailable",
            no_effort["withheld_reasons"],
        )
        no_rate = dict(assumptions)
        no_rate.pop("loaded_labor_rate_usd_per_hour", None)
        self.assertIn(
            "labor_value_withheld_loaded_labor_rate_unavailable",
            _run_estimate(run, manifest, no_rate)["withheld_reasons"],
        )
        assumed_effort = dict(assumptions)
        assumed_effort["agent_human_effort_seconds"] = 20
        estimate = _run_estimate(replace(run, human_effort_seconds=None), manifest, assumed_effort)
        self.assertEqual(estimate["assumptions_used"]["agent_human_effort_seconds"], 20)

    def test_comparator_and_population_withholding_paths(self) -> None:
        run = next(item for item in _workflow_runs() if item.run_id == "run-001")
        summary = _config_metric_summary([run])
        self.assertEqual(
            _build_comparator_deltas(
                {"only": summary},
                {
                    "evaluation_design": "scenario_suite",
                    "baseline": {},
                    "comparator": {},
                },
            ),
            {},
        )
        self.assertEqual(
            _build_comparator_deltas(
                {"only": summary},
                {
                    "evaluation_design": "scenario_suite",
                    "baseline": {"agent_configuration_id": "missing"},
                    "comparator": {"agent_configuration_id": "only"},
                },
            ),
            {},
        )

        baseline = deepcopy(summary)
        comparator = deepcopy(summary)
        baseline["avg_duration_seconds"] = "not-numeric"
        comparator["avg_duration_seconds"] = "not-numeric"
        baseline["avg_observed_cost_usd"] = 1.0
        comparator["avg_observed_cost_usd"] = 2.0
        delta = _build_comparator_deltas(
            {"baseline": baseline, "comparator": comparator},
            {
                "evaluation_design": "scenario_suite",
                "baseline": {"agent_configuration_id": "baseline"},
                "comparator": {"agent_configuration_id": "comparator"},
            },
        )
        self.assertEqual(delta["values"]["avg_observed_cost_usd_delta"], 1.0)

        self.assertIsNone(_percentile([], 0.5))
        self.assertEqual(
            _build_sensitivity_summary([run], [], {}),
            {},
        )
        with self.assertRaisesRegex(ValueError, "No workflow runs"):
            build_scorecard([], sample_evaluation_manifest())
        with self.assertRaisesRegex(ConfigurationError, "same workflow family"):
            build_scorecard(
                [run, replace(run, run_id="other", workflow_family="other")],
                sample_evaluation_manifest(),
            )

        manifest = sample_evaluation_manifest()
        incomplete_cycle = replace(run, duration_seconds=None)
        scorecard = build_scorecard([run, incomplete_cycle], manifest, sample_baseline_context())
        self.assertNotIn(
            "avg_estimated_cycle_time_difference",
            scorecard.assumption_based_summary["values"],
        )

        assumptions = sample_baseline_context()
        assumptions.pop("agent_human_effort_seconds", None)
        incomplete_effort = replace(run, human_effort_seconds=None)
        scorecard = build_scorecard([run, incomplete_effort], manifest, assumptions)
        self.assertNotIn(
            "avg_estimated_human_effort_saved_seconds_vs_manual_baseline",
            scorecard.assumption_based_summary["values"],
        )

        no_labor_manifest = sample_evaluation_manifest()
        no_labor_manifest["assumptions"].pop("manual_baseline_human_effort_seconds", None)
        scorecard = build_scorecard([run], no_labor_manifest)
        self.assertNotIn(
            "total_estimated_labor_value_delta_usd",
            scorecard.assumption_based_summary["values"],
        )


class ValidationDecisionCoverageTest(unittest.TestCase):
    def test_workflow_configuration_shape_capability_and_policy_guards(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "JSON object"):
            validate_workflow_config([])

        mutations = []
        config = sample_workflow_config()
        config["run_id_keys"] = "run_id"
        mutations.append(config)
        config = sample_workflow_config()
        config["run_id_keys"] = [""]
        mutations.append(config)
        config = sample_workflow_config()
        config["run_id_keys"] = []
        mutations.append(config)
        config = sample_workflow_config()
        config["run_boundaries"]["start_event_type"] = "step_started"
        mutations.append(config)
        config = sample_workflow_config()
        config["event_type_map"] = {
            key: value for key, value in config["event_type_map"].items() if value != "run_started"
        }
        mutations.append(config)
        config = sample_workflow_config()
        config["evidence_mode"] = "plaintext"
        mutations.append(config)
        for index, invalid in enumerate(mutations):
            with self.subTest(config_case=index), self.assertRaises(ConfigurationError):
                validate_workflow_config(invalid)

        for value in (["input_tokens", "input_tokens"], "invalid"):
            with self.subTest(capabilities=value), self.assertRaises(ConfigurationError):
                _declared_telemetry_capabilities({"telemetry_capabilities": value})
        with self.assertRaises(ConfigurationError):
            _declared_telemetry_capabilities({"telemetry_capabilities": {"input_tokens": "yes"}})
        with self.assertRaises(ConfigurationError):
            _declared_telemetry_capabilities({"telemetry_capabilities": {"unknown_signal": True}})
        self.assertEqual(
            _declared_telemetry_capabilities(
                {"telemetry_capabilities": {"input_tokens": True, "output_tokens": False}}
            ),
            {"input_tokens"},
        )

        config = sample_workflow_config()
        config["quality_score_field"] = None
        with self.assertRaisesRegex(ConfigurationError, "lack a source field"):
            validate_workflow_config(config)

        config = sample_workflow_config()
        for field_name in ("cost_field", "input_tokens_field", "output_tokens_field"):
            config[field_name] = None
        config.pop("aggregation_semantics")
        _validate_aggregation_semantics(config)

        config = sample_workflow_config()
        config["aggregation_semantics"]["unknown"] = "per_event_increment"
        with self.assertRaisesRegex(ConfigurationError, "unsupported signals"):
            _validate_aggregation_semantics(config)
        config = sample_workflow_config()
        config["aggregation_semantics"].pop("input_tokens")
        with self.assertRaisesRegex(ConfigurationError, "explicitly declare"):
            _validate_aggregation_semantics(config)

        config = sample_workflow_config()
        config["metadata_fields"] = ["ticket_priority", "private"]
        config["allowed_metadata_fields"] = ["ticket_priority"]
        with self.assertRaisesRegex(ConfigurationError, "outside"):
            validate_workflow_config(config)

    def test_baseline_manifest_matrix_and_onboarding_guards(self) -> None:
        with self.assertRaises(ConfigurationError):
            validate_baseline_context([])
        for value in (math.inf, -1):
            with self.subTest(baseline=value), self.assertRaises(ConfigurationError):
                validate_baseline_context({"manual_baseline_cycle_time_seconds": value})
        self.assertEqual(validate_baseline_context({"custom_note": "kept"})["custom_note"], "kept")

        sensitivity_cases = (
            {"cycle": {"low": 1}},
            {"cycle": {"low": math.inf, "high": 2}},
            {"cycle": {"low": -1, "high": 2}},
            {"cycle": {"low": 3, "high": 2}},
        )
        for value in sensitivity_cases:
            with self.subTest(sensitivity=value), self.assertRaises(ConfigurationError):
                _validate_sensitivity(value)

        with self.assertRaises(ConfigurationError):
            validate_evaluation_manifest([])
        manifest = sample_evaluation_manifest()
        manifest["evaluation_design"] = "unsupported"
        with self.assertRaisesRegex(ConfigurationError, "evaluation_design"):
            validate_evaluation_manifest(manifest)
        manifest = sample_evaluation_manifest()
        manifest["telemetry_requirements"]["optional"].append(
            manifest["telemetry_requirements"]["required"][0]
        )
        with self.assertRaisesRegex(ConfigurationError, "unique"):
            validate_evaluation_manifest(manifest)
        manifest = sample_evaluation_manifest()
        manifest["quality_gate"] = {"approved": True, "minimum_score": True}
        with self.assertRaisesRegex(ConfigurationError, "numeric threshold"):
            validate_evaluation_manifest(manifest)

        matrix = {
            "control": {
                "agent_configuration_id": "control",
                "agent_version": "1",
                "configuration_hash": "hash-control",
            }
        }
        self.assertEqual(_validate_expected_configurations(matrix)[0]["group"], "control")
        invalid_matrices = (
            "invalid",
            [
                {
                    "agent_configuration_id": "one",
                    "agent_version": "1",
                    "configuration_hash": "hash-one",
                    "extra": True,
                }
            ],
            [
                {
                    "agent_configuration_id": "one",
                    "agent_version": "1",
                    "configuration_hash": "hash-one",
                    "required": "yes",
                }
            ],
            [
                {
                    "group": "one",
                    "agent_configuration_id": "one",
                    "agent_version": "1",
                    "configuration_hash": "same",
                },
                {
                    "group": "two",
                    "agent_configuration_id": "one",
                    "agent_version": "1",
                    "configuration_hash": "same",
                },
            ],
            [
                {
                    "group": "same",
                    "agent_configuration_id": "one",
                    "agent_version": "1",
                    "configuration_hash": "one",
                },
                {
                    "group": "same",
                    "agent_configuration_id": "two",
                    "agent_version": "1",
                    "configuration_hash": "two",
                },
            ],
        )
        for value in invalid_matrices:
            with self.subTest(matrix=value), self.assertRaises(ConfigurationError):
                _validate_expected_configurations(value)

        valid_onboarding = {
            "source": "offline_langfuse_export",
            "status_mapping_approved": True,
            "trace_as_workflow_run_approved": True,
            "enterprise_evaluation_gate": "approved",
            "trace_audit_file": "audit.csv",
            "reviewer": "reviewer",
            "review_evidence": "ticket-1",
        }
        for field_name, value in (
            ("source", "other"),
            ("status_mapping_approved", False),
            ("enterprise_evaluation_gate", "pending"),
        ):
            onboarding = dict(valid_onboarding)
            onboarding[field_name] = value
            with self.subTest(onboarding=field_name), self.assertRaises(ConfigurationError):
                _validate_onboarding(onboarding)

    def test_exclusion_canonical_coercion_and_drift_guards(self) -> None:
        with self.assertRaises(ConfigurationError):
            _validate_run_exclusions("invalid")
        with self.assertRaises(ConfigurationError):
            _validate_run_exclusions([{"run_id": "one", "reason": "x", "extra": True}])
        with self.assertRaises(ConfigurationError):
            _validate_run_exclusions(
                [
                    {"run_id": "one", "reason": "x"},
                    {"run_id": "one", "reason": "y"},
                ]
            )

        config = sample_workflow_config()
        result = validate_canonical_rows([(1, [])], config)
        self.assertFalse(result["passed"])
        payload = {
            "timestamp": "2026-01-01T00:00:00Z",
            "quality_score": 1.1,
        }
        result = validate_canonical_rows([(2, payload)], config)
        self.assertFalse(result["passed"])

        run = _workflow_runs()[0]
        self.assertTrue(_run_has_signal(run, "workflow_family"))
        self.assertTrue(_run_has_signal(run, "unknown_future_signal"))
        manifest = sample_evaluation_manifest()
        manifest["expected_configurations"] = []
        missing_identity = replace(run, agent_configuration_ids=[])
        drift = validate_configuration_drift([missing_identity], manifest)
        self.assertFalse(drift["passed"])
        self.assertIn("missing_agent_configuration_id", drift["invalid_runs"][0]["issues"])

        with self.assertRaises(DataValidationError):
            normalize_timestamp(1)
        with self.assertRaises(DataValidationError):
            coerce_optional_float(math.inf, "cost", "event")
        with self.assertRaises(DataValidationError):
            coerce_optional_string("", "name", "event")
        with self.assertRaises(DataValidationError):
            coerce_optional_quality_score(1.1, "quality", "event")


if __name__ == "__main__":
    unittest.main()
