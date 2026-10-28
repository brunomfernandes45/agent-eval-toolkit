from __future__ import annotations

import unittest

from agent_eval_toolkit.metrics import build_scorecard
from agent_eval_toolkit.normalize import normalize_logs
from agent_eval_toolkit.reconstruct import reconstruct_runs
from agent_eval_toolkit.report import render_markdown_report
from agent_eval_toolkit.validation import validate_telemetry_requirements
from tests.helpers import (
    ROOT,
    sample_baseline_context,
    sample_evaluation_manifest,
    sample_raw_rows,
    sample_workflow_config,
)


class ReportTest(unittest.TestCase):
    def test_render_markdown_report_contains_core_sections(self) -> None:
        events = normalize_logs(
            sample_raw_rows(),
            sample_workflow_config(),
            str(ROOT / "data/sample_inputs/pilot_logs.jsonl"),
        )
        runs = reconstruct_runs(events, sample_workflow_config())
        manifest = sample_evaluation_manifest()
        workflow_config = sample_workflow_config()
        telemetry_validation = validate_telemetry_requirements(runs, manifest, workflow_config)
        scorecard = build_scorecard(runs, manifest, sample_baseline_context(), telemetry_validation)
        report = render_markdown_report(scorecard, runs)

        self.assertIn("# Workflow Evaluation Report", report)
        self.assertIn("Design claim ceiling", report)
        self.assertIn("Attained claim strength", report)
        self.assertIn("## Evaluation Design", report)
        self.assertIn("## Telemetry Coverage", report)
        self.assertIn("## Observed Summary", report)
        self.assertIn("## Comparator Summary", report)
        self.assertIn("## Assumption-Based Summary", report)
        self.assertIn("### Run `run-002`", report)


if __name__ == "__main__":
    unittest.main()
