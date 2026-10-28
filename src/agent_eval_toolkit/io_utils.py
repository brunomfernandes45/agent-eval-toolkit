from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .exceptions import DataValidationError
from .models import to_dict

RawJsonlRow = tuple[int, dict[str, Any]] | tuple[int, dict[str, Any], str]


def read_json(path: str | Path) -> Any:
    target = Path(path)
    try:
        with target.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise DataValidationError(f"Input file not found: {target}") from exc
    except json.JSONDecodeError as exc:
        raise DataValidationError(
            f"Invalid JSON in {target}: line {exc.lineno}, column {exc.colno}"
        ) from exc


def read_jsonl_with_line_numbers(path: str | Path) -> list[RawJsonlRow]:
    rows: list[RawJsonlRow] = []
    target = Path(path)
    try:
        with target.open("r", encoding="utf-8") as handle:
            for index, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                payload = json.loads(stripped)
                if not isinstance(payload, dict):
                    raise DataValidationError(
                        f"JSONL row {index} in {target} must be a JSON object"
                    )
                rows.append((index, payload, line.rstrip("\r\n")))
    except FileNotFoundError as exc:
        raise DataValidationError(f"Input file not found: {target}") from exc
    except json.JSONDecodeError as exc:
        raise DataValidationError(
            f"Invalid JSONL in {target}: line {exc.lineno}, column {exc.colno}"
        ) from exc
    return rows


def unpack_jsonl_row(row: RawJsonlRow) -> tuple[int, dict[str, Any], str | None]:
    if len(row) == 2:
        return row[0], row[1], None
    return row[0], row[1], row[2]


def write_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(to_dict(payload), handle, indent=2, sort_keys=True)
        handle.write("\n")


def write_text(path: str | Path, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        handle.write(text)
