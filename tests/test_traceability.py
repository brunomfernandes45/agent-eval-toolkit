from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_eval_toolkit.traceability import (
    build_artifact_index,
    configured_restricted_canaries,
    file_fingerprint,
    hash_payload,
    hash_raw_line,
    scan_derived_bundle,
)


class TraceabilityTest(unittest.TestCase):
    def test_hash_payload_is_stable_for_key_order(self) -> None:
        self.assertEqual(hash_payload({"a": 1, "b": 2}), hash_payload({"b": 2, "a": 1}))

    def test_raw_line_hash_preserves_line_level_identity(self) -> None:
        self.assertNotEqual(hash_raw_line('{"a":1}'), hash_raw_line('{"a": 1}'))

    def test_file_fingerprint_reports_sha_and_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir, "input.jsonl")
            target.write_text('{"a":1}\n', encoding="utf-8")

            result = file_fingerprint(target)

        self.assertEqual(result["size_bytes"], 8)
        self.assertEqual(len(result["sha256"]), 64)

    def test_artifact_index_uses_logical_relative_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir, "scorecard.json")
            target.write_text("{}\n", encoding="utf-8")

            result = build_artifact_index(temp_dir, ["scorecard.json"])

        self.assertEqual(result[0]["path"], "scorecard.json")

    def test_restricted_canary_scan_covers_files_and_captured_output(self) -> None:
        config = {"restricted_canaries": {"customer": "PRIVATE-CANARY"}}
        canaries = configured_restricted_canaries(config)
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "diagnostics.json").write_text(
                '{"message":"PRIVATE-CANARY"}\n', encoding="utf-8"
            )
            result = scan_derived_bundle(
                temp_dir,
                canaries,
                captured_output={"stderr": "also PRIVATE-CANARY"},
            )

        self.assertFalse(result["passed"])
        self.assertEqual(
            {match["location"] for match in result["matches"]},
            {"diagnostics.json", "captured_output:stderr"},
        )


if __name__ == "__main__":
    unittest.main()
