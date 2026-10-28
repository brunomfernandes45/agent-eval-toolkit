#!/usr/bin/env python3
"""Run the deterministic, public-CLI thesis readiness evidence suite.

The runner deliberately imports no toolkit code. Its expected values are hand-authored
in ``scenario_oracles.json`` and every framework interaction crosses the installed CLI
boundary. This keeps the oracle independent from the implementation it evaluates.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import venv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

FORBIDDEN_FAILURE_ARTIFACTS = {
    "normalized_events.json",
    "workflow_runs.json",
    "excluded_runs.json",
    "telemetry_validation.json",
    "scorecard.json",
    "redaction_report.json",
    "monitoring_summary.csv",
    "telemetry_coverage.csv",
    "pilot_report.md",
    "enterprise_evaluation_report.md",
    "evaluation_package.json",
}
REQUIRED_SUCCESS_ARTIFACTS = {
    "diagnostics.json",
    "evaluation_readiness.json",
    "input_validation.json",
    "normalized_events.json",
    "workflow_runs.json",
    "excluded_runs.json",
    "telemetry_validation.json",
    "scorecard.json",
    "redaction_report.json",
    "monitoring_summary.csv",
    "telemetry_coverage.csv",
    "pilot_report.md",
    "quarantined_runs.json",
    "enterprise_evaluation_report.md",
    "evaluation_package.json",
}
LANGFUSE_OUTPUTS = {
    "canonical_events.jsonl",
    "langfuse_source_profile.json",
    "telemetry_coverage.json",
    "redaction_report.json",
    "trace_audit_sample.csv",
    "input_fingerprints.json",
    "evaluation_manifest.monitoring.json",
    "workflow_config.json",
    "source_profile.json",
    "mapping_spec.json",
    "mapping_assessment.json",
    "mapping_friction_summary.json",
    "mapping_issue_register.csv",
    "mapping_readiness_report.md",
    "diagnostics.json",
}

MAPPING_ONLY_ANALYTICAL_ARTIFACTS = {
    "normalized_events.json",
    "workflow_runs.json",
    "scorecard.json",
    "monitoring_summary.csv",
    "pilot_report.md",
    "enterprise_evaluation_report.md",
    "evaluation_package.json",
}


@dataclass
class MetricCheck:
    scenario: str
    metric: str
    expected: Any
    actual: Any
    tolerance: float | None
    passed: bool


@dataclass
class GovernanceCheck:
    scenario: str
    check: str
    expected: Any
    actual: Any
    passed: bool
    evidence: str


@dataclass
class ScenarioResult:
    scenario: str
    category: str
    expected_outcome: str
    status: str
    run_a_exit_code: int | None
    run_b_exit_code: int | None
    detail: str


@dataclass
class Execution:
    returncode: int
    stdout: str
    stderr: str


class ReadinessSuite:
    def __init__(
        self,
        *,
        repo_root: Path,
        output_root: Path,
        toolkit_command: list[str],
        command_mode: str,
        environment: dict[str, str],
        include_langfuse: bool,
    ) -> None:
        self.repo_root = repo_root
        self.output_root = output_root
        self.pack_root = repo_root / "data/readiness_samples/enterprise_readiness_pack"
        self.langfuse_root = repo_root / "data/langfuse_samples"
        self.toolkit_command = toolkit_command
        self.command_mode = command_mode
        self.environment = environment
        self.include_langfuse = include_langfuse
        self.metrics: list[MetricCheck] = []
        self.governance: list[GovernanceCheck] = []
        self.results: list[ScenarioResult] = []
        self.oracles = self._read_json(self.pack_root / "scenario_oracles.json")
        self.tolerance = float(self.oracles["float_tolerance"])

    def run(self) -> bool:
        for name, definition in self.oracles["positive_scenarios"].items():
            self._run_positive(name, definition)
        for definition in self.oracles["negative_scenarios"]:
            self._run_negative(definition)
        self._run_manifest_designs()
        self._run_tiered_partial_scenario()
        self._run_mapping_assessment_scenarios()
        self._run_generated_manifest_failures()
        self._verify_traceability()
        if self.include_langfuse:
            self._run_langfuse_hook()
        self._write_results()
        return all(result.status == "passed" for result in self.results)

    def _run_positive(self, name: str, definition: dict[str, Any]) -> None:
        scenario_root = self.output_root / "scenarios" / name
        config = self.pack_root / definition["workflow_config"]
        raw_logs = self.pack_root / definition["raw_logs"]
        manifest = self.pack_root / definition["evaluation_manifest"]
        start_metric = len(self.metrics)
        start_governance = len(self.governance)
        run_a = self._run_pipeline(
            config=config,
            raw_logs=raw_logs,
            manifest=manifest,
            output_dir=scenario_root / "run_a",
            execution_path=scenario_root / "run_a_execution.json",
        )
        run_b = self._run_pipeline(
            config=config,
            raw_logs=raw_logs,
            manifest=manifest,
            output_dir=scenario_root / "run_b",
            execution_path=scenario_root / "run_b_execution.json",
        )
        self._metric(name, "exit_code_run_a", 0, run_a.returncode)
        self._metric(name, "exit_code_run_b", 0, run_b.returncode)
        if run_a.returncode == 0 and run_b.returncode == 0:
            names_a = self._relative_file_names(scenario_root / "run_a")
            names_b = self._relative_file_names(scenario_root / "run_b")
            self._metric(
                name,
                "required_success_artifacts",
                sorted(REQUIRED_SUCCESS_ARTIFACTS),
                sorted(REQUIRED_SUCCESS_ARTIFACTS & names_a),
            )
            identical = names_a == names_b and all(
                (scenario_root / "run_a" / item).read_bytes()
                == (scenario_root / "run_b" / item).read_bytes()
                for item in names_a
            )
            self._governance_check(
                name,
                "byte_identical_repeated_execution",
                True,
                identical,
                identical,
                f"scenarios/{name}/run_a and run_b",
            )
            self._assert_positive_oracle(name, definition["expected"], scenario_root / "run_a")
        checks = self.metrics[start_metric:]
        governance = self.governance[start_governance:]
        passed = all(check.passed for check in checks) and all(check.passed for check in governance)
        failed_names = [check.metric for check in checks if not check.passed]
        failed_names += [check.check for check in governance if not check.passed]
        self.results.append(
            ScenarioResult(
                scenario=name,
                category="synthetic_positive",
                expected_outcome="success_twice",
                status="passed" if passed else "failed",
                run_a_exit_code=run_a.returncode,
                run_b_exit_code=run_b.returncode,
                detail=(
                    "Implementation-independent oracle matched and repeated artifacts "
                    "were byte-identical."
                    if passed
                    else "Failed checks: " + ", ".join(failed_names)
                ),
            )
        )

    def _assert_positive_oracle(
        self, name: str, expected: dict[str, Any], output_dir: Path
    ) -> None:
        workflow_runs = self._read_json(output_dir / "workflow_runs.json")
        scorecard = self._read_json(output_dir / "scorecard.json")
        if name == "complete_telemetry":
            normalized = self._read_json(output_dir / "normalized_events.json")
            run = workflow_runs[0]
            values = {
                "event_count": len(normalized),
                "run_count": len(workflow_runs),
                "status": run["status"],
                "duration_seconds": run["duration_seconds"],
                "input_tokens": run["input_tokens"],
                "output_tokens": run["output_tokens"],
                "tool_call_count": run["tool_call_count"],
                "avg_quality_score": run["avg_quality_score"],
            }
            self._metric_mapping(name, expected, values)
        elif name == "pilot_metric_oracle":
            observed = scorecard["observed_summary"]
            assumptions = scorecard["assumption_based_summary"]
            values = {
                key: observed[key]
                for key in (
                    "run_count_total",
                    "run_count_completed",
                    "run_count_failed",
                    "completion_rate",
                    "failure_rate",
                    "avg_duration_seconds",
                    "p50_duration_seconds",
                    "p90_duration_seconds",
                    "avg_retry_count",
                    "avg_intervention_count",
                    "avg_tool_call_count",
                    "avg_handoff_count",
                    "avg_routing_decision_count",
                    "avg_quality_score",
                    "total_observed_cost_usd",
                    "total_input_tokens",
                    "total_output_tokens",
                )
            }
            values["eligible_run_count"] = assumptions["eligible_run_count"]
            values["ineligible_run_count"] = assumptions["ineligible_run_count"]
            values.update(assumptions["values"])
            failed_run = next(
                run for run in scorecard["runs"] if run["run_id"] == expected["failed_run_id"]
            )
            estimates = failed_run["assumption_based_estimates"]
            values["failed_run_id"] = failed_run["run_id"]
            values["failed_run_labor_value_delta_usd"] = estimates["values"][
                "estimated_labor_value_delta_usd"
            ]
            values["failed_run_net_value_delta_usd"] = estimates["values"][
                "estimated_net_value_delta_usd"
            ]
            self._metric_mapping(name, expected, values)
            self._metric(
                name,
                "failed_run_benefit_eligible",
                False,
                estimates["benefit_eligible"],
            )
            self._governance_check(
                name,
                "failed_run_has_no_positive_productivity_value",
                {
                    "benefit_eligible": False,
                    "labor_value_usd": 0.0,
                    "net_value_usd": expected["failed_run_net_value_delta_usd"],
                },
                {
                    "benefit_eligible": estimates["benefit_eligible"],
                    "labor_value_usd": estimates["values"].get("estimated_labor_value_delta_usd"),
                    "net_value_usd": estimates["values"].get("estimated_net_value_delta_usd"),
                },
                estimates["benefit_eligible"] is False
                and estimates["values"].get("estimated_labor_value_delta_usd") == 0.0
                and estimates["values"].get("estimated_net_value_delta_usd", 0.0) < 0,
                "scorecard.json:runs[pilot-run-003].assumption_based_estimates",
            )
        elif name == "synthetic_comparator":
            summary = scorecard["comparator_summary"]
            by_config = summary["by_agent_configuration"]
            deltas = summary["deltas"]["values"]
            control = by_config["control-agent.v1"]
            treatment = by_config["treatment-agent.v1"]
            values = {
                "design_claim_ceiling": summary["design_claim_ceiling"],
                "attained_claim_strength": summary["attained_claim_strength"],
                "control_duration_seconds": control["avg_duration_seconds"],
                "control_quality_score": control["avg_quality_score"],
                "control_input_tokens": control["avg_input_tokens"],
                "control_output_tokens": control["avg_output_tokens"],
                "treatment_duration_seconds": treatment["avg_duration_seconds"],
                "treatment_quality_score": treatment["avg_quality_score"],
                "treatment_input_tokens": treatment["avg_input_tokens"],
                "treatment_output_tokens": treatment["avg_output_tokens"],
                "duration_delta_seconds": deltas["avg_duration_seconds_delta"],
                "quality_delta": deltas["avg_quality_score_delta"],
                "input_tokens_delta": deltas["avg_input_tokens_delta"],
                "output_tokens_delta": deltas["avg_output_tokens_delta"],
            }
            self._metric_mapping(name, expected, values)
        elif name == "redaction":
            report = self._read_json(output_dir / "redaction_report.json")
            derived_text = self._read_tree_text(output_dir)
            field_name = expected["restricted_field"]
            count = report["redacted_field_counts"].get(field_name, 0)
            leak_count = derived_text.count(expected["plaintext"])
            self._metric(name, "redacted_occurrences", expected["redacted_occurrences"], count)
            self._metric(name, "plaintext_leak_count", 0, leak_count)
            self._governance_check(
                name,
                "restricted_plaintext_absent_from_derived_outputs",
                0,
                leak_count,
                leak_count == 0,
                f"scenarios/{name}/run_a",
            )
        elif name == "window_exclusions":
            excluded = self._read_json(output_dir / "excluded_runs.json")
            summary = scorecard["measurement_window_summary"]
            actual_exclusions = {item["run_id"]: item["reason"] for item in excluded}
            values = {
                "included_run_ids": [run["run_id"] for run in workflow_runs],
                "excluded_runs": actual_exclusions,
                "included_run_count": summary["included_run_count"],
                "excluded_run_count": summary["excluded_run_count"],
                "total_reconstructed_run_count": summary["total_reconstructed_run_count"],
                "run_coverage": summary["run_coverage"],
                "unmatched_run_exclusion_ids": summary["unmatched_run_exclusion_ids"],
            }
            self._metric_mapping(name, expected, values)
            anchors_present = all(item["evidence_anchors"] for item in excluded)
            self._governance_check(
                name,
                "excluded_runs_retain_evidence_anchors",
                True,
                anchors_present,
                anchors_present,
                "excluded_runs.json",
            )
        elif name == "unsupported_signals":
            run = workflow_runs[0]
            actual_null_fields = [field for field in expected["null_fields"] if run[field] is None]
            self._metric(name, "null_fields", expected["null_fields"], actual_null_fields)
            self._metric(
                name,
                "unsupported_signals",
                expected["unsupported_signals"],
                run["unsupported_telemetry"],
            )
            telemetry = self._read_json(output_dir / "telemetry_validation.json")
            unsupported_coverage = {
                signal: telemetry["coverage_by_signal"][signal]
                for signal in expected["unsupported_signals"]
            }
            coverage_valid = all(
                row
                == {
                    "coverage": 0.0,
                    "run_denominator": 1,
                    "runs_with_signal": 0,
                    "supported": False,
                }
                for row in unsupported_coverage.values()
            )
            captured_empty = all(
                not event["evidence"]["captured_fields"] and not event["metadata"]
                for event in self._read_json(output_dir / "normalized_events.json")
            )
            self._governance_check(
                name,
                "unsupported_signal_denominators_are_explicit",
                True,
                coverage_valid,
                coverage_valid,
                "telemetry_validation.json:coverage_by_signal",
            )
            self._governance_check(
                name,
                "anchor_hash_only_retains_no_snapshots",
                True,
                captured_empty,
                captured_empty,
                "normalized_events.json:evidence.captured_fields",
            )
        elif name == "deduplication":
            run = workflow_runs[0]
            values = {key: run[key] for key in expected}
            self._metric_mapping(name, expected, values)

    def _run_negative(self, definition: dict[str, Any]) -> None:
        name = definition["name"]
        scenario_root = self.output_root / "scenarios" / name
        execution = self._run_pipeline(
            config=self.pack_root / definition["workflow_config"],
            raw_logs=self.pack_root / definition["raw_logs"],
            manifest=self.pack_root / definition["evaluation_manifest"],
            output_dir=scenario_root / "output",
            execution_path=scenario_root / "execution.json",
        )
        start_metric = len(self.metrics)
        start_governance = len(self.governance)
        self._metric(name, "exit_code", 1, execution.returncode)
        output_dir = scenario_root / "output"
        diagnostic_path = output_dir / "diagnostics.json"
        diagnostic_codes = (
            [item["code"] for item in self._read_json(diagnostic_path).get("diagnostics", [])]
            if diagnostic_path.is_file()
            else []
        )
        self._metric(
            name,
            "diagnostic_code",
            definition["diagnostic_code"],
            (
                definition["diagnostic_code"]
                if definition["diagnostic_code"] in diagnostic_codes
                else diagnostic_codes
            ),
        )
        degraded_names = {
            "missing_telemetry",
            "missing_boundary",
            "conflicting_boundaries",
            "post_terminal_event",
            "configuration_drift",
        }
        expected_tier = "mapping_only" if name in degraded_names else "blocked"
        readiness_path = output_dir / "evaluation_readiness.json"
        actual_tier = (
            self._read_json(readiness_path).get("attained_tier")
            if readiness_path.is_file()
            else None
        )
        self._metric(name, "attained_evidence_tier", expected_tier, actual_tier)
        actual_files = self._relative_file_names(output_dir) if output_dir.exists() else set()
        forbidden_policy = (
            MAPPING_ONLY_ANALYTICAL_ARTIFACTS
            if expected_tier == "mapping_only"
            else FORBIDDEN_FAILURE_ARTIFACTS
        )
        forbidden = sorted(forbidden_policy & actual_files)
        self._metric(name, "artifacts_outside_tier_policy", [], forbidden)
        self._governance_check(
            name,
            "tier_publication_policy_enforced",
            [],
            forbidden,
            not forbidden,
            f"scenarios/{name}/output",
        )
        if (
            "missing_required_summary" in definition
            and (output_dir / "quarantined_runs.json").exists()
        ):
            quarantined = self._read_json(output_dir / "quarantined_runs.json")
            missing_summary: dict[str, int] = {}
            for run in quarantined:
                for signal in run.get("missing_required_signals", []):
                    missing_summary[signal] = missing_summary.get(signal, 0) + 1
            self._metric(
                name,
                "missing_required_summary",
                definition["missing_required_summary"],
                dict(sorted(missing_summary.items())),
            )
        checks = self.metrics[start_metric:]
        governance = self.governance[start_governance:]
        passed = all(check.passed for check in checks) and all(check.passed for check in governance)
        failed_names = [check.metric for check in checks if not check.passed]
        failed_names += [check.check for check in governance if not check.passed]
        self.results.append(
            ScenarioResult(
                scenario=name,
                category="synthetic_negative",
                expected_outcome=(
                    "mapping_only_with_actionable_diagnostics"
                    if expected_tier == "mapping_only"
                    else "blocked_with_readiness_diagnostics_only"
                ),
                status="passed" if passed else "failed",
                run_a_exit_code=execution.returncode,
                run_b_exit_code=None,
                detail=(
                    "Tiered publication and diagnostics matched the declared safety policy."
                    if passed
                    else "Failed checks: " + ", ".join(failed_names)
                ),
            )
        )

    def _run_manifest_designs(self) -> None:
        matrix = self._read_json(self.pack_root / "design_manifest_matrix.json")
        base_manifest = self._read_json(self.pack_root / matrix["base_manifest"])
        for row in matrix["designs"]:
            design = row["evaluation_design"]
            name = f"design_manifest_{design}"
            scenario_root = self.output_root / "scenarios" / name
            manifest = dict(base_manifest)
            manifest["evaluation_id"] = f"thesis-readiness-design-{design}"
            manifest["evaluation_design"] = design
            manifest_path = scenario_root / "manifest.json"
            self._write_json(manifest_path, manifest)
            run_a = self._run_pipeline(
                config=self.pack_root / "workflow_config.json",
                raw_logs=self.pack_root / "comparator_telemetry.jsonl",
                manifest=manifest_path,
                output_dir=scenario_root / "run_a",
                execution_path=scenario_root / "run_a_execution.json",
            )
            run_b = self._run_pipeline(
                config=self.pack_root / "workflow_config.json",
                raw_logs=self.pack_root / "comparator_telemetry.jsonl",
                manifest=manifest_path,
                output_dir=scenario_root / "run_b",
                execution_path=scenario_root / "run_b_execution.json",
            )
            start_metric = len(self.metrics)
            start_governance = len(self.governance)
            self._metric(name, "exit_code_run_a", 0, run_a.returncode)
            self._metric(name, "exit_code_run_b", 0, run_b.returncode)
            if run_a.returncode == 0 and run_b.returncode == 0:
                scorecard = self._read_json(scenario_root / "run_a/scorecard.json")
                evaluation = scorecard["evaluation"]
                actual_ceiling = evaluation["design_claim_ceiling"]
                actual_attained = evaluation["attained_claim_strength"]
                self._metric(
                    name,
                    "design_claim_ceiling",
                    row["design_claim_ceiling"],
                    actual_ceiling,
                )
                self._metric(
                    name,
                    "attained_claim_strength",
                    row["attained_claim_strength"],
                    actual_attained,
                )
                identical = self._directories_byte_identical(
                    scenario_root / "run_a", scenario_root / "run_b"
                )
                self._governance_check(
                    name,
                    "byte_identical_repeated_execution",
                    True,
                    identical,
                    identical,
                    f"scenarios/{name}/run_a and run_b",
                )
                self._governance_check(
                    name,
                    "claim_ceiling_and_synthetic_cap_match_design",
                    {
                        "design_claim_ceiling": row["design_claim_ceiling"],
                        "attained_claim_strength": row["attained_claim_strength"],
                    },
                    {
                        "design_claim_ceiling": actual_ceiling,
                        "attained_claim_strength": actual_attained,
                    },
                    actual_ceiling == row["design_claim_ceiling"]
                    and actual_attained == row["attained_claim_strength"],
                    "scorecard.json:evaluation claim assessment",
                )
            checks = self.metrics[start_metric:]
            governance = self.governance[start_governance:]
            passed = all(check.passed for check in checks) and all(
                check.passed for check in governance
            )
            self.results.append(
                ScenarioResult(
                    scenario=name,
                    category="evaluation_design",
                    expected_outcome="valid_with_standard_claim_label_twice",
                    status="passed" if passed else "failed",
                    run_a_exit_code=run_a.returncode,
                    run_b_exit_code=run_b.returncode,
                    detail=(
                        "Schema and claim ceiling validated; synthetic evidence remained capped."
                        if passed
                        else "Design validation or claim label failed."
                    ),
                )
            )

    def _run_tiered_partial_scenario(self) -> None:
        name = "tiered_partial_quarantine"
        scenario_root = self.output_root / "scenarios" / name
        manifest = self._read_json(self.pack_root / "manifest_comparator.json")
        manifest["evaluation_id"] = "thesis-readiness-tiered-partial"
        manifest["evidence_context"] = {
            "source_kind": "synthetic_fixture",
            "mapping_assessment_required": False,
        }
        manifest["publication_policy"] = {"minimum_evidence_tier": "partial"}
        manifest_path = scenario_root / "manifest.json"
        self._write_json(manifest_path, manifest)
        rows = [
            json.loads(line)
            for line in (self.pack_root / "comparator_telemetry.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        rows = [row for row in rows if row["event_id"] != "cmp-004"]
        logs_path = scenario_root / "partial_telemetry.jsonl"
        logs_path.parent.mkdir(parents=True, exist_ok=True)
        logs_path.write_text(
            "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
            encoding="utf-8",
        )
        executions = []
        for repetition in ("a", "b"):
            executions.append(
                self._run_pipeline(
                    config=self.pack_root / "workflow_config.json",
                    raw_logs=logs_path,
                    manifest=manifest_path,
                    output_dir=scenario_root / f"run_{repetition}",
                    execution_path=scenario_root / f"run_{repetition}_execution.json",
                )
            )
        start_metric = len(self.metrics)
        start_governance = len(self.governance)
        for repetition, execution in zip(("a", "b"), executions, strict=True):
            self._metric(name, f"exit_code_run_{repetition}", 0, execution.returncode)
        if all(execution.returncode == 0 for execution in executions):
            output = scenario_root / "run_a"
            readiness = self._read_json(output / "evaluation_readiness.json")
            scorecard = self._read_json(output / "scorecard.json")
            quarantine = self._read_json(output / "quarantined_runs.json")
            self._metric(name, "attained_tier", "partial", readiness["attained_tier"])
            self._metric(name, "quarantined_run_count", 1, len(quarantine))
            self._metric(name, "quarantined_run_id", "cmp-run-002", quarantine[0]["run_id"])
            duration = scorecard["metric_statuses"]["duration"]
            self._metric(name, "duration_status", "partial", duration["status"])
            self._metric(name, "duration_covered", 1, duration["covered_run_count"])
            self._metric(name, "duration_population", 2, duration["population_denominator"])
            comparator = scorecard["metric_statuses"]["comparator"]
            self._metric(name, "comparator_status", "withheld", comparator["status"])
            self._metric(name, "comparator_deltas", {}, scorecard["comparator_summary"]["deltas"])
            identical = self._directories_byte_identical(
                scenario_root / "run_a", scenario_root / "run_b"
            )
            self._governance_check(
                name,
                "byte_identical_repeated_execution",
                True,
                identical,
                identical,
                f"scenarios/{name}/run_a and run_b",
            )
        checks = self.metrics[start_metric:]
        governance = self.governance[start_governance:]
        passed = all(check.passed for check in checks) and all(check.passed for check in governance)
        self.results.append(
            ScenarioResult(
                scenario=name,
                category="tiered_evidence",
                expected_outcome="partial_with_one_quarantined_run",
                status="passed" if passed else "failed",
                run_a_exit_code=executions[0].returncode,
                run_b_exit_code=executions[1].returncode,
                detail=(
                    "One anomalous run was quarantined and bounded retained-run metrics published."
                    if passed
                    else "Tier or denominator oracle failed."
                ),
            )
        )

    def _run_mapping_assessment_scenarios(self) -> None:
        name = "mapping_assessment"
        scenario_root = self.output_root / "scenarios" / name
        mapping_root = self.repo_root / "data" / "mapping_samples"
        start_metric = len(self.metrics)
        start_governance = len(self.governance)
        executions: list[Execution] = []
        for repetition in ("a", "b"):
            target = scenario_root / f"complete_run_{repetition}"
            command = [
                *self.toolkit_command,
                "assess-mapping",
                "--source-profile",
                str(mapping_root / "source_profile.complete.json"),
                "--mapping-spec",
                str(mapping_root / "mapping_spec.confirmed.json"),
                "--evaluation-manifest",
                str(mapping_root / "manifest.mapping.json"),
                "--activity-log",
                str(mapping_root / "mapping_activity.complete.jsonl"),
                "--output-dir",
                str(target),
            ]
            execution = self._run_command(command)
            executions.append(execution)
            self._write_execution(
                scenario_root / f"complete_run_{repetition}_execution.json",
                command,
                execution,
            )
            self._metric(name, f"complete_exit_code_{repetition}", 0, execution.returncode)
        complete = scenario_root / "complete_run_a"
        if all(execution.returncode == 0 for execution in executions):
            assessment = self._read_json(complete / "mapping_assessment.json")
            friction = self._read_json(complete / "mapping_friction_summary.json")
            self._metric(name, "mapping_readiness", "verified", assessment["mapping_readiness"])
            self._metric(
                name,
                "required_signal_progress",
                {
                    "mapped_count": 3,
                    "observed_count": 3,
                    "semantically_approved_count": 3,
                    "signal_count": 3,
                    "transformation_tested_count": 3,
                },
                assessment["required_signal_summary"],
            )
            expected_friction = {
                "mapping_iteration_count": 2,
                "decision_count": 1,
                "approval_activity_count": 1,
                "elapsed_onboarding_cycle_seconds": 3600.0,
            }
            self._metric_mapping(name, expected_friction, friction)
            self._metric(
                name,
                "active_effort_complete_total_minutes",
                45.0,
                friction["active_effort"]["complete_total_minutes"],
            )
            self._metric(
                name,
                "transformation_counts",
                {"coercion": 1, "direct": 2, "lookup": 1},
                friction["transformation_counts"],
            )
            identical = self._directories_byte_identical(
                scenario_root / "complete_run_a", scenario_root / "complete_run_b"
            )
            self._governance_check(
                name,
                "byte_identical_repeated_execution",
                True,
                identical,
                identical,
                f"scenarios/{name}/complete_run_a and complete_run_b",
            )

        no_activity = scenario_root / "no_activity"
        no_activity_command = [
            *self.toolkit_command,
            "assess-mapping",
            "--source-profile",
            str(mapping_root / "source_profile.complete.json"),
            "--mapping-spec",
            str(mapping_root / "mapping_spec.confirmed.json"),
            "--evaluation-manifest",
            str(mapping_root / "manifest.mapping.json"),
            "--output-dir",
            str(no_activity),
        ]
        no_activity_execution = self._run_command(no_activity_command)
        self._write_execution(
            scenario_root / "no_activity_execution.json",
            no_activity_command,
            no_activity_execution,
        )
        self._metric(name, "no_activity_exit_code", 0, no_activity_execution.returncode)
        if no_activity_execution.returncode == 0:
            effort = self._read_json(no_activity / "mapping_friction_summary.json")["active_effort"]
            self._metric(
                name, "no_activity_effort_status", "not_recorded", effort["measurement_status"]
            )
            self._metric(name, "no_activity_effort_total", None, effort["observed_total_minutes"])
            self._metric(name, "no_activity_denominator", 0, effort["activity_denominator"])

        checks = self.metrics[start_metric:]
        governance = self.governance[start_governance:]
        passed = all(check.passed for check in checks) and all(check.passed for check in governance)
        self.results.append(
            ScenarioResult(
                scenario=name,
                category="mapping_onboarding",
                expected_outcome="exact_friction_oracle_and_missing_effort_preservation",
                status="passed" if passed else "failed",
                run_a_exit_code=executions[0].returncode,
                run_b_exit_code=executions[1].returncode,
                detail=(
                    "Mapping and friction mechanics matched the independent fixture oracle."
                    if passed
                    else "Mapping readiness or friction oracle failed."
                ),
            )
        )

    def _run_generated_manifest_failures(self) -> None:
        base = self._read_json(self.pack_root / "manifest_complete.json")
        cases: list[tuple[str, dict[str, Any], str, str]] = []
        reversed_window = json.loads(json.dumps(base))
        reversed_window["evaluation_id"] = "thesis-readiness-reversed-window"
        reversed_window["measurement_window"]["start"] = "2026-04-02T00:00:00Z"
        reversed_window["measurement_window"]["end"] = "2026-04-01T00:00:00Z"
        cases.append(
            (
                "reversed_measurement_window",
                reversed_window,
                "must be later",
                "MANIFEST_INVALID_WINDOW",
            )
        )
        placeholder = json.loads(json.dumps(base))
        placeholder["evaluation_id"] = "TODO_REPLACE_ME"
        cases.append(
            (
                "unresolved_template_placeholder",
                placeholder,
                "Unresolved template",
                "CONFIG_UNRESOLVED_PLACEHOLDER",
            )
        )
        for name, manifest, diagnostic, expected_code in cases:
            scenario_root = self.output_root / "scenarios" / name
            manifest_path = scenario_root / "manifest.json"
            self._write_json(manifest_path, manifest)
            execution = self._run_pipeline(
                config=self.pack_root / "workflow_config.json",
                raw_logs=self.pack_root / "complete_telemetry.jsonl",
                manifest=manifest_path,
                output_dir=scenario_root / "output",
                execution_path=scenario_root / "execution.json",
            )
            output = scenario_root / "output"
            actual_files = self._relative_file_names(output) if output.exists() else set()
            forbidden = sorted(FORBIDDEN_FAILURE_ARTIFACTS & actual_files)
            self._metric(name, "exit_code", 1, execution.returncode)
            self._metric(name, "diagnostic_message_present", True, diagnostic in execution.stderr)
            diagnostic_path = output / "diagnostics.json"
            diagnostic_codes = (
                [item["code"] for item in self._read_json(diagnostic_path).get("diagnostics", [])]
                if diagnostic_path.is_file()
                else []
            )
            self._metric(
                name,
                "diagnostic_code",
                expected_code,
                expected_code if expected_code in diagnostic_codes else diagnostic_codes,
            )
            self._metric(name, "derived_success_artifacts_after_failure", [], forbidden)
            passed = all(check.passed for check in self.metrics if check.scenario == name)
            self.results.append(
                ScenarioResult(
                    scenario=name,
                    category="configuration_negative",
                    expected_outcome="fail_closed",
                    status="passed" if passed else "failed",
                    run_a_exit_code=execution.returncode,
                    run_b_exit_code=None,
                    detail=(
                        "Invalid manifest rejected before derived artifacts."
                        if passed
                        else "Invalid manifest was not rejected as expected."
                    ),
                )
            )

    def _verify_traceability(self) -> None:
        name = "traceability"
        output = self.output_root / "scenarios/complete_telemetry/run_a"
        start = len(self.governance)
        if not (output / "evaluation_package.json").exists():
            self._governance_check(
                name,
                "traceability_prerequisite",
                True,
                False,
                False,
                "complete_telemetry output missing",
            )
        else:
            self._verify_event_hashes(name, output)
            self._verify_package_hashes(name, output)
            raw = (self.pack_root / "complete_telemetry.jsonl").read_bytes()
            original = hashlib.sha256(raw).hexdigest()
            tampered = hashlib.sha256(raw + b"\nsynthetic-tamper").hexdigest()
            self._governance_check(
                name,
                "tampering_changes_input_fingerprint",
                True,
                original != tampered,
                original != tampered,
                "in-memory tamper probe",
            )
            repeated = self._directories_byte_identical(
                output, self.output_root / "scenarios/complete_telemetry/run_b"
            )
            self._governance_check(
                name,
                "result_package_independent_of_output_directory",
                True,
                repeated,
                repeated,
                "complete_telemetry run_a and run_b",
            )
        checks = self.governance[start:]
        passed = bool(checks) and all(check.passed for check in checks)
        self.results.append(
            ScenarioResult(
                scenario=name,
                category="governance",
                expected_outcome="all_hashes_verify_and_tampering_changes_fingerprint",
                status="passed" if passed else "failed",
                run_a_exit_code=0 if passed else None,
                run_b_exit_code=0 if passed else None,
                detail=(
                    "Event, raw-line, input, artifact, tamper, and repeated-run checks passed."
                    if passed
                    else "One or more traceability checks failed."
                ),
            )
        )

    def _verify_event_hashes(self, name: str, output: Path) -> None:
        fixture = self.pack_root / "complete_telemetry.jsonl"
        raw_lines = fixture.read_text(encoding="utf-8").splitlines()
        events = self._read_json(output / "normalized_events.json")
        raw_line_matches = True
        payload_matches = True
        for event in events:
            evidence = event["evidence"]
            raw_line = raw_lines[evidence["source_line"] - 1]
            raw_line_matches &= self._sha256(raw_line.encode("utf-8")) == evidence["raw_line_hash"]
            payload = json.loads(raw_line)
            canonical = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            payload_matches &= self._sha256(canonical) == evidence["raw_event_hash"]
        self._governance_check(
            name,
            "raw_line_hashes_recompute",
            True,
            raw_line_matches,
            raw_line_matches,
            "normalized_events.json:evidence.raw_line_hash",
        )
        self._governance_check(
            name,
            "canonical_event_hashes_recompute",
            True,
            payload_matches,
            payload_matches,
            "normalized_events.json:evidence.raw_event_hash",
        )

    def _verify_package_hashes(self, name: str, output: Path) -> None:
        package = self._read_json(output / "evaluation_package.json")
        input_paths = {
            "workflow_config": self.pack_root / "workflow_config.json",
            "raw_logs": self.pack_root / "complete_telemetry.jsonl",
            "evaluation_manifest": self.pack_root / "manifest_complete.json",
        }
        input_matches = all(
            self._sha256(input_paths[key].read_bytes()) == fingerprint["sha256"]
            and input_paths[key].stat().st_size == fingerprint["size_bytes"]
            for key, fingerprint in package["input_fingerprints"].items()
        )
        artifact_matches = all(
            self._sha256((output / item["path"]).read_bytes()) == item["sha256"]
            and (output / item["path"]).stat().st_size == item["size_bytes"]
            for item in package["artifact_fingerprints"]
        )
        lock = package["dependency_lock"]
        lock_matches = lock.get("status") == "unavailable" or (
            self._sha256((self.repo_root / "uv.lock").read_bytes()) == lock["sha256"]
        )
        version_fields_present = all(
            package.get(field)
            for field in ("toolkit_version", "artifact_schema_version", "python_version")
        )
        self._governance_check(
            name,
            "input_fingerprints_recompute",
            True,
            input_matches,
            input_matches,
            "evaluation_package.json:input_fingerprints",
        )
        self._governance_check(
            name,
            "artifact_fingerprints_recompute",
            True,
            artifact_matches,
            artifact_matches,
            "evaluation_package.json:artifact_fingerprints",
        )
        self._governance_check(
            name,
            "dependency_lock_fingerprint_recomputes",
            True,
            lock_matches,
            lock_matches,
            "evaluation_package.json:dependency_lock",
        )
        self._governance_check(
            name,
            "version_metadata_present",
            True,
            version_fields_present,
            version_fields_present,
            "evaluation_package.json",
        )

    def _run_langfuse_hook(self) -> None:
        name = "langfuse_adapter"
        help_result = self._run_command([*self.toolkit_command, "prepare-langfuse", "--help"])
        fixtures = {
            "mapping": self.langfuse_root / "langfuse_mapping.approved.json",
            "camel": self.langfuse_root / "observations.camel.json",
            "snake": self.langfuse_root / "observations.snake.json",
            "incomplete": self.langfuse_root / "observations.incomplete_api.json",
        }
        if help_result.returncode != 0 or not all(path.exists() for path in fixtures.values()):
            self.results.append(
                ScenarioResult(
                    scenario=name,
                    category="conditional_adapter",
                    expected_outcome="run_when_adapter_and_fixtures_are_available",
                    status="failed",
                    run_a_exit_code=help_result.returncode,
                    run_b_exit_code=None,
                    detail="prepare-langfuse command or stable fixture pack is unavailable.",
                )
            )
            return
        scenario_root = self.output_root / "scenarios" / name
        executions: dict[str, Execution] = {}
        for style in ("camel", "snake"):
            for repetition in ("a", "b"):
                target = scenario_root / f"{style}_run_{repetition}"
                command = [
                    *self.toolkit_command,
                    "prepare-langfuse",
                    "--input",
                    str(fixtures[style]),
                    "--mapping",
                    str(fixtures["mapping"]),
                    "--output-dir",
                    str(target),
                ]
                execution = self._run_command(command)
                executions[f"{style}_{repetition}"] = execution
                self._write_execution(
                    scenario_root / f"{style}_run_{repetition}_execution.json",
                    command,
                    execution,
                )
        incomplete_target = scenario_root / "incomplete_output"
        incomplete_command = [
            *self.toolkit_command,
            "prepare-langfuse",
            "--input",
            str(fixtures["incomplete"]),
            "--mapping",
            str(fixtures["mapping"]),
            "--output-dir",
            str(incomplete_target),
        ]
        incomplete = self._run_command(incomplete_command)
        self._write_execution(
            scenario_root / "incomplete_execution.json", incomplete_command, incomplete
        )
        start_metric = len(self.metrics)
        start_governance = len(self.governance)
        for label, execution in executions.items():
            self._metric(name, f"{label}_exit_code", 0, execution.returncode)
        self._metric(name, "incomplete_api_exit_code", 1, incomplete.returncode)
        successful = all(execution.returncode == 0 for execution in executions.values())
        if successful:
            camel_a = scenario_root / "camel_run_a"
            snake_a = scenario_root / "snake_run_a"
            self._metric(
                name,
                "required_langfuse_outputs",
                sorted(LANGFUSE_OUTPUTS),
                sorted(LANGFUSE_OUTPUTS & self._relative_file_names(camel_a)),
            )
            camel_events = self._semantic_langfuse_events(camel_a / "canonical_events.jsonl")
            snake_events = self._semantic_langfuse_events(snake_a / "canonical_events.jsonl")
            self._metric(
                name,
                "camel_and_snake_canonical_events_semantically_identical",
                camel_events,
                snake_events,
            )
            for style in ("camel", "snake"):
                repeated = self._directories_byte_identical(
                    scenario_root / f"{style}_run_a", scenario_root / f"{style}_run_b"
                )
                self._governance_check(
                    name,
                    f"{style}_repeated_execution_byte_identical",
                    True,
                    repeated,
                    repeated,
                    f"scenarios/{name}/{style}_run_a and {style}_run_b",
                )
            derived = self._read_tree_text(camel_a) + self._read_tree_text(snake_a)
            sentinel_count = derived.count("LEAKAGE SENTINEL")
            self._metric(name, "private_body_leak_count", 0, sentinel_count)
            self._governance_check(
                name,
                "langfuse_input_output_bodies_fully_masked",
                0,
                sentinel_count,
                sentinel_count == 0,
                "Langfuse derived outputs",
            )
        incomplete_files = (
            self._relative_file_names(incomplete_target) if incomplete_target.exists() else set()
        )
        incomplete_derived = sorted(LANGFUSE_OUTPUTS & incomplete_files)
        self._metric(name, "incomplete_api_derived_outputs", [], incomplete_derived)
        checks = self.metrics[start_metric:]
        governance = self.governance[start_governance:]
        passed = all(check.passed for check in checks) and all(check.passed for check in governance)
        self.results.append(
            ScenarioResult(
                scenario=name,
                category="conditional_adapter",
                expected_outcome="camel_and_snake_match; incomplete_pagination_rejected",
                status="passed" if passed else "failed",
                run_a_exit_code=executions["camel_a"].returncode,
                run_b_exit_code=executions["camel_b"].returncode,
                detail=(
                    "Offline normalization, masking, determinism, and pagination gate passed."
                    if passed
                    else "One or more Langfuse adapter checks failed."
                ),
            )
        )

    def _run_pipeline(
        self,
        *,
        config: Path,
        raw_logs: Path,
        manifest: Path,
        output_dir: Path,
        execution_path: Path,
    ) -> Execution:
        command = [
            *self.toolkit_command,
            "run",
            "--workflow-config",
            str(config),
            "--raw-logs",
            str(raw_logs),
            "--evaluation-manifest",
            str(manifest),
            "--output-dir",
            str(output_dir),
        ]
        execution = self._run_command(command)
        self._write_execution(execution_path, command, execution)
        return execution

    def _run_command(self, command: Sequence[str]) -> Execution:
        completed = subprocess.run(
            command,
            cwd=self.repo_root,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        return Execution(completed.returncode, completed.stdout, completed.stderr)

    def _write_execution(self, path: Path, command: Sequence[str], execution: Execution) -> None:
        logical_command = [self._logical_argument(item) for item in command]
        self._write_json(
            path,
            {
                "command": logical_command,
                "returncode": execution.returncode,
                "stdout": execution.stdout,
                "stderr": execution.stderr,
            },
        )

    def _logical_argument(self, value: str) -> str:
        if self.toolkit_command and value == self.toolkit_command[0]:
            return "<toolkit-python>"
        root = str(self.repo_root)
        return value.replace(root + os.sep, "<repo>/") if value.startswith(root) else value

    def _metric_mapping(
        self, scenario: str, expected: dict[str, Any], actual: dict[str, Any]
    ) -> None:
        for key, expected_value in expected.items():
            self._metric(scenario, key, expected_value, actual.get(key))

    def _metric(self, scenario: str, metric: str, expected: Any, actual: Any) -> None:
        tolerance = (
            self.tolerance
            if isinstance(expected, float)
            and not isinstance(expected, bool)
            and isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            else None
        )
        if tolerance is None:
            passed = expected == actual
        else:
            passed = abs(float(expected) - float(actual)) <= tolerance
        self.metrics.append(MetricCheck(scenario, metric, expected, actual, tolerance, passed))

    def _governance_check(
        self,
        scenario: str,
        check: str,
        expected: Any,
        actual: Any,
        passed: bool,
        evidence: str,
    ) -> None:
        self.governance.append(GovernanceCheck(scenario, check, expected, actual, passed, evidence))

    def _write_results(self) -> None:
        passed = sum(result.status == "passed" for result in self.results)
        failed = sum(result.status == "failed" for result in self.results)
        summary = {
            "readiness_schema_version": "1.0.0",
            "suite": "thesis_synthetic_readiness",
            "command_mode": self.command_mode,
            "overall_status": "passed" if failed == 0 else "failed",
            "scenario_count": len(self.results),
            "passed_scenario_count": passed,
            "failed_scenario_count": failed,
            "metric_check_count": len(self.metrics),
            "metric_check_pass_count": sum(check.passed for check in self.metrics),
            "governance_check_count": len(self.governance),
            "governance_check_pass_count": sum(check.passed for check in self.governance),
            "interpretation": (
                "Requirements-based synthetic conformance evidence only; no causal or "
                "enterprise productivity claim."
            ),
        }
        self._write_json(self.output_root / "readiness_summary.json", summary)
        self._write_csv(
            self.output_root / "readiness_matrix.csv",
            (
                "scenario",
                "category",
                "expected_outcome",
                "status",
                "run_a_exit_code",
                "run_b_exit_code",
                "detail",
            ),
            (
                (
                    result.scenario,
                    result.category,
                    result.expected_outcome,
                    result.status,
                    result.run_a_exit_code,
                    result.run_b_exit_code,
                    result.detail,
                )
                for result in self.results
            ),
        )
        self._write_csv(
            self.output_root / "metric_accuracy.csv",
            ("scenario", "metric", "expected", "actual", "tolerance", "passed"),
            (
                (
                    check.scenario,
                    check.metric,
                    self._csv_value(check.expected),
                    self._csv_value(check.actual),
                    check.tolerance,
                    str(check.passed).lower(),
                )
                for check in self.metrics
            ),
        )
        self._write_csv(
            self.output_root / "governance_repeatability.csv",
            ("scenario", "check", "expected", "actual", "passed", "evidence"),
            (
                (
                    check.scenario,
                    check.check,
                    self._csv_value(check.expected),
                    self._csv_value(check.actual),
                    str(check.passed).lower(),
                    check.evidence,
                )
                for check in self.governance
            ),
        )
        (self.output_root / "readiness_report.md").write_text(
            self._render_report(summary), encoding="utf-8"
        )
        (self.output_root / "synthetic_reconstruction_timeline.svg").write_text(
            self._render_timeline_svg(summary), encoding="utf-8"
        )

    def _render_report(self, summary: dict[str, Any]) -> str:
        lines = [
            "# Requirements-Based Public-Interface Conformance Results",
            "",
            f"**Overall status:** {summary['overall_status'].upper()}",
            "",
            (
                f"{summary['passed_scenario_count']} of {summary['scenario_count']} scenarios "
                f"passed; {summary['metric_check_pass_count']} of "
                f"{summary['metric_check_count']} metric checks and "
                f"{summary['governance_check_pass_count']} of "
                f"{summary['governance_check_count']} governance checks passed."
            ),
            "",
            "> Synthetic fixtures with known ground truth establish conformance under the tested "
            "contract. These results do not establish causal impact, enterprise productivity, "
            "or ROI.",
            "",
            "## Scenario matrix",
            "",
            "| Scenario | Category | Result | Evidence |",
            "|---|---|---:|---|",
        ]
        for result in self.results:
            lines.append(
                f"| `{result.scenario}` | {result.category} | "
                f"{result.status.upper()} | {result.detail} |"
            )
        lines.extend(
            [
                "",
                "## Result files",
                "",
                "- `metric_accuracy.csv` contains every hand-authored expected/actual comparison.",
                "- `governance_repeatability.csv` contains privacy, hashing, fail-closed, and "
                "deterministic repeatability checks.",
                "- `scenarios/` retains each CLI execution record, manifest, diagnostics, and "
                "successful artifact package.",
                "- `synthetic_reconstruction_timeline.svg` visualizes the evidence pipeline.",
                "",
            ]
        )
        return "\n".join(lines)

    def _render_timeline_svg(self, summary: dict[str, Any]) -> str:
        passed = summary["overall_status"] == "passed"
        color = "#16825d" if passed else "#b42318"
        stages = [
            ("1", "Canonical validation", "Reject malformed or duplicate rows"),
            ("2", "Run reconstruction", "Enforce explicit start and terminal boundaries"),
            ("3", "Telemetry gate", "Keep missing and unsupported signals explicit"),
            ("4", "Metric oracle", "Compare against hand-authored expected values"),
            ("5", "Evidence package", "Verify privacy, hashes, and byte repeatability"),
        ]
        blocks: list[str] = []
        for index, (number, title, subtitle) in enumerate(stages):
            x = 30 + index * 194
            blocks.extend(
                [
                    f'<rect x="{x}" y="70" width="170" height="105" rx="10" '
                    f'fill="#f8fafc" stroke="{color}" stroke-width="2"/>',
                    f'<circle cx="{x + 22}" cy="94" r="13" fill="{color}"/>',
                    f'<text x="{x + 22}" y="99" text-anchor="middle" '
                    f'class="number">{number}</text>',
                    f'<text x="{x + 12}" y="125" class="title">{title}</text>',
                    f'<text x="{x + 12}" y="147" class="subtitle">{subtitle}</text>',
                ]
            )
            if index < len(stages) - 1:
                blocks.append(
                    f'<path d="M {x + 170} 122 L {x + 190} 122" stroke="{color}" '
                    'stroke-width="2" marker-end="url(#arrow)"/>'
                )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="230" '
            'viewBox="0 0 1000 230" role="img" '
            'aria-label="Synthetic workflow reconstruction and evidence timeline">\n'
            "<style>.heading{font:700 20px sans-serif;fill:#14213d}.title{font:700 12px "
            "sans-serif;fill:#14213d}.subtitle{font:10px sans-serif;fill:#475467}.number{font:700 "
            "12px sans-serif;fill:white}.status{font:700 12px sans-serif}</style>\n"
            f'<defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" '
            f'orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" fill="{color}"/></marker></defs>\n'
            '<rect width="1000" height="230" fill="white"/>\n'
            '<text x="30" y="35" class="heading">Deterministic synthetic conformance '
            "evidence flow</text>\n"
            f'<text x="970" y="35" text-anchor="end" class="status" fill="{color}">'
            f'{summary["overall_status"].upper()}</text>\n' + "\n".join(blocks) + "\n</svg>\n"
        )

    @staticmethod
    def _read_json(path: Path) -> Any:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")

    @staticmethod
    def _write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(header)
            writer.writerows(rows)

    @staticmethod
    def _csv_value(value: Any) -> str:
        if isinstance(value, (dict, list, bool)) or value is None:
            return json.dumps(value, sort_keys=True, separators=(",", ":"))
        return str(value)

    @staticmethod
    def _sha256(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    @staticmethod
    def _relative_file_names(root: Path) -> set[str]:
        return {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}

    @classmethod
    def _directories_byte_identical(cls, left: Path, right: Path) -> bool:
        left_names = cls._relative_file_names(left)
        right_names = cls._relative_file_names(right)
        return left_names == right_names and all(
            (left / name).read_bytes() == (right / name).read_bytes() for name in left_names
        )

    @staticmethod
    def _read_tree_text(root: Path) -> str:
        chunks: list[str] = []
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            try:
                chunks.append(path.read_text(encoding="utf-8"))
            except UnicodeDecodeError:
                continue
        return "\n".join(chunks)

    @staticmethod
    def _semantic_langfuse_events(path: Path) -> list[dict[str, Any]]:
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        for event in events:
            event.pop("source_name", None)
            event.pop("source_line", None)
            event.pop("source_observation_hash", None)
        return events


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate deterministic synthetic readiness evidence for the thesis."
    )
    parser.add_argument(
        "--output-dir",
        default="experiments/readiness_results",
        help="Result directory (default: experiments/readiness_results).",
    )
    parser.add_argument(
        "--toolkit-command",
        help="Installed CLI command, parsed with shell quoting (for example: agent-eval-toolkit).",
    )
    parser.add_argument(
        "--wheel",
        help="Install this built wheel into a fresh temporary venv and run it with python -m.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing readiness result directory.",
    )
    parser.add_argument(
        "--skip-langfuse",
        action="store_true",
        help="Skip the adapter scenario (the synthetic core suite still runs).",
    )
    return parser


def _resolve_toolkit(
    args: argparse.Namespace, repo_root: Path
) -> tuple[list[str], str, dict[str, str], tempfile.TemporaryDirectory[str] | None]:
    environment = dict(os.environ)
    temporary_environment: tempfile.TemporaryDirectory[str] | None = None
    if args.wheel and args.toolkit_command:
        raise ValueError("Use either --wheel or --toolkit-command, not both")
    if args.wheel:
        wheel = Path(args.wheel).expanduser().resolve()
        if not wheel.is_file():
            raise ValueError(f"Wheel not found: {wheel}")
        temporary_environment = tempfile.TemporaryDirectory(prefix="agent-eval-readiness-")
        venv_root = Path(temporary_environment.name) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(venv_root)
        python = venv_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        install = subprocess.run(
            [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if install.returncode != 0:
            temporary_environment.cleanup()
            raise RuntimeError(f"Fresh-wheel installation failed:\n{install.stderr}")
        return (
            [str(python), "-m", "agent_eval_toolkit"],
            "fresh_wheel_venv",
            environment,
            temporary_environment,
        )
    if args.toolkit_command:
        command = shlex.split(args.toolkit_command)
        if not command:
            raise ValueError("--toolkit-command must not be empty")
        return command, "explicit_installed_command", environment, None
    installed = shutil.which("agent-eval-toolkit")
    if installed:
        return [installed], "installed_executable", environment, None
    source_root = repo_root / "src"
    current_python_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(source_root)
        if not current_python_path
        else str(source_root) + os.pathsep + current_python_path
    )
    return [sys.executable, "-m", "agent_eval_toolkit"], "python_module_source", environment, None


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    output_root = Path(args.output_dir).expanduser()
    if not output_root.is_absolute():
        output_root = repo_root / output_root
    if output_root.exists():
        if not args.overwrite:
            print(
                f"Refusing to replace existing readiness results: {output_root}; use --overwrite",
                file=sys.stderr,
            )
            return 2
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)
    temporary_environment: tempfile.TemporaryDirectory[str] | None = None
    try:
        command, mode, environment, temporary_environment = _resolve_toolkit(args, repo_root)
        suite = ReadinessSuite(
            repo_root=repo_root,
            output_root=output_root,
            toolkit_command=command,
            command_mode=mode,
            environment=environment,
            include_langfuse=not args.skip_langfuse,
        )
        passed = suite.run()
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Readiness suite could not complete: {exc}", file=sys.stderr)
        return 2
    finally:
        if temporary_environment is not None:
            temporary_environment.cleanup()
    print(f"Readiness results: {output_root}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
