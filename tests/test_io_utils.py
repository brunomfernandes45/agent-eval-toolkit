from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_eval_toolkit.exceptions import DataValidationError
from agent_eval_toolkit.io_utils import (
    read_json,
    read_jsonl_with_line_numbers,
    unpack_jsonl_row,
    write_json,
    write_text,
)


class IoUtilsTest(unittest.TestCase):
    def test_write_and_read_json_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir, "payload.json")
            write_json(target, {"a": 1, "b": ["x"]})
            self.assertEqual(read_json(target), {"a": 1, "b": ["x"]})

    def test_read_jsonl_with_line_numbers_skips_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir, "events.jsonl")
            target.write_text('{"id": 1}\n\n{"id": 2}\n', encoding="utf-8")

            rows = read_jsonl_with_line_numbers(target)

            self.assertEqual(rows, [(1, {"id": 1}, '{"id": 1}'), (3, {"id": 2}, '{"id": 2}')])

    def test_write_text_persists_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir, "note.txt")
            write_text(target, "framework")
            self.assertEqual(target.read_text(encoding="utf-8"), "framework")

    def test_read_json_reports_missing_and_malformed_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(DataValidationError, "Input file not found"):
                read_json(root / "missing.json")
            malformed = root / "malformed.json"
            malformed.write_text('{"broken":', encoding="utf-8")
            with self.assertRaisesRegex(DataValidationError, "Invalid JSON"):
                read_json(malformed)

    def test_read_jsonl_reports_missing_malformed_and_non_object_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(DataValidationError, "Input file not found"):
                read_jsonl_with_line_numbers(root / "missing.jsonl")
            malformed = root / "malformed.jsonl"
            malformed.write_text('{"broken":\n', encoding="utf-8")
            with self.assertRaisesRegex(DataValidationError, "Invalid JSONL"):
                read_jsonl_with_line_numbers(malformed)
            non_object = root / "non-object.jsonl"
            non_object.write_text("[]\n", encoding="utf-8")
            with self.assertRaisesRegex(DataValidationError, "must be a JSON object"):
                read_jsonl_with_line_numbers(non_object)

    def test_unpack_jsonl_row_supports_legacy_and_exact_line_shapes(self) -> None:
        self.assertEqual(unpack_jsonl_row((2, {"id": 1})), (2, {"id": 1}, None))
        self.assertEqual(
            unpack_jsonl_row((3, {"id": 2}, '{"id":2}')),
            (3, {"id": 2}, '{"id":2}'),
        )


if __name__ == "__main__":
    unittest.main()
