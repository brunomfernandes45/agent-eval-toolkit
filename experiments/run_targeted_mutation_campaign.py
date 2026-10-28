#!/usr/bin/env python3
"""Run the predeclared, thesis-critical mutation campaign without toolkit imports."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run targeted thesis-critical mutants.")
    parser.add_argument(
        "--spec",
        default="verification/critical_mutants.json",
        help="Predeclared mutation specification.",
    )
    parser.add_argument(
        "--output-dir",
        default="experiments/mutation_results",
        help="Directory for mutation_summary.json and mutation_results.csv.",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser


def _run_tests(
    python: str,
    repo_root: Path,
    source_root: Path,
    targets: list[str],
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join((str(source_root), str(repo_root)))
    return subprocess.run(
        [python, "-m", "unittest", *targets],
        cwd=repo_root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _sanitize(text: str, repo_root: Path, temp_root: Path) -> str:
    return text.replace(str(repo_root), "<repo>").replace(str(temp_root), "<mutation-temp>")


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    spec_path = Path(args.spec)
    if not spec_path.is_absolute():
        spec_path = repo_root / spec_path
    output = Path(args.output_dir)
    if not output.is_absolute():
        output = repo_root / output
    if output.exists():
        if not args.overwrite:
            print(f"Refusing to replace {output}; use --overwrite", file=sys.stderr)
            return 2
        shutil.rmtree(output)
    output.mkdir(parents=True)

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    mutants = spec["mutants"]
    python = sys.executable
    original_source = repo_root / "src"
    all_targets = sorted({target for mutant in mutants for target in mutant["tests"]})
    baseline = _run_tests(python, repo_root, original_source, all_targets)
    if baseline.returncode != 0:
        _write_json(
            output / "mutation_summary.json",
            {
                "overall_status": "invalid_baseline",
                "baseline_returncode": baseline.returncode,
                "stderr": _sanitize(baseline.stderr, repo_root, output),
            },
        )
        return 2

    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="agent-eval-mutants-") as temp:
        temp_root = Path(temp)
        for mutant in mutants:
            mutant_root = temp_root / mutant["id"]
            mutant_source = mutant_root / "src"
            shutil.copytree(original_source, mutant_source)
            target_file = mutant_root / mutant["file"]
            content = target_file.read_text(encoding="utf-8")
            occurrence_count = content.count(mutant["search"])
            if occurrence_count != 1:
                rows.append(
                    {
                        "mutant_id": mutant["id"],
                        "rule": mutant["rule"],
                        "status": "invalid_spec",
                        "search_occurrences": occurrence_count,
                        "test_targets": mutant["tests"],
                        "returncode": None,
                        "diagnostic": "Expected the mutation search text exactly once.",
                    }
                )
                continue
            target_file.write_text(
                content.replace(mutant["search"], mutant["replacement"], 1),
                encoding="utf-8",
            )
            execution = _run_tests(python, repo_root, mutant_source, mutant["tests"])
            killed = execution.returncode != 0
            diagnostic_text = execution.stderr or execution.stdout
            rows.append(
                {
                    "mutant_id": mutant["id"],
                    "rule": mutant["rule"],
                    "status": "killed" if killed else "survived",
                    "search_occurrences": occurrence_count,
                    "test_targets": mutant["tests"],
                    "returncode": execution.returncode,
                    "diagnostic": _sanitize(diagnostic_text[-2000:], repo_root, temp_root),
                }
            )

    with (output / "mutation_results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            [
                "mutant_id",
                "rule",
                "status",
                "search_occurrences",
                "returncode",
                "test_targets",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["mutant_id"],
                    row["rule"],
                    row["status"],
                    row["search_occurrences"],
                    row["returncode"],
                    ";".join(row["test_targets"]),
                ]
            )
    _write_json(output / "mutation_diagnostics.json", rows)
    killed_count = sum(row["status"] == "killed" for row in rows)
    survived_count = sum(row["status"] == "survived" for row in rows)
    invalid_count = sum(row["status"] == "invalid_spec" for row in rows)
    summary = {
        "mutation_schema_version": "1.0",
        "overall_status": (
            "passed"
            if killed_count == len(mutants) and not survived_count and not invalid_count
            else "failed"
        ),
        "declared_critical_mutant_count": len(mutants),
        "killed_critical_mutant_count": killed_count,
        "surviving_critical_mutant_count": survived_count,
        "invalid_mutant_spec_count": invalid_count,
        "mutation_score": round(killed_count / len(mutants), 3) if mutants else None,
        "interpretation": (
            "Targeted non-equivalent thesis-critical mutants only; not an exhaustive "
            "mutation score for the repository."
        ),
    }
    _write_json(output / "mutation_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["overall_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
