from __future__ import annotations

from pathlib import Path

from agent_eval_toolkit.io_utils import RawJsonlRow, read_json, read_jsonl_with_line_numbers

ROOT = Path(__file__).resolve().parents[1]


def sample_workflow_config() -> dict:
    return read_json(ROOT / "data/sample_inputs/pilot_workflow.json")


def sample_raw_rows() -> list[RawJsonlRow]:
    return read_jsonl_with_line_numbers(ROOT / "data/sample_inputs/pilot_logs.jsonl")


def sample_baseline_context() -> dict:
    return read_json(ROOT / "data/sample_inputs/baseline_context.json")


def sample_evaluation_manifest() -> dict:
    return read_json(ROOT / "data/sample_inputs/evaluation_manifest.json")
