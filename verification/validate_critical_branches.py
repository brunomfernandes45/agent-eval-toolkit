#!/usr/bin/env python3
"""Validate complete coverage of the predeclared thesis-critical branch arcs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        default="verification/critical_branches.json",
        help="Critical branch declaration relative to the repository root.",
    )
    parser.add_argument(
        "--mutants",
        default="verification/critical_mutants.json",
        help="Critical mutation declaration used to check complete rule mapping.",
    )
    parser.add_argument(
        "--coverage-json",
        required=True,
        help="coverage.py JSON report generated with branch=true.",
    )
    return parser


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _resolve(repo_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def _coverage_entry(
    coverage_files: dict[str, Any], repo_root: Path, declared_file: str
) -> dict[str, Any] | None:
    direct = coverage_files.get(declared_file)
    if isinstance(direct, dict):
        return direct
    expected = (repo_root / declared_file).resolve()
    for key, value in coverage_files.items():
        candidate = Path(key)
        resolved = (
            candidate.resolve() if candidate.is_absolute() else (repo_root / candidate).resolve()
        )
        if resolved == expected and isinstance(value, dict):
            return value
    return None


def validate(
    repo_root: Path,
    manifest: dict[str, Any],
    mutants: dict[str, Any],
    coverage: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    decisions = manifest.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        return ["critical branch manifest must declare a non-empty decisions list"]
    coverage_files = coverage.get("files")
    if not isinstance(coverage_files, dict):
        return ["coverage report does not contain a files object"]

    thresholds = manifest.get("thresholds")
    totals = coverage.get("totals")
    if not isinstance(thresholds, dict):
        errors.append("critical branch manifest must declare coverage thresholds")
    elif not isinstance(totals, dict):
        errors.append("coverage report does not contain a totals object")
    else:
        measured = {
            "minimum_statement_percent": totals.get("percent_statements_covered"),
            "minimum_branch_percent": totals.get("percent_branches_covered"),
        }
        for threshold_name, actual in measured.items():
            minimum = thresholds.get(threshold_name)
            if not isinstance(minimum, int | float) or isinstance(minimum, bool):
                errors.append(f"{threshold_name}: threshold must be numeric")
            elif not isinstance(actual, int | float) or isinstance(actual, bool):
                errors.append(f"{threshold_name}: coverage percentage is unavailable")
            elif actual < minimum:
                errors.append(f"{threshold_name}: measured {actual:.6f}% is below {minimum:.6f}%")
        critical_minimum = thresholds.get("critical_decision_branch_percent")
        if critical_minimum != 100 and critical_minimum != 100.0:
            errors.append("critical_decision_branch_percent must be exactly 100")

    mutant_items = mutants.get("mutants", [])
    declared_mutants = {
        item.get("id") for item in mutant_items if isinstance(item, dict) and item.get("id")
    }
    mapped_mutants: set[str] = set()
    seen_decisions: set[str] = set()

    for raw_decision in decisions:
        if not isinstance(raw_decision, dict):
            errors.append("every decision must be an object")
            continue
        decision_id = raw_decision.get("id")
        if not isinstance(decision_id, str) or not decision_id:
            errors.append("every decision must have a non-empty id")
            continue
        if decision_id in seen_decisions:
            errors.append(f"{decision_id}: duplicate decision id")
            continue
        seen_decisions.add(decision_id)

        mutant_ids = raw_decision.get("mutant_ids")
        if not isinstance(mutant_ids, list) or not mutant_ids:
            errors.append(f"{decision_id}: mutant_ids must be a non-empty list")
        else:
            for mutant_id in mutant_ids:
                if not isinstance(mutant_id, str):
                    errors.append(f"{decision_id}: mutant id must be a string")
                elif mutant_id not in declared_mutants:
                    errors.append(f"{decision_id}: unknown mutant {mutant_id}")
                else:
                    mapped_mutants.add(mutant_id)

        declared_file = raw_decision.get("file")
        line = raw_decision.get("decision_line")
        anchor = raw_decision.get("anchor")
        rationale = raw_decision.get("rationale")
        if not isinstance(declared_file, str) or not declared_file:
            errors.append(f"{decision_id}: file must be a non-empty string")
            continue
        if isinstance(line, bool) or not isinstance(line, int) or line < 1:
            errors.append(f"{decision_id}: decision_line must be a positive integer")
            continue
        if not isinstance(anchor, str) or not anchor:
            errors.append(f"{decision_id}: anchor must be a non-empty string")
            continue
        if not isinstance(rationale, str) or not rationale:
            errors.append(f"{decision_id}: rationale must be a non-empty string")

        source_path = repo_root / declared_file
        if not source_path.is_file():
            errors.append(f"{decision_id}: source file not found: {declared_file}")
            continue
        source_lines = source_path.read_text(encoding="utf-8").splitlines()
        if line > len(source_lines) or source_lines[line - 1].strip() != anchor:
            actual = source_lines[line - 1].strip() if line <= len(source_lines) else "<EOF>"
            errors.append(
                f"{decision_id}: anchor mismatch at {declared_file}:{line}; "
                f"expected {anchor!r}, found {actual!r}"
            )

        entry = _coverage_entry(coverage_files, repo_root, declared_file)
        if entry is None:
            errors.append(f"{decision_id}: file absent from coverage report: {declared_file}")
            continue
        executed = {
            (arc[0], arc[1])
            for arc in entry.get("executed_branches", [])
            if isinstance(arc, list) and len(arc) == 2
        }
        missing = {
            (arc[0], arc[1])
            for arc in entry.get("missing_branches", [])
            if isinstance(arc, list) and len(arc) == 2
        }
        possible = {arc for arc in executed | missing if arc[0] == line}

        expected_items = raw_decision.get("expected_arcs")
        if not isinstance(expected_items, list) or not expected_items:
            errors.append(f"{decision_id}: expected_arcs must be a non-empty list")
            continue
        expected: set[tuple[int, int]] = set()
        for item in expected_items:
            if not isinstance(item, dict):
                errors.append(f"{decision_id}: expected arc must be an object")
                continue
            arc = (item.get("from"), item.get("to"))
            if not all(isinstance(part, int) and not isinstance(part, bool) for part in arc):
                errors.append(f"{decision_id}: expected arc endpoints must be integers")
                continue
            expected.add(arc)
            if arc[0] != line:
                errors.append(f"{decision_id}: expected arc {arc} does not start at decision_line")

        if expected != possible:
            errors.append(
                f"{decision_id}: declared arcs {sorted(expected)} do not exactly match "
                f"coverage.py decision arcs {sorted(possible)}"
            )
        unexecuted = expected - executed
        if unexecuted:
            errors.append(f"{decision_id}: unexecuted critical arcs {sorted(unexecuted)}")

    unmapped = declared_mutants - mapped_mutants
    if unmapped:
        errors.append("critical mutants without a branch decision: " + ", ".join(sorted(unmapped)))
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    try:
        manifest = _load_json(_resolve(repo_root, args.manifest))
        mutants = _load_json(_resolve(repo_root, args.mutants))
        coverage = _load_json(_resolve(repo_root, args.coverage_json))
        errors = validate(repo_root, manifest, mutants, coverage)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"critical branch validation failed: {exc}", file=sys.stderr)
        return 2

    result = {
        "critical_branch_decision_count": len(manifest["decisions"]),
        "coverage": {
            "branch_percent": coverage.get("totals", {}).get("percent_branches_covered"),
            "statement_percent": coverage.get("totals", {}).get("percent_statements_covered"),
        },
        "declared_critical_mutant_count": len(mutants.get("mutants", [])),
        "error_count": len(errors),
        "errors": errors,
        "passed": not errors,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
