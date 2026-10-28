from __future__ import annotations

import unittest

from agent_eval_toolkit.exceptions import ConfigurationError, DataValidationError
from agent_eval_toolkit.normalize import normalize_logs
from agent_eval_toolkit.reconstruct import reconstruct_runs
from agent_eval_toolkit.validation import (
    coerce_optional_float,
    coerce_optional_int,
    normalize_timestamp,
    validate_baseline_context,
    validate_canonical_rows,
    validate_configuration_drift,
    validate_evaluation_manifest,
    validate_telemetry_requirements,
    validate_workflow_config,
)
from tests.helpers import (
    ROOT,
    sample_evaluation_manifest,
    sample_raw_rows,
    sample_workflow_config,
)


class ValidationTest(unittest.TestCase):
    def test_validate_workflow_config_accepts_sample_config(self) -> None:
        config = _sample_workflow_config()
        self.assertEqual(validate_workflow_config(config), config)

    def test_validate_workflow_config_rejects_unsupported_event_type(self) -> None:
        config = _sample_workflow_config()
        config["event_type_map"]["workflow_started"] = "made_up_event_type"

        with self.assertRaises(ConfigurationError):
            validate_workflow_config(config)

    def test_validate_workflow_config_rejects_unprotected_restricted_fields(self) -> None:
        config = _sample_workflow_config()
        config["restricted_fields"] = ["ticket_text"]
        config["redact_raw_fields"] = []

        with self.assertRaises(ConfigurationError):
            validate_workflow_config(config)

    def test_validate_workflow_config_requires_explicit_boundaries(self) -> None:
        config = _sample_workflow_config()
        config.pop("run_boundaries", None)

        with self.assertRaisesRegex(ConfigurationError, "run_boundaries"):
            validate_workflow_config(config)

    def test_validate_workflow_config_rejects_unknown_capability(self) -> None:
        config = _sample_workflow_config()
        config["telemetry_capabilities"] = ["made_up_signal"]

        with self.assertRaisesRegex(ConfigurationError, "Unsupported telemetry capabilities"):
            validate_workflow_config(config)

    def test_validate_workflow_config_requires_increment_aggregation_semantics(self) -> None:
        config = _sample_workflow_config()
        config.pop("aggregation_semantics")

        with self.assertRaisesRegex(ConfigurationError, "aggregation_semantics"):
            validate_workflow_config(config)

        config = _sample_workflow_config()
        config["aggregation_semantics"]["observed_cost_usd"] = "cumulative_snapshot"
        with self.assertRaisesRegex(ConfigurationError, "cumulative snapshots"):
            validate_workflow_config(config)

    def test_validate_workflow_config_rejects_restricted_semantic_mapping(self) -> None:
        config = _sample_workflow_config()
        config["restricted_fields"] = ["input_tokens"]
        config["redact_raw_fields"] = ["input_tokens"]

        with self.assertRaisesRegex(ConfigurationError, "canonical semantic fields"):
            validate_workflow_config(config)

    def test_validate_workflow_config_rejects_template_placeholders(self) -> None:
        config = _sample_workflow_config()
        config["workflow_family"] = "TODO_WORKFLOW_FAMILY"

        with self.assertRaisesRegex(ConfigurationError, "Unresolved template"):
            validate_workflow_config(config)

    def test_validate_baseline_context_normalizes_numeric_values(self) -> None:
        baseline = validate_baseline_context(
            {
                "manual_baseline_duration_seconds": "420",
                "loaded_labor_rate_usd_per_hour": 68,
                "sensitivity": {"manual_baseline_duration_seconds": {"low": "360", "high": 480}},
            }
        )

        self.assertEqual(baseline["manual_baseline_duration_seconds"], 420.0)
        self.assertEqual(baseline["loaded_labor_rate_usd_per_hour"], 68.0)
        self.assertEqual(
            baseline["sensitivity"]["manual_baseline_duration_seconds"]["low"],
            360.0,
        )

    def test_validate_evaluation_manifest_accepts_sample_manifest(self) -> None:
        manifest = validate_evaluation_manifest(sample_evaluation_manifest())

        self.assertEqual(manifest["evaluation_id"], "support-ticket-resolution-pilot-2026-03")
        self.assertEqual(manifest["measurement_window"]["start"], "2026-03-20T00:00:00Z")
        self.assertEqual(manifest["publication_policy"]["minimum_evidence_tier"], "complete")
        self.assertTrue(manifest["publication_policy"]["legacy_default_applied"])

    def test_manifest_evidence_context_and_publication_policy_are_validated(self) -> None:
        manifest = sample_evaluation_manifest()
        manifest["evidence_context"] = {
            "source_kind": "enterprise_operational",
            "mapping_assessment_required": True,
        }
        manifest["publication_policy"] = {"minimum_evidence_tier": "partial"}

        validated = validate_evaluation_manifest(manifest)

        self.assertEqual(validated["evidence_context"]["source_kind"], "enterprise_operational")
        self.assertTrue(validated["evidence_context"]["mapping_assessment_required"])
        self.assertEqual(validated["publication_policy"]["minimum_evidence_tier"], "partial")
        self.assertFalse(validated["publication_policy"]["legacy_default_applied"])

        manifest["publication_policy"]["minimum_evidence_tier"] = "confidence_95"
        with self.assertRaises(ConfigurationError):
            validate_evaluation_manifest(manifest)

    def test_validate_evaluation_manifest_rejects_missing_required_fields(self) -> None:
        manifest = sample_evaluation_manifest()
        del manifest["telemetry_requirements"]

        with self.assertRaises(ConfigurationError):
            validate_evaluation_manifest(manifest)

    def test_validate_evaluation_manifest_rejects_missing_taxonomy_fields(self) -> None:
        manifest = sample_evaluation_manifest()
        del manifest["taxonomy"]["deployment_mode"]

        with self.assertRaises(ConfigurationError):
            validate_evaluation_manifest(manifest)

    def test_validate_evaluation_manifest_rejects_reversed_window(self) -> None:
        manifest = sample_evaluation_manifest()
        manifest["measurement_window"]["end"] = "2026-03-19T00:00:00Z"

        with self.assertRaisesRegex(ConfigurationError, "must be later"):
            validate_evaluation_manifest(manifest)

    def test_validate_evaluation_manifest_rejects_unknown_telemetry(self) -> None:
        manifest = sample_evaluation_manifest()
        manifest["telemetry_requirements"]["required"].append("made_up_signal")

        with self.assertRaisesRegex(ConfigurationError, "Unsupported telemetry requirements"):
            validate_evaluation_manifest(manifest)

    def test_validate_evaluation_manifest_normalizes_structured_exclusions(self) -> None:
        manifest = sample_evaluation_manifest()
        manifest["run_exclusions"] = [{"run_id": "run-002", "reason": "Known test traffic"}]

        normalized = validate_evaluation_manifest(manifest)

        self.assertEqual(
            normalized["run_exclusions"],
            [{"run_id": "run-002", "reason": "Known test traffic"}],
        )

    def test_validate_evaluation_manifest_normalizes_quality_and_expected_config_gates(
        self,
    ) -> None:
        manifest = sample_evaluation_manifest()
        manifest["quality_gate"] = {"approved": True, "threshold": 0.85}

        normalized = validate_evaluation_manifest(manifest)

        self.assertEqual(normalized["quality_gate"]["minimum_score"], 0.85)
        self.assertNotIn("threshold", normalized["quality_gate"])
        self.assertTrue(normalized["expected_configurations"][0]["required"])

    def test_validate_evaluation_manifest_rejects_unapproved_or_unbounded_quality_gate(
        self,
    ) -> None:
        manifest = sample_evaluation_manifest()
        manifest["quality_gate"] = {"approved": "yes", "minimum_score": 0.8}
        with self.assertRaisesRegex(ConfigurationError, "quality_gate.approved"):
            validate_evaluation_manifest(manifest)

        manifest["quality_gate"] = {"approved": True, "minimum_score": 1.1}
        with self.assertRaisesRegex(ConfigurationError, "between zero and one"):
            validate_evaluation_manifest(manifest)

    def test_validate_canonical_rows_reports_missing_required_shape(self) -> None:
        result = validate_canonical_rows(
            [(1, {"event_id": "evt-1", "event_name": "workflow_started"})],
            _sample_workflow_config(),
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["missing_by_signal"]["timestamp"], 1)

    def test_validate_canonical_rows_rejects_duplicates_unknown_types_and_bad_values(self) -> None:
        config = _sample_workflow_config()
        rows = [
            (
                1,
                {
                    "event_id": "duplicate",
                    "timestamp": "not-a-timestamp",
                    "event_name": "unknown_raw_type",
                    "workflow_run_id": "run-1",
                    "trace_id": "trace-1",
                    "step_name": "step",
                    "status": "in_progress",
                    "input_tokens": -1,
                    "observed_cost_usd": -0.01,
                },
            ),
            (
                2,
                {
                    "event_id": "duplicate",
                    "timestamp": "2026-03-20T09:00:00Z",
                    "event_name": "workflow_started",
                    "workflow_run_id": "run-1",
                    "trace_id": "trace-1",
                    "step_name": "step",
                    "status": "in_progress",
                },
            ),
        ]

        result = validate_canonical_rows(rows, config)

        self.assertFalse(result["passed"])
        self.assertEqual(result["invalid_by_signal"]["timestamp"], 1)
        self.assertEqual(result["invalid_by_signal"]["event_type"], 1)
        self.assertEqual(result["invalid_by_signal"]["input_tokens"], 1)
        self.assertEqual(result["invalid_by_signal"]["observed_cost_usd"], 1)
        self.assertEqual(result["duplicate_event_ids"][0]["event_id"], "duplicate")

    def test_validate_canonical_rows_rejects_coerced_identifier_and_semantic_types(self) -> None:
        config = _sample_workflow_config()
        rows = [
            (
                1,
                {
                    "event_id": 123,
                    "timestamp": "2026-03-20T09:00:00Z",
                    "event_name": "workflow_started",
                    "workflow_run_id": 456,
                    "trace_id": "trace-1",
                    "step_name": "intake",
                    "status": True,
                    "attempt": "1",
                    "human_intervention": "false",
                    "business_success": 1,
                },
            )
        ]

        result = validate_canonical_rows(rows, config)

        self.assertFalse(result["passed"])
        for signal in (
            "event_id",
            "run_id",
            "status",
            "attempt_index",
            "intervention_markers",
            "business_success",
        ):
            self.assertEqual(result["invalid_by_signal"][signal], 1)

    def test_validate_telemetry_requirements_reports_required_coverage(self) -> None:
        workflow_config = _sample_workflow_config()
        events = normalize_logs(
            sample_raw_rows(),
            workflow_config,
            str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
        )
        runs = reconstruct_runs(events)

        result = validate_telemetry_requirements(
            runs, sample_evaluation_manifest(), workflow_config
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["run_count"], 3)
        self.assertEqual(result["missing_required_summary"], {})
        self.assertEqual(result["coverage_by_signal"]["input_tokens"]["run_denominator"], 3)
        self.assertEqual(result["coverage_by_signal"]["input_tokens"]["coverage"], 1.0)

    def test_validate_telemetry_requirements_fails_with_zero_runs(self) -> None:
        result = validate_telemetry_requirements(
            [], sample_evaluation_manifest(), _sample_workflow_config()
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["run_count"], 0)
        self.assertEqual(result["missing_required_summary"]["workflow_runs"], 1)

    def test_normalize_timestamp_rejects_invalid_values(self) -> None:
        with self.assertRaises(DataValidationError):
            normalize_timestamp("not-a-timestamp")

    def test_coercion_helpers_raise_for_invalid_values(self) -> None:
        with self.assertRaises(DataValidationError):
            coerce_optional_int("abc", "attempt", "evt-1")

        with self.assertRaises(DataValidationError):
            coerce_optional_float("usd", "cost", "evt-1")

        with self.assertRaises(DataValidationError):
            coerce_optional_int(-1, "input_tokens", "evt-1")

        with self.assertRaises(DataValidationError):
            coerce_optional_float(-0.1, "cost", "evt-1")

    def test_configuration_drift_is_blocked_for_comparative_design(self) -> None:
        workflow_config = _sample_workflow_config()
        events = normalize_logs(
            sample_raw_rows(),
            workflow_config,
            str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
        )
        runs = reconstruct_runs(events)
        runs[0].configuration_hashes.append("sha256:different")
        manifest = sample_evaluation_manifest()
        manifest["evaluation_design"] = "ab_test"

        result = validate_configuration_drift(runs, manifest)

        self.assertFalse(result["passed"])
        self.assertEqual(result["invalid_runs"][0]["run_id"], "fallback-run-003")
        self.assertIn("mixed_configuration_hash", result["invalid_runs"][0]["issues"])

    def test_expected_configuration_matrix_rejects_unexpected_and_missing_groups(self) -> None:
        workflow_config = _sample_workflow_config()
        events = normalize_logs(sample_raw_rows(), workflow_config, "pilot_logs.jsonl")
        runs = reconstruct_runs(events, workflow_config)
        manifest = sample_evaluation_manifest()
        manifest["expected_configurations"].append(
            {
                "group": "required_treatment",
                "agent_configuration_id": "treatment.v1",
                "agent_version": "2026-03-15",
                "configuration_hash": "sha256:treatment-v1",
                "required": True,
            }
        )
        runs[0].configuration_hashes = ["sha256:unexpected"]

        result = validate_configuration_drift(runs, manifest)

        self.assertFalse(result["passed"])
        self.assertIn("unexpected_configuration_identity", result["invalid_runs"][0]["issues"])
        self.assertEqual(
            result["missing_expected_configurations"][0]["group"],
            "required_treatment",
        )

    def test_manifest_configuration_is_fallback_identity_for_monitoring(self) -> None:
        workflow_config = _sample_workflow_config()
        events = normalize_logs(sample_raw_rows(), workflow_config, "pilot_logs.jsonl")
        runs = reconstruct_runs(events, workflow_config)
        manifest = sample_evaluation_manifest()
        manifest["expected_configurations"] = []
        runs[0].configuration_hashes = ["sha256:unexpected"]

        result = validate_configuration_drift(runs, manifest)

        self.assertFalse(result["passed"])
        self.assertIn("unexpected_configuration_identity", result["invalid_runs"][0]["issues"])
        self.assertEqual(
            result["expected_configurations"][0]["group"],
            "manifest_configuration",
        )

    def test_langfuse_onboarding_requires_recorded_trace_approval(self) -> None:
        manifest = sample_evaluation_manifest()
        manifest["onboarding"] = {
            "source": "offline_langfuse_export",
            "status_mapping_approved": True,
            "trace_as_workflow_run_approved": False,
            "enterprise_evaluation_gate": "pending_manual_trace_review",
            "trace_audit_file": "trace_audit_sample.csv",
        }

        with self.assertRaises(ConfigurationError):
            validate_evaluation_manifest(manifest)

        manifest["onboarding"].update(
            {
                "trace_as_workflow_run_approved": True,
                "enterprise_evaluation_gate": "approved",
                "reviewer": "approved-data-owner",
                "review_evidence": "trace_audit_sample.csv reviewed in full",
            }
        )
        validated = validate_evaluation_manifest(manifest)
        self.assertTrue(validated["onboarding"]["trace_as_workflow_run_approved"])


def _sample_workflow_config() -> dict:
    config = sample_workflow_config()
    config.setdefault(
        "run_boundaries",
        {
            "start_event_type": "run_started",
            "completed_event_type": "run_completed",
            "failed_event_type": "run_failed",
        },
    )
    config.setdefault(
        "telemetry_capabilities",
        [
            "failure_modes",
            "handoff_targets",
            "input_tokens",
            "intervention_markers",
            "observed_cost_usd",
            "output_tokens",
            "quality_score",
            "retry_markers",
            "routing_decisions",
            "tool_call_trace",
        ],
    )
    return config


if __name__ == "__main__":
    unittest.main()
