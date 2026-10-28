from __future__ import annotations

import csv
import io
import unittest

from agent_eval_toolkit.metrics import build_scorecard
from agent_eval_toolkit.normalize import normalize_logs
from agent_eval_toolkit.reconstruct import reconstruct_runs
from agent_eval_toolkit.result_exports import (
    render_monitoring_summary_csv,
    render_telemetry_coverage_csv,
)
from agent_eval_toolkit.validation import validate_telemetry_requirements
from tests.helpers import (
    sample_evaluation_manifest,
    sample_raw_rows,
    sample_workflow_config,
)


class ResultExportsTest(unittest.TestCase):
    def test_csv_exports_are_tidy_and_preserve_missingness(self) -> None:
        config = sample_workflow_config()
        runs = reconstruct_runs(
            normalize_logs(sample_raw_rows(), config, "pilot_logs.jsonl"), config
        )
        manifest = sample_evaluation_manifest()
        telemetry = validate_telemetry_requirements(runs, manifest, config)
        scorecard = build_scorecard(runs, manifest, telemetry_validation=telemetry)

        summary_rows = list(csv.DictReader(io.StringIO(render_monitoring_summary_csv(scorecard))))
        coverage_rows = list(csv.DictReader(io.StringIO(render_telemetry_coverage_csv(telemetry))))

        self.assertTrue(any(row["metric"] == "completion_rate" for row in summary_rows))
        quality = next(row for row in coverage_rows if row["signal"] == "quality_score")
        self.assertEqual(quality["requirement"], "optional")
        self.assertEqual(quality["coverage"], "0.667")


if __name__ == "__main__":
    unittest.main()
