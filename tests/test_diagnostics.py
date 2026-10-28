from __future__ import annotations

import unittest

from agent_eval_toolkit.diagnostics import (
    canonical_validation_diagnostics,
    diagnostic_from_exception,
)
from agent_eval_toolkit.exceptions import DataValidationError


class DiagnosticsTest(unittest.TestCase):
    def test_exception_mapping_uses_stable_thesis_codes_and_context(self) -> None:
        cases = [
            (
                "Run `r-1` is missing its `run_started` boundary",
                "reconstruction",
                "BOUNDARY_MISSING_START",
            ),
            (
                "Run `r-2` has duplicate `run_started` boundaries",
                "reconstruction",
                "BOUNDARY_DUPLICATE_START",
            ),
            (
                "Run `r-3` event `e-3` occurs after the terminal boundary",
                "reconstruction",
                "BOUNDARY_POST_TERMINAL_EVENT",
            ),
            (
                "Incomplete Langfuse API capture in `observations.json`: meta.cursor is non-null",
                "langfuse_prepare",
                "LANGFUSE_INCOMPLETE_PAGINATION",
            ),
            (
                "Unresolved template placeholders are not allowed: workflow_config.id",
                "workflow_config",
                "CONFIG_UNRESOLVED_PLACEHOLDER",
            ),
        ]
        for message, stage, code in cases:
            with self.subTest(code=code):
                diagnostic = diagnostic_from_exception(DataValidationError(message), stage)
                self.assertEqual(diagnostic["code"], code)
                self.assertEqual(diagnostic["stage"], stage)
        contextual = diagnostic_from_exception(
            DataValidationError("Run `r-3` event `e-3` occurs after the terminal boundary"),
            "reconstruction",
        )
        self.assertEqual(contextual["run_id"], "r-3")
        self.assertEqual(contextual["event_id"], "e-3")

    def test_canonical_diagnostics_include_raw_field_event_run_and_line(self) -> None:
        validation = {
            "duplicate_event_ids": [],
            "rows_with_missing_fields": [],
            "rows_with_invalid_fields": [
                {"source_line": 4, "invalid_signals": ["observed_cost_usd"]}
            ],
        }
        config = {
            "source_schema": {
                "event_id_field": "event_id",
                "timestamp_field": "timestamp",
                "event_type_field": "event_name",
            },
            "run_id_keys": ["workflow_run_id"],
            "cost_field": "cost",
        }
        raw_rows = [(4, {"event_id": "evt-4", "workflow_run_id": "run-4", "cost": -1})]

        diagnostic = canonical_validation_diagnostics(validation, raw_rows, config)[0]

        self.assertEqual(diagnostic["code"], "CANONICAL_NEGATIVE_VALUE")
        self.assertEqual(diagnostic["field"], "cost")
        self.assertEqual(diagnostic["event_id"], "evt-4")
        self.assertEqual(diagnostic["run_id"], "run-4")
        self.assertEqual(diagnostic["source_line"], 4)


if __name__ == "__main__":
    unittest.main()
