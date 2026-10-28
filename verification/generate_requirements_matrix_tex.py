#!/usr/bin/env python3
"""Generate the dissertation's complete requirement matrix from the frozen CSV."""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "verification" / "requirements_matrix.csv"
OUTPUT = ROOT / "report" / "requirements_matrix_longtable.tex"
EXPECTED_REQUIREMENT_COUNT = 58
TABLE_COLUMNS = (
    r"@{}>{\RaggedRight\arraybackslash}p{0.075\linewidth}"
    r">{\RaggedRight\arraybackslash}p{0.185\linewidth}"
    r">{\RaggedRight\arraybackslash}p{0.145\linewidth}"
    r">{\RaggedRight\arraybackslash}p{0.155\linewidth}"
    r">{\RaggedRight\arraybackslash}p{0.170\linewidth}"
    r">{\RaggedRight\arraybackslash}p{0.180\linewidth}@{}"
)
TABLE_HEADER = (
    r"\textbf{ID / category} & \textbf{Normative rule} & "
    r"\textbf{Rationale or validity threat} & \textbf{Implementation control} & "
    r"\textbf{Positive / negative or boundary cases} & "
    r"\textbf{Expected result or code / final evidence}\\"
)


def _tex(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_\allowbreak{}",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
        "-": r"-\allowbreak{}",
        "/": r"/\allowbreak{}",
        ";": r";\allowbreak{}",
        "|": r"\textbar{}\allowbreak{}",
    }
    return "".join(replacements.get(character, character) for character in value)


def main() -> None:
    with SOURCE.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != EXPECTED_REQUIREMENT_COUNT:
        raise SystemExit(
            f"Expected {EXPECTED_REQUIREMENT_COUNT} requirement rows, found {len(rows)}"
        )

    lines = [
        "% Generated from verification/requirements_matrix.csv; do not edit by hand.",
        r"\begin{landscape}",
        r"\begingroup",
        r"\scriptsize",
        r"\setlength{\tabcolsep}{2.5pt}",
        r"\renewcommand{\arraystretch}{1.12}",
        rf"\begin{{longtable}}{{{TABLE_COLUMNS}}}",
        rf"\caption{{Complete {EXPECTED_REQUIREMENT_COUNT}-row bidirectional "
        r"requirement-to-evidence matrix.}",
        r"\label{tab:requirements-matrix}\\",
        r"\toprule",
        TABLE_HEADER,
        r"\midrule",
        r"\endfirsthead",
        r"\multicolumn{6}{c}{\tablename\ \thetable{} -- continued from previous page}\\",
        r"\toprule",
        TABLE_HEADER,
        r"\midrule",
        r"\endhead",
        r"\midrule",
        r"\multicolumn{6}{r}{Continued on next page}\\",
        r"\endfoot",
        r"\bottomrule",
        r"\endlastfoot",
    ]
    for row in rows:
        id_and_category = (
            rf"\textbf{{{_tex(row['requirement_id'])}}}\newline "
            rf"\textit{{{_tex(row['category'])}}}"
        )
        cases = (
            rf"\textbf{{Positive:}} {_tex(row['positive_case'])}\newline "
            rf"\textbf{{Negative:}} {_tex(row['negative_or_boundary_case'])}"
        )
        result_and_evidence = (
            rf"\textbf{{Expected/code:}} {_tex(row['expected_result_or_code'])}\newline "
            rf"\textbf{{Evidence:}} {_tex(row['final_evidence_artifact'])}"
        )
        lines.extend(
            [
                " & ".join(
                    [
                        id_and_category,
                        _tex(row["normative_rule"]),
                        _tex(row["rationale_or_validity_threat"]),
                        _tex(row["implementation_control"]),
                        cases,
                        result_and_evidence,
                    ]
                )
                + r"\\",
                r"\addlinespace[2pt]",
            ]
        )
    lines.extend([r"\end{longtable}", r"\endgroup", r"\end{landscape}", ""])
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
