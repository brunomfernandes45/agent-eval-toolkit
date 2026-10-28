#!/usr/bin/env python3
"""Run the frozen thesis verification and onboarding-readiness campaign.

The campaign deliberately treats the toolkit as an external program.  It coordinates
quality, schema, coverage, metamorphic, mutation, build, wheel, readiness, package,
repeatability, privacy, and requirements gates, then publishes a content-addressed
evidence bundle.  A failed gate is evidence: the bundle is still finalized, but the
process exits nonzero.
"""

from __future__ import annotations

import argparse
import ast
import csv
import gzip
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

SOURCE_DATE_EPOCH_DEFAULT = 1_704_067_200
LINE_COVERAGE_THRESHOLD = 90.0
BRANCH_COVERAGE_THRESHOLD = 85.0
CRITICAL_COVERAGE_THRESHOLD = 100.0

DEFAULT_RESTRICTED_CANARIES = {
    "langfuse_input_sentinel": "SYNTHETIC PRIVATE INPUT LEAKAGE SENTINEL",
    "langfuse_output_sentinel": "SYNTHETIC PRIVATE OUTPUT LEAKAGE SENTINEL",
    "test_customer_body": "PRIVATE CUSTOMER MESSAGE",
    "test_generated_body": "PRIVATE GENERATED ANSWER",
    "captured_output_probe": "PRIVATE-CAPTURED-OUTPUT-CANARY",
}

SOURCE_ROOT_FILES = (
    "CONTRIBUTING.md",
    "LICENSE",
    "MANIFEST.in",
    "README.md",
    "SECURITY.md",
    "pyproject.toml",
    "uv.lock",
)
SOURCE_ROOT_DIRECTORIES = (
    "data/langfuse_samples",
    "data/mapping_samples",
    "data/readiness_samples",
    "docs",
    "schemas",
    "src",
    "templates",
    "tests",
    "verification",
)
TRANSIENT_DIRECTORY_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".uv-cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "mutation_results",
    "readiness_results",
}
TRANSIENT_SUFFIXES = {
    ".aux",
    ".coverage",
    ".fdb_latexmk",
    ".fls",
    ".log",
    ".out",
    ".pdf",
    ".pyc",
    ".pyo",
    ".synctex.gz",
    ".toc",
}

SUCCESS_ARTIFACT_NAMES = {
    "diagnostics.json",
    "enterprise_evaluation_report.md",
    "evaluation_readiness.json",
    "evaluation_package.json",
    "excluded_runs.json",
    "input_validation.json",
    "monitoring_summary.csv",
    "normalized_events.json",
    "pilot_report.md",
    "quarantined_runs.json",
    "redaction_report.json",
    "scorecard.json",
    "telemetry_coverage.csv",
    "telemetry_validation.json",
    "workflow_runs.json",
}

EVIDENCE_GATE_BY_BASENAME = {
    "build_reproducibility.json": "reproducible_builds",
    "coverage.xml": "test_coverage",
    "coverage_summary.json": "test_coverage",
    "metamorphic_results.csv": "metamorphic_tests",
    "mutation_results.csv": "targeted_mutations",
    "mutation_summary.json": "targeted_mutations",
    "package_verification.csv": "package_verification",
    "privacy_scan.json": "privacy_path_scan",
    "report_language_audit.json": "report_language_audit",
    "schema_validation.csv": "schema_validation",
}
RESULT_BUNDLE_HASH_EXCLUSIONS = {
    "bundle_index.json",
    "bundle_checksums.sha256",
}


@dataclass
class CommandCapture:
    label: str
    command: list[str]
    cwd: Path
    returncode: int
    stdout: str
    stderr: str


@dataclass
class PackageCase:
    case_id: str
    package_dir: Path
    workflow_config: Path
    raw_logs: Path
    evaluation_manifest: Path
    baseline_context: Path | None = None
    mapping_assessment: Path | None = None


@dataclass
class SchemaCheck:
    phase: str
    schema: str
    instance: str
    expected_valid: bool
    actual_valid: bool
    passed: bool
    diagnostic: str


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the complete frozen thesis verification campaign."
    )
    parser.add_argument("--wheel", required=True, help="Frozen wheel to install and verify.")
    parser.add_argument("--output-dir", required=True, help="Final evidence bundle directory.")
    parser.add_argument(
        "--source-date-epoch",
        type=int,
        default=SOURCE_DATE_EPOCH_DEFAULT,
        help="Fixed build epoch (default: 2024-01-01T00:00:00Z).",
    )
    parser.add_argument(
        "--restricted-canary-file",
        help="Optional JSON list or object of additional restricted canary values.",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return _sha256_bytes(encoded)


def _fingerprint(path: Path, logical_path: str) -> dict[str, Any]:
    return {
        "path": logical_path,
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-._")
    return normalized or "case"


def _relative_files(root: Path) -> list[Path]:
    return sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _normalize_sdist_archive(path: Path, source_date_epoch: int) -> None:
    """Canonicalize tar and gzip metadata after a setuptools sdist build."""
    members: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with tarfile.open(path, mode="r:gz") as source:
        for member in source.getmembers():
            content: bytes | None = None
            if member.isfile():
                extracted = source.extractfile(member)
                if extracted is None:
                    raise ValueError(f"Could not read sdist member: {member.name}")
                content = extracted.read()
            members.append((member, content))

    temporary = path.with_name(f".{path.name}.normalized")
    try:
        with temporary.open("wb") as raw_handle:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw_handle, mtime=source_date_epoch
            ) as gzip_handle:
                with tarfile.open(
                    fileobj=gzip_handle, mode="w", format=tarfile.PAX_FORMAT
                ) as destination:
                    for original, content in sorted(members, key=lambda item: item[0].name):
                        normalized = tarfile.TarInfo(original.name)
                        normalized.type = original.type
                        normalized.linkname = original.linkname
                        normalized.mode = original.mode
                        normalized.uid = 0
                        normalized.gid = 0
                        normalized.uname = ""
                        normalized.gname = ""
                        normalized.mtime = source_date_epoch
                        normalized.size = len(content) if content is not None else 0
                        destination.addfile(
                            normalized,
                            io.BytesIO(content) if content is not None else None,
                        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _is_transient_source(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(
        part in TRANSIENT_DIRECTORY_NAMES or part.endswith(".egg-info") for part in relative.parts
    ):
        return True
    name = path.name
    return name == ".DS_Store" or any(name.endswith(suffix) for suffix in TRANSIENT_SUFFIXES)


class VerificationCampaign:
    def __init__(
        self,
        *,
        repo_root: Path,
        wheel: Path,
        output_root: Path,
        stage_root: Path,
        temp_root: Path,
        source_date_epoch: int,
        canaries: dict[str, str],
    ) -> None:
        self.repo_root = repo_root
        self.wheel = wheel
        self.output_root = output_root
        self.stage_root = stage_root
        self.temp_root = temp_root
        self.source_date_epoch = source_date_epoch
        self.canaries = dict(sorted(canaries.items()))
        self.commands: list[CommandCapture] = []
        self.gates: dict[str, dict[str, Any]] = {}
        self.schema_checks: list[SchemaCheck] = []
        self.package_cases: list[PackageCase] = []
        self.path_aliases: dict[str, str] = {}
        self.capture_canary_matches: list[dict[str, str]] = []
        self.frozen_manifest: dict[str, Any] = {}
        self.result_bundle_sha256: str | None = None
        self.venv_root: Path | None = None
        self.venv_python: Path | None = None
        self._register_path(repo_root, "<repo>")
        self._register_path(stage_root, "<bundle>")
        self._register_path(temp_root, "<campaign-temp>")
        self._register_path(output_root, "<output>")
        self._register_path(Path.home(), "<home>")
        self._register_path(wheel, f"<supplied-wheel>/{wheel.name}")

    def _register_path(self, path: Path, alias: str) -> None:
        candidates = {str(path.expanduser())}
        try:
            candidates.add(str(path.expanduser().resolve()))
        except OSError:
            pass
        for candidate in candidates:
            if candidate and candidate != os.sep:
                self.path_aliases[candidate] = alias

    def _logical_path(self, path: Path) -> str:
        try:
            return path.relative_to(self.stage_root).as_posix()
        except ValueError:
            pass
        try:
            return f"<repo>/{path.relative_to(self.repo_root).as_posix()}"
        except ValueError:
            return self._sanitize_text(str(path), record_canaries=False)

    def _sanitize_text(self, value: str, *, record_canaries: bool) -> str:
        sanitized = value
        for canary_id, canary in self.canaries.items():
            variants = {
                canary,
                json.dumps(canary, ensure_ascii=False)[1:-1],
                json.dumps(canary, ensure_ascii=True)[1:-1],
            }
            for variant in variants:
                if variant and variant in sanitized:
                    if record_canaries:
                        match = {
                            "canary_id": canary_id,
                            "location": "captured_command_output",
                        }
                        if match not in self.capture_canary_matches:
                            self.capture_canary_matches.append(match)
                    sanitized = sanitized.replace(variant, f"<restricted-canary:{canary_id}>")
        for raw_path, alias in sorted(
            self.path_aliases.items(), key=lambda item: len(item[0]), reverse=True
        ):
            sanitized = sanitized.replace(raw_path, alias)
        sanitized = re.sub(
            r"(?:/private)?/var/folders/[^\s\"']*agent-eval-readiness-[^\s\"']+",
            "<fresh-wheel-temp>",
            sanitized,
        )
        return sanitized

    def _clean_environment(self, additions: dict[str, str] | None = None) -> dict[str, str]:
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        if additions:
            environment.update(additions)
        return environment

    def _run_command(
        self,
        label: str,
        command: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        rendered = [str(item) for item in command]
        effective_cwd = cwd or self.repo_root
        completed = subprocess.run(
            rendered,
            cwd=effective_cwd,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.commands.append(
            CommandCapture(
                label=label,
                command=rendered,
                cwd=effective_cwd,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        )
        return completed

    def _gate(
        self,
        name: str,
        passed: bool,
        *,
        summary: str,
        evidence: Sequence[str] = (),
        details: dict[str, Any] | None = None,
    ) -> None:
        self.gates[name] = {
            "passed": bool(passed),
            "summary": self._sanitize_text(summary, record_canaries=False),
            "evidence": sorted(set(evidence)),
            "details": details or {},
        }

    def _guard(self, name: str, operation: Callable[[], None]) -> None:
        try:
            operation()
        except Exception as exc:  # campaign failures must become retained evidence
            self._gate(
                name,
                False,
                summary=f"Gate raised {type(exc).__name__}: {exc}",
                details={"exception_type": type(exc).__name__},
            )

    def run(self) -> bool:
        self._guard("frozen_inputs", self._prepare_frozen_inputs)
        self._guard("quality", self._run_quality)
        self._guard("schema_validation", self._run_static_schema_validation)
        self._guard("test_coverage", self._run_tests_with_coverage)
        self._guard("metamorphic_tests", self._run_metamorphic_tests)
        self._guard("targeted_mutations", self._run_mutations)
        self._guard("reproducible_builds", self._run_reproducible_builds)
        self._guard("fresh_wheel_readiness", self._run_fresh_wheel_readiness)
        self._guard("cross_root_repeatability", self._run_cross_root_repeatability)
        self._guard("package_verification", self._run_package_verification)
        self._guard("schema_validation", self._run_output_schema_validation)
        self._guard("report_language_audit", self._run_report_language_audit)
        self._write_command_records()
        self._guard("privacy_path_scan", self._run_privacy_path_scan)
        self._guard("requirements_coverage", self._run_requirements_coverage)
        self._finalize_record_and_privacy()
        self._write_bundle_index_and_checksums()
        return all(gate["passed"] for gate in self.gates.values())

    def _selected_source_files(self) -> list[Path]:
        selected: set[Path] = set()
        for name in SOURCE_ROOT_FILES:
            candidate = self.repo_root / name
            if candidate.is_file():
                selected.add(candidate)
        experiments_root = self.repo_root / "experiments"
        if experiments_root.is_dir():
            selected.update(path for path in experiments_root.glob("*.py") if path.is_file())
        for directory in SOURCE_ROOT_DIRECTORIES:
            root = self.repo_root / directory
            if not root.is_dir():
                continue
            selected.update(
                path
                for path in root.rglob("*")
                if path.is_file() and not _is_transient_source(path, self.repo_root)
            )
        return sorted(selected, key=lambda path: path.relative_to(self.repo_root).as_posix())

    def _create_source_archive(self, destination: Path, source_files: Sequence[Path]) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as raw_handle:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw_handle, mtime=self.source_date_epoch
            ) as gzip_handle:
                with tarfile.open(fileobj=gzip_handle, mode="w", format=tarfile.PAX_FORMAT) as tar:
                    for path in source_files:
                        content = path.read_bytes()
                        logical = path.relative_to(self.repo_root).as_posix()
                        info = tarfile.TarInfo(logical)
                        info.size = len(content)
                        info.mtime = self.source_date_epoch
                        info.mode = 0o755 if os.access(path, os.X_OK) else 0o644
                        info.uid = 0
                        info.gid = 0
                        info.uname = ""
                        info.gname = ""
                        tar.addfile(info, io.BytesIO(content))

    def _category_fingerprints(
        self, source_files: Sequence[Path], predicate: Callable[[str], bool]
    ) -> dict[str, Any]:
        entries = [
            _fingerprint(path, path.relative_to(self.repo_root).as_posix())
            for path in source_files
            if predicate(path.relative_to(self.repo_root).as_posix())
        ]
        return {
            "file_count": len(entries),
            "combined_sha256": _canonical_hash(entries),
            "files": entries,
        }

    def _add_configured_canaries(self, source_files: Sequence[Path]) -> None:
        candidate_keys = {
            "restricted_canaries",
            "restricted_value_canaries",
            "redaction_canaries",
        }
        for path in source_files:
            logical = path.relative_to(self.repo_root).as_posix()
            if not path.name.endswith(".json") or "workflow" not in path.name:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            candidates = [payload.get(key) for key in candidate_keys if key in payload]
            privacy = payload.get("privacy")
            if isinstance(privacy, dict) and "restricted_canaries" in privacy:
                candidates.append(privacy["restricted_canaries"])
            for candidate_index, candidate in enumerate(candidates, start=1):
                if isinstance(candidate, list):
                    values = [(str(index), value) for index, value in enumerate(candidate, start=1)]
                elif isinstance(candidate, dict):
                    values = [(str(key), value) for key, value in candidate.items()]
                else:
                    continue
                for value_label, value in values:
                    if isinstance(value, str) and value:
                        identifier = _slug(f"config-{logical}-{candidate_index}-{value_label}")
                        self.canaries[identifier] = value
        self.canaries = dict(sorted(self.canaries.items()))

    def _prepare_frozen_inputs(self) -> None:
        source_files = self._selected_source_files()
        self._add_configured_canaries(source_files)
        source_entries = [
            _fingerprint(path, path.relative_to(self.repo_root).as_posix()) for path in source_files
        ]
        frozen_root = self.stage_root / "frozen_inputs"
        source_archive = frozen_root / "source" / "frozen_source.tar.gz"
        second_archive = self.temp_root / "frozen_source_second.tar.gz"
        self._create_source_archive(source_archive, source_files)
        self._create_source_archive(second_archive, source_files)
        archive_reproducible = source_archive.read_bytes() == second_archive.read_bytes()

        copies = {
            self.repo_root / "uv.lock": frozen_root / "uv.lock",
            self.repo_root / "pyproject.toml": frozen_root / "pyproject.toml",
            self.repo_root
            / "verification/FROZEN_VERIFICATION_PROTOCOL.md": (
                frozen_root / "verification/FROZEN_VERIFICATION_PROTOCOL.md"
            ),
            self.repo_root
            / "verification/requirements_matrix.csv": (
                frozen_root / "verification/requirements_matrix.csv"
            ),
            self.repo_root
            / "verification/critical_mutants.json": (
                frozen_root / "verification/critical_mutants.json"
            ),
            self.repo_root
            / "verification/critical_branches.json": (
                frozen_root / "verification/critical_branches.json"
            ),
            self.repo_root
            / "verification/coverage_scope.json": (
                frozen_root / "verification/coverage_scope.json"
            ),
            self.repo_root
            / "verification/validate_critical_branches.py": (
                frozen_root / "verification/validate_critical_branches.py"
            ),
            self.repo_root
            / "verification/defect_log.csv": (frozen_root / "verification/defect_log.csv"),
            self.repo_root
            / "verification/build_oracle_workbook.mjs": (
                frozen_root / "oracle/build_oracle_workbook.mjs"
            ),
            self.repo_root
            / "outputs/thesis_verification/implementation_independent_oracle.xlsx": (
                frozen_root / "oracle/oracle_workbook.xlsx"
            ),
            self.repo_root
            / "outputs/thesis_verification/implementation_independent_oracle.xlsx.inspect.ndjson": (
                frozen_root / "oracle/oracle_formula_inspection.ndjson"
            ),
        }
        missing = [self._logical_path(source) for source in copies if not source.is_file()]
        for source, destination in copies.items():
            if source.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        schemas_destination = frozen_root / "schemas"
        schemas_destination.mkdir(parents=True, exist_ok=True)
        for schema in sorted((self.repo_root / "schemas").glob("*.schema.json")):
            shutil.copy2(schema, schemas_destination / schema.name)
        supplied_destination = frozen_root / "distributions" / self.wheel.name
        supplied_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.wheel, supplied_destination)

        categories = {
            "fixtures": self._category_fingerprints(
                source_files,
                lambda name: name.startswith("data/")
                and (name.endswith(".json") or name.endswith(".jsonl")),
            ),
            "workflow_configurations": self._category_fingerprints(
                source_files,
                lambda name: (
                    "workflow" in Path(name).name
                    and name.endswith(".json")
                    and (name.startswith("data/") or name.startswith("templates/"))
                ),
            ),
            "evaluation_manifests": self._category_fingerprints(
                source_files,
                lambda name: (
                    "manifest" in Path(name).name
                    and name.endswith(".json")
                    and (name.startswith("data/") or name.startswith("templates/"))
                ),
            ),
            "schemas": self._category_fingerprints(
                source_files, lambda name: name.startswith("schemas/")
            ),
        }
        dependency_lock = (
            _fingerprint(self.repo_root / "uv.lock", "uv.lock")
            if (self.repo_root / "uv.lock").is_file()
            else None
        )
        protocol = self.repo_root / "verification/FROZEN_VERIFICATION_PROTOCOL.md"
        coverage_scope = self.repo_root / "verification/coverage_scope.json"
        matrix = self.repo_root / "verification/requirements_matrix.csv"
        oracle = (
            self.repo_root / "outputs/thesis_verification/implementation_independent_oracle.xlsx"
        )
        formula_export = Path(f"{oracle}.inspect.ndjson")
        self.frozen_manifest = {
            "freeze_schema_version": "1.0.0",
            "source_date_epoch": self.source_date_epoch,
            "selection_policy": {
                "root_files": list(SOURCE_ROOT_FILES),
                "root_directories": list(SOURCE_ROOT_DIRECTORIES),
                "transient_directories_excluded": sorted(TRANSIENT_DIRECTORY_NAMES),
                "transient_suffixes_excluded": sorted(TRANSIENT_SUFFIXES),
            },
            "source_tree": {
                "file_count": len(source_entries),
                "combined_sha256": _canonical_hash(source_entries),
                "files": source_entries,
                "archive": _fingerprint(
                    source_archive, "frozen_inputs/source/frozen_source.tar.gz"
                ),
                "archive_reproducible": archive_reproducible,
            },
            "dependency_lock": dependency_lock,
            "categories": categories,
            "protocol": _fingerprint(protocol, "verification/FROZEN_VERIFICATION_PROTOCOL.md"),
            "coverage_scope": _fingerprint(coverage_scope, "verification/coverage_scope.json"),
            "requirements_matrix": _fingerprint(matrix, "verification/requirements_matrix.csv"),
            "oracle_workbook": _fingerprint(
                oracle, "outputs/thesis_verification/implementation_independent_oracle.xlsx"
            ),
            "oracle_formula_export": _fingerprint(
                formula_export,
                "outputs/thesis_verification/implementation_independent_oracle.xlsx.inspect.ndjson",
            ),
            "supplied_wheel": _fingerprint(
                supplied_destination, f"frozen_inputs/distributions/{self.wheel.name}"
            ),
            "missing_required_inputs": missing,
            "reproducible_distributions": [],
        }
        _write_json(frozen_root / "freeze_manifest.json", self.frozen_manifest)
        passed = not missing and bool(source_files) and archive_reproducible
        self._gate(
            "frozen_inputs",
            passed,
            summary=(
                f"Content-addressed {len(source_files)} source files; deterministic source "
                f"archive comparison {'passed' if archive_reproducible else 'failed'}."
            ),
            evidence=[
                "frozen_inputs/freeze_manifest.json",
                "frozen_inputs/source/frozen_source.tar.gz",
                "frozen_inputs/oracle/oracle_workbook.xlsx",
            ],
            details={
                "source_file_count": len(source_files),
                "source_tree_sha256": _canonical_hash(source_entries),
                "source_archive_reproducible": archive_reproducible,
                "missing_required_input_count": len(missing),
            },
        )

    def _active_python_files(self) -> list[Path]:
        files: list[Path] = []
        for root_name in ("src", "tests"):
            root = self.repo_root / root_name
            files.extend(
                path
                for path in root.rglob("*.py")
                if not _is_transient_source(path, self.repo_root)
            )
        files.extend((self.repo_root / "experiments").glob("*.py"))
        return sorted(set(files), key=lambda path: path.relative_to(self.repo_root).as_posix())

    def _run_quality(self) -> None:
        python_files = self._active_python_files()
        targets = [str(path.relative_to(self.repo_root)) for path in python_files]
        checks = {
            "black": self._run_command(
                "quality-black",
                [sys.executable, "-m", "black", "--check", *targets],
            ),
            "ruff": self._run_command(
                "quality-ruff", [sys.executable, "-m", "ruff", "check", *targets]
            ),
            "mypy": self._run_command("quality-mypy", [sys.executable, "-m", "mypy", "src"]),
        }
        result = {
            "quality_schema_version": "1.0.0",
            "passed": all(item.returncode == 0 for item in checks.values()),
            "checks": {
                name: {"passed": item.returncode == 0, "returncode": item.returncode}
                for name, item in checks.items()
            },
            "python_file_count": len(python_files),
        }
        _write_json(self.stage_root / "quality/quality_summary.json", result)
        self._gate(
            "quality",
            result["passed"],
            summary=(
                "Black, Ruff, and mypy passed."
                if result["passed"]
                else "One or more Black, Ruff, or mypy checks failed."
            ),
            evidence=["quality/quality_summary.json"],
            details=result["checks"],
        )

    def _schema_validator(self, schema_name: str) -> tuple[Any, Any]:
        try:
            from jsonschema import Draft202012Validator, FormatChecker
        except ImportError as exc:
            raise RuntimeError("jsonschema is required for the frozen schema gate") from exc
        schema_path = self.repo_root / "schemas" / schema_name
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema, format_checker=FormatChecker()), schema

    def _validate_schema_instance(
        self,
        *,
        phase: str,
        schema_name: str,
        instance_name: str,
        payload: Any,
        expected_valid: bool = True,
    ) -> bool:
        validator, _schema = self._schema_validator(schema_name)
        errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
        actual_valid = not errors
        diagnostic = ""
        if errors:
            first = errors[0]
            json_path = "/".join(str(part) for part in first.absolute_path) or "<root>"
            diagnostic = (
                f"{len(errors)} error(s); first validator={first.validator} path={json_path}"
            )
        passed = actual_valid == expected_valid
        self.schema_checks.append(
            SchemaCheck(
                phase=phase,
                schema=schema_name,
                instance=instance_name,
                expected_valid=expected_valid,
                actual_valid=actual_valid,
                passed=passed,
                diagnostic=diagnostic,
            )
        )
        return passed

    def _validate_json_file(
        self,
        path: Path,
        schema_name: str,
        *,
        phase: str,
        expected_valid: bool = True,
        instance_name: str | None = None,
    ) -> bool:
        logical = instance_name or self._logical_path(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.schema_checks.append(
                SchemaCheck(
                    phase=phase,
                    schema=schema_name,
                    instance=logical,
                    expected_valid=expected_valid,
                    actual_valid=False,
                    passed=not expected_valid,
                    diagnostic="File is not readable valid JSON.",
                )
            )
            return not expected_valid
        return self._validate_schema_instance(
            phase=phase,
            schema_name=schema_name,
            instance_name=logical,
            payload=payload,
            expected_valid=expected_valid,
        )

    def _validate_jsonl_file(
        self,
        path: Path,
        schema_name: str,
        *,
        phase: str,
        require_any_invalid: bool = False,
    ) -> bool:
        invalid_count = 0
        row_count = 0
        for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw.strip():
                continue
            row_count += 1
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                invalid_count += 1
                if not require_any_invalid:
                    self.schema_checks.append(
                        SchemaCheck(
                            phase=phase,
                            schema=schema_name,
                            instance=f"{self._logical_path(path)}:{line_number}",
                            expected_valid=True,
                            actual_valid=False,
                            passed=False,
                            diagnostic="JSONL row is not valid JSON.",
                        )
                    )
                continue
            validator, _schema = self._schema_validator(schema_name)
            if list(validator.iter_errors(payload)):
                invalid_count += 1
            if not require_any_invalid:
                self._validate_schema_instance(
                    phase=phase,
                    schema_name=schema_name,
                    instance_name=f"{self._logical_path(path)}:{line_number}",
                    payload=payload,
                )
        if require_any_invalid:
            passed = invalid_count > 0
            self.schema_checks.append(
                SchemaCheck(
                    phase=phase,
                    schema=schema_name,
                    instance=self._logical_path(path),
                    expected_valid=False,
                    actual_valid=invalid_count == 0,
                    passed=passed,
                    diagnostic=(
                        f"Expected at least one schema rejection; {invalid_count} of "
                        f"{row_count} rows were rejected."
                    ),
                )
            )
            return passed
        return invalid_count == 0

    def _write_schema_results(self) -> None:
        output = self.stage_root / "schema/schema_validation.csv"
        _write_csv(
            output,
            (
                "phase",
                "schema",
                "instance",
                "expected_valid",
                "actual_valid",
                "passed",
                "diagnostic",
            ),
            (
                (
                    check.phase,
                    check.schema,
                    check.instance,
                    str(check.expected_valid).lower(),
                    str(check.actual_valid).lower(),
                    str(check.passed).lower(),
                    check.diagnostic,
                )
                for check in self.schema_checks
            ),
        )
        passed = bool(self.schema_checks) and all(check.passed for check in self.schema_checks)
        summary = {
            "schema_validation_schema_version": "1.0.0",
            "passed": passed,
            "check_count": len(self.schema_checks),
            "passed_check_count": sum(check.passed for check in self.schema_checks),
            "failed_check_count": sum(not check.passed for check in self.schema_checks),
            "phases": sorted({check.phase for check in self.schema_checks}),
        }
        _write_json(self.stage_root / "schema/schema_validation_summary.json", summary)
        self._gate(
            "schema_validation",
            passed,
            summary=(
                f"{summary['passed_check_count']} of {summary['check_count']} schema checks passed."
            ),
            evidence=[
                "schema/schema_validation.csv",
                "schema/schema_validation_summary.json",
            ],
            details=summary,
        )

    def _run_static_schema_validation(self) -> None:
        for schema_path in sorted((self.repo_root / "schemas").glob("*.schema.json")):
            validator, schema = self._schema_validator(schema_path.name)
            del validator
            self.schema_checks.append(
                SchemaCheck(
                    phase="meta_schema",
                    schema=schema_path.name,
                    instance=self._logical_path(schema_path),
                    expected_valid=True,
                    actual_valid=isinstance(schema, dict),
                    passed=isinstance(schema, dict),
                    diagnostic="",
                )
            )

        pack = self.repo_root / "data/readiness_samples/enterprise_readiness_pack"
        workflow_configs = [
            self.repo_root / "data/sample_inputs/pilot_workflow.json",
            *sorted(pack.glob("workflow*.json")),
            self.repo_root / "templates/workflow_config.template.json",
        ]
        manifests = [
            self.repo_root / "data/sample_inputs/evaluation_manifest.json",
            *sorted(pack.glob("manifest*.json")),
            *sorted((self.repo_root / "templates").glob("evaluation_manifest*.json")),
        ]
        mappings = [
            self.repo_root / "data/langfuse_samples/langfuse_mapping.approved.json",
            self.repo_root / "templates/langfuse_mapping.example.json",
        ]
        source_profiles = [
            self.repo_root / "data/mapping_samples/source_profile.complete.json",
            self.repo_root / "templates/source_profile.template.json",
        ]
        mapping_specs = [
            self.repo_root / "data/mapping_samples/mapping_spec.confirmed.json",
            self.repo_root / "templates/mapping_spec.template.json",
        ]
        for path in workflow_configs:
            self._validate_json_file(path, "workflow_config.schema.json", phase="static_input")
        for path in manifests:
            self._validate_json_file(path, "evaluation_manifest.schema.json", phase="static_input")
        for path in mappings:
            self._validate_json_file(path, "langfuse_mapping.schema.json", phase="static_input")
        for path in source_profiles:
            self._validate_json_file(path, "source_profile.schema.json", phase="static_input")
        for path in mapping_specs:
            self._validate_json_file(path, "mapping_spec.schema.json", phase="static_input")
        self._validate_jsonl_file(
            self.repo_root / "data/mapping_samples/mapping_activity.complete.jsonl",
            "mapping_activity.schema.json",
            phase="static_input",
        )

        invalid_by_schema = {
            "anomaly_malformed_timestamp.jsonl",
            "anomaly_negative_values.jsonl",
        }
        canonical_fixtures = [
            self.repo_root / "templates/canonical_event.example.jsonl",
            *sorted(pack.glob("*.jsonl")),
        ]
        for path in canonical_fixtures:
            self._validate_jsonl_file(
                path,
                "canonical_event.schema.json",
                phase="static_input",
                require_any_invalid=path.name in invalid_by_schema,
            )
        self._write_schema_results()

    def _critical_coverage_anchors(self, coverage: dict[str, Any]) -> list[dict[str, Any]]:
        spec_path = self.repo_root / "verification/critical_mutants.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        anchors: list[dict[str, Any]] = []
        for mutant in spec.get("mutants", []):
            source_path = self.repo_root / mutant["file"]
            source = source_path.read_text(encoding="utf-8")
            search = mutant["search"]
            occurrences = source.count(search)
            line_number = (
                source[: source.find(search)].count("\n") + 1 if occurrences == 1 else None
            )
            logical = source_path.relative_to(self.repo_root).as_posix()
            file_coverage = coverage.get("files", {}).get(logical, {})
            missing_lines = set(file_coverage.get("missing_lines", []))
            executed_lines = set(file_coverage.get("executed_lines", []))
            executed_branches = {
                tuple(branch) for branch in file_coverage.get("executed_branches", [])
            }
            missing_branches = {
                tuple(branch) for branch in file_coverage.get("missing_branches", [])
            }
            branch_arcs = sorted(
                branch
                for branch in executed_branches | missing_branches
                if line_number is not None and branch[0] == line_number
            )
            covered_arcs = [branch for branch in branch_arcs if branch in executed_branches]
            line_covered = (
                line_number is not None
                and line_number in executed_lines
                and line_number not in missing_lines
            )
            passed = occurrences == 1 and line_covered and len(covered_arcs) == len(branch_arcs)
            anchors.append(
                {
                    "mutant_id": mutant["id"],
                    "source_file": logical,
                    "source_line": line_number,
                    "search_occurrences": occurrences,
                    "line_covered": line_covered,
                    "branch_arc_count": len(branch_arcs),
                    "covered_branch_arc_count": len(covered_arcs),
                    "passed": passed,
                }
            )
        return anchors

    def _declared_critical_decisions(self, coverage: dict[str, Any]) -> list[dict[str, Any]]:
        manifest = json.loads(
            (self.repo_root / "verification/critical_branches.json").read_text(encoding="utf-8")
        )
        decisions: list[dict[str, Any]] = []
        coverage_files = coverage.get("files", {})
        for declaration in manifest.get("decisions", []):
            source_file = declaration["file"]
            entry = coverage_files.get(source_file, {})
            executed = {
                tuple(arc)
                for arc in entry.get("executed_branches", [])
                if isinstance(arc, list) and len(arc) == 2
            }
            expected = {(arc["from"], arc["to"]) for arc in declaration.get("expected_arcs", [])}
            covered = expected & executed
            decisions.append(
                {
                    "decision_id": declaration["id"],
                    "source_file": source_file,
                    "source_line": declaration["decision_line"],
                    "expected_arc_count": len(expected),
                    "covered_arc_count": len(covered),
                    "passed": bool(expected) and covered == expected,
                }
            )
        return decisions

    def _run_tests_with_coverage(self) -> None:
        coverage_root = self.stage_root / "coverage"
        coverage_root.mkdir(parents=True, exist_ok=True)
        coverage_data = self.temp_root / ".coverage"
        environment = self._clean_environment(
            {
                "COVERAGE_FILE": str(coverage_data),
                "PYTHONPATH": os.pathsep.join((str(self.repo_root / "src"), str(self.repo_root))),
            }
        )
        test_run = self._run_command(
            "coverage-tests",
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                "--branch",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
            ],
            environment=environment,
        )
        json_result = self._run_command(
            "coverage-json",
            [
                sys.executable,
                "-m",
                "coverage",
                "json",
                "--fail-under=0",
                "-o",
                coverage_root / "coverage.json",
            ],
            environment=environment,
        )
        xml_result = self._run_command(
            "coverage-xml",
            [
                sys.executable,
                "-m",
                "coverage",
                "xml",
                "--fail-under=0",
                "-o",
                coverage_root / "coverage.xml",
            ],
            environment=environment,
        )
        critical_result = self._run_command(
            "critical-branch-validator",
            [
                sys.executable,
                "verification/validate_critical_branches.py",
                "--manifest",
                "verification/critical_branches.json",
                "--mutants",
                "verification/critical_mutants.json",
                "--coverage-json",
                coverage_root / "coverage.json",
            ],
            environment=environment,
        )
        try:
            critical_validation = json.loads(critical_result.stdout)
        except json.JSONDecodeError:
            critical_validation = {
                "passed": False,
                "error_count": 1,
                "errors": ["critical branch validator did not emit valid JSON"],
            }
        _write_json(coverage_root / "critical_branch_validation.json", critical_validation)
        coverage_path = coverage_root / "coverage.json"
        if coverage_path.is_file():
            coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
            totals = coverage.get("totals", {})
        else:
            coverage = {}
            totals = {}
        line_percent = float(totals.get("percent_statements_covered", 0.0))
        branch_percent = float(totals.get("percent_branches_covered", 0.0))
        coverage_scope_path = self.repo_root / "verification/coverage_scope.json"
        coverage_scope = json.loads(coverage_scope_path.read_text(encoding="utf-8"))
        anchors = self._critical_coverage_anchors(coverage) if coverage else []
        decisions = self._declared_critical_decisions(coverage) if coverage else []
        expected_arc_count = sum(item["expected_arc_count"] for item in decisions)
        covered_arc_count = sum(item["covered_arc_count"] for item in decisions)
        critical_percent = (
            100.0 * covered_arc_count / expected_arc_count if expected_arc_count else 0.0
        )
        critical_validator_passed = (
            critical_result.returncode == 0 and critical_validation.get("passed") is True
        )
        passed = (
            test_run.returncode == 0
            and json_result.returncode == 0
            and xml_result.returncode == 0
            and line_percent >= LINE_COVERAGE_THRESHOLD
            and branch_percent >= BRANCH_COVERAGE_THRESHOLD
            and critical_percent >= CRITICAL_COVERAGE_THRESHOLD
            and critical_validator_passed
        )
        summary = {
            "coverage_summary_schema_version": "2.0.0",
            "coverage_scope": {
                "scope_id": coverage_scope["scope_id"],
                "description": coverage_scope["description"],
                "manifest": "verification/coverage_scope.json",
            },
            "passed": passed,
            "tests_passed": test_run.returncode == 0,
            "thresholds": {
                "line_percent": LINE_COVERAGE_THRESHOLD,
                "branch_percent": BRANCH_COVERAGE_THRESHOLD,
                "critical_decision_branch_percent": CRITICAL_COVERAGE_THRESHOLD,
            },
            "actual": {
                "line_percent": round(line_percent, 3),
                "branch_percent": round(branch_percent, 3),
                "critical_decision_branch_percent": round(critical_percent, 3),
                "statement_count": totals.get("num_statements", 0),
                "branch_count": totals.get("num_branches", 0),
            },
            "structural_anchor_count": len(anchors),
            "structural_anchor_pass_count": sum(anchor["passed"] for anchor in anchors),
            "structural_anchors": anchors,
            "critical_branch_manifest": "verification/critical_branches.json",
            "critical_branch_validator_passed": critical_validator_passed,
            "critical_decision_count": len(decisions),
            "critical_decision_pass_count": sum(item["passed"] for item in decisions),
            "critical_expected_arc_count": expected_arc_count,
            "critical_covered_arc_count": covered_arc_count,
            "critical_decisions": decisions,
        }
        _write_json(coverage_root / "coverage_summary.json", summary)
        xml_path = coverage_root / "coverage.xml"
        if xml_path.is_file():
            sanitized_xml = self._sanitize_text(
                xml_path.read_text(encoding="utf-8"), record_canaries=True
            )
            for alias in set(self.path_aliases.values()):
                sanitized_xml = sanitized_xml.replace(alias, html.escape(alias))
            xml_path.write_text(sanitized_xml, encoding="utf-8")
        self._gate(
            "test_coverage",
            passed,
            summary=(
                f"Tests {'passed' if test_run.returncode == 0 else 'failed'}; frozen-core line "
                f"coverage {line_percent:.3f}%, branch coverage {branch_percent:.3f}%, "
                f"critical anchors {critical_percent:.3f}%."
            ),
            evidence=[
                "coverage/coverage.json",
                "coverage/coverage.xml",
                "coverage/coverage_summary.json",
                "coverage/critical_branch_validation.json",
                "frozen_inputs/verification/coverage_scope.json",
            ],
            details=summary["actual"],
        )

    def _metamorphic_test_ids(self) -> list[str]:
        path = self.repo_root / "tests/test_metamorphic.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        identifiers: list[str] = []
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or not node.name.startswith("Metamorphic"):
                continue
            for member in node.body:
                if isinstance(
                    member, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and member.name.startswith("test_"):
                    identifiers.append(f"tests.test_metamorphic.{node.name}.{member.name}")
        return sorted(identifiers)

    def _run_metamorphic_tests(self) -> None:
        rows: list[tuple[str, int, str]] = []
        environment = self._clean_environment(
            {"PYTHONPATH": os.pathsep.join((str(self.repo_root / "src"), str(self.repo_root)))}
        )
        for test_id in self._metamorphic_test_ids():
            completed = self._run_command(
                f"metamorphic-{test_id.rsplit('.', 1)[-1]}",
                [sys.executable, "-m", "unittest", test_id],
                environment=environment,
            )
            rows.append(
                (test_id, completed.returncode, "passed" if completed.returncode == 0 else "failed")
            )
        output = self.stage_root / "metamorphic/metamorphic_results.csv"
        _write_csv(output, ("test_id", "returncode", "status"), rows)
        passed = bool(rows) and all(row[1] == 0 for row in rows)
        summary = {
            "metamorphic_schema_version": "1.0.0",
            "passed": passed,
            "relation_count": len(rows),
            "passed_relation_count": sum(row[1] == 0 for row in rows),
        }
        _write_json(self.stage_root / "metamorphic/metamorphic_summary.json", summary)
        self._gate(
            "metamorphic_tests",
            passed,
            summary=(
                f"{summary['passed_relation_count']} of {len(rows)} metamorphic "
                "relations passed."
            ),
            evidence=[
                "metamorphic/metamorphic_results.csv",
                "metamorphic/metamorphic_summary.json",
            ],
            details=summary,
        )

    def _run_mutations(self) -> None:
        output = self.stage_root / "mutation"
        completed = self._run_command(
            "targeted-mutation-campaign",
            [
                sys.executable,
                "experiments/run_targeted_mutation_campaign.py",
                "--spec",
                "verification/critical_mutants.json",
                "--output-dir",
                output,
                "--overwrite",
            ],
            environment=self._clean_environment(),
        )
        summary_path = output / "mutation_summary.json"
        summary = (
            json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
        )
        passed = completed.returncode == 0 and summary.get("overall_status") == "passed"
        self._gate(
            "targeted_mutations",
            passed,
            summary=(
                f"Killed {summary.get('killed_critical_mutant_count', 0)} of "
                f"{summary.get('declared_critical_mutant_count', 0)} declared critical mutants."
            ),
            evidence=[
                "mutation/mutation_results.csv",
                "mutation/mutation_summary.json",
                "mutation/mutation_diagnostics.json",
            ],
            details={
                "returncode": completed.returncode,
                "surviving_critical_mutant_count": summary.get("surviving_critical_mutant_count"),
                "invalid_mutant_spec_count": summary.get("invalid_mutant_spec_count"),
            },
        )

    def _copy_build_source(self, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        for name in ("LICENSE", "MANIFEST.in", "README.md", "pyproject.toml"):
            source = self.repo_root / name
            if source.is_file():
                shutil.copy2(source, destination / name)
        for name in ("docs", "schemas", "src", "templates"):
            source = self.repo_root / name
            if source.is_dir():
                shutil.copytree(
                    source,
                    destination / name,
                    ignore=shutil.ignore_patterns(
                        "__pycache__", "*.pyc", ".DS_Store", "*.egg-info"
                    ),
                )

    def _run_reproducible_builds(self) -> None:
        build_root = self.stage_root / "build_reproducibility"
        source_a = self.temp_root / "build-source-a"
        source_b = self.temp_root / "build-source-b"
        self._register_path(source_a, "<build-root-a>")
        self._register_path(source_b, "<build-root-b>")
        self._copy_build_source(source_a)
        self._copy_build_source(source_b)
        environment = self._clean_environment(
            {
                "SOURCE_DATE_EPOCH": str(self.source_date_epoch),
                "PYTHONHASHSEED": "0",
            }
        )
        executions = []
        for label, source_root, output in (
            ("a", source_a, build_root / "run_a"),
            ("b", source_b, build_root / "run_b"),
        ):
            output.mkdir(parents=True, exist_ok=True)
            executions.append(
                self._run_command(
                    f"fixed-epoch-build-{label}",
                    [
                        sys.executable,
                        "-m",
                        "build",
                        "--no-isolation",
                        "--outdir",
                        output,
                    ],
                    cwd=source_root,
                    environment=environment,
                )
            )
            for sdist in output.glob("*.tar.gz"):
                _normalize_sdist_archive(sdist, self.source_date_epoch)
        files_a = {path.name: path for path in _relative_files(build_root / "run_a")}
        files_b = {path.name: path for path in _relative_files(build_root / "run_b")}
        artifact_names_match = set(files_a) == set(files_b) and bool(files_a)
        rows = []
        for name in sorted(set(files_a) | set(files_b)):
            left = _sha256_file(files_a[name]) if name in files_a else None
            right = _sha256_file(files_b[name]) if name in files_b else None
            rows.append(
                {
                    "artifact": name,
                    "run_a_sha256": left,
                    "run_b_sha256": right,
                    "identical": left is not None and left == right,
                }
            )
        reproduced_wheel = next(
            (path for name, path in files_a.items() if name.endswith(".whl")), None
        )
        supplied_matches = (
            reproduced_wheel is not None
            and self.wheel.name == reproduced_wheel.name
            and _sha256_file(self.wheel) == _sha256_file(reproduced_wheel)
        )
        passed = (
            all(execution.returncode == 0 for execution in executions)
            and artifact_names_match
            and all(row["identical"] for row in rows)
            and supplied_matches
        )
        result = {
            "build_reproducibility_schema_version": "1.0.0",
            "passed": passed,
            "source_date_epoch": self.source_date_epoch,
            "build_returncodes": [execution.returncode for execution in executions],
            "artifact_names_match": artifact_names_match,
            "artifacts": rows,
            "supplied_wheel_matches_reproduced_wheel": supplied_matches,
            "supplied_wheel_sha256": _sha256_file(self.wheel),
        }
        _write_json(build_root / "build_reproducibility.json", result)
        reproduced = []
        for run_name in ("run_a", "run_b"):
            for path in _relative_files(build_root / run_name):
                reproduced.append(
                    _fingerprint(
                        path,
                        (
                            f"build_reproducibility/{run_name}/"
                            f"{path.relative_to(build_root / run_name).as_posix()}"
                        ),
                    )
                )
        self.frozen_manifest["reproducible_distributions"] = reproduced
        _write_json(self.stage_root / "frozen_inputs/freeze_manifest.json", self.frozen_manifest)
        self._gate(
            "reproducible_builds",
            passed,
            summary=(
                f"Compared {len(rows)} fixed-epoch distribution artifacts; supplied wheel "
                f"match {'passed' if supplied_matches else 'failed'}."
            ),
            evidence=["build_reproducibility/build_reproducibility.json"],
            details={
                "distribution_count": len(rows),
                "supplied_wheel_matches": supplied_matches,
            },
        )

    def _venv_executable(self, root: Path, executable: str) -> Path:
        directory = root / ("Scripts" if os.name == "nt" else "bin")
        suffix = ".exe" if os.name == "nt" else ""
        return directory / f"{executable}{suffix}"

    @staticmethod
    def _option_value(command: Sequence[str], option: str) -> str | None:
        try:
            return command[command.index(option) + 1]
        except (ValueError, IndexError):
            return None

    def _resolve_recorded_path(self, value: str | None) -> Path | None:
        if value is None:
            return None
        if value.startswith("<repo>/"):
            return self.repo_root / value.removeprefix("<repo>/")
        path = Path(value)
        return path if path.is_absolute() else self.repo_root / path

    def _collect_readiness_package_cases(self, readiness_root: Path) -> None:
        for package_path in sorted(readiness_root.rglob("evaluation_package.json")):
            package_dir = package_path.parent
            execution_path = package_dir.with_name(f"{package_dir.name}_execution.json")
            if not execution_path.is_file():
                continue
            execution = json.loads(execution_path.read_text(encoding="utf-8"))
            command = execution.get("command", [])
            if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
                continue
            config = self._resolve_recorded_path(self._option_value(command, "--workflow-config"))
            logs = self._resolve_recorded_path(self._option_value(command, "--raw-logs"))
            manifest = self._resolve_recorded_path(
                self._option_value(command, "--evaluation-manifest")
            )
            baseline = self._resolve_recorded_path(
                self._option_value(command, "--baseline-context")
            )
            mapping_assessment = self._resolve_recorded_path(
                self._option_value(command, "--mapping-assessment")
            )
            if config is None or logs is None or manifest is None:
                continue
            self.package_cases.append(
                PackageCase(
                    case_id=package_path.relative_to(self.stage_root).parent.as_posix(),
                    package_dir=package_dir,
                    workflow_config=config,
                    raw_logs=logs,
                    evaluation_manifest=manifest,
                    baseline_context=baseline,
                    mapping_assessment=mapping_assessment,
                )
            )

    def _sanitize_json_tree(self, root: Path) -> None:
        for path in _relative_files(root):
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            sanitized = self._sanitize_text(content, record_canaries=True)
            if sanitized != content:
                path.write_text(sanitized, encoding="utf-8")

    def _run_fresh_wheel_readiness(self) -> None:
        self.venv_root = self.temp_root / "fresh-wheel-venv"
        self._register_path(self.venv_root, "<fresh-wheel-venv>")
        create = self._run_command(
            "fresh-wheel-create-venv",
            [sys.executable, "-m", "venv", self.venv_root],
            environment=self._clean_environment(),
        )
        self.venv_python = self._venv_executable(self.venv_root, "python")
        clean_environment = self._clean_environment()
        install = self._run_command(
            "fresh-wheel-install",
            [self.venv_python, "-m", "pip", "install", "--no-deps", self.wheel],
            environment=clean_environment,
        )
        pip_check = self._run_command(
            "fresh-wheel-pip-check",
            [self.venv_python, "-m", "pip", "check"],
            environment=clean_environment,
        )
        import_check = self._run_command(
            "fresh-wheel-import-location",
            [
                self.venv_python,
                "-c",
                (
                    "import agent_eval_toolkit,pathlib;"
                    "print(pathlib.Path(agent_eval_toolkit.__file__).resolve())"
                ),
            ],
            environment=clean_environment,
        )
        templates = self._run_command(
            "fresh-wheel-list-templates",
            [self.venv_python, "-m", "agent_eval_toolkit", "list-templates"],
            environment=clean_environment,
        )
        readiness_root = self.stage_root / "readiness"
        toolkit_command = f"{self.venv_python} -m agent_eval_toolkit"
        readiness = self._run_command(
            "fresh-wheel-readiness-suite",
            [
                sys.executable,
                "experiments/run_readiness_suite.py",
                "--toolkit-command",
                toolkit_command,
                "--output-dir",
                readiness_root,
                "--overwrite",
            ],
            environment=clean_environment,
        )
        self._collect_readiness_package_cases(readiness_root)
        self._sanitize_json_tree(readiness_root)
        summary_path = readiness_root / "readiness_summary.json"
        summary = (
            json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
        )
        installed_location = (
            Path(import_check.stdout.strip()) if import_check.stdout.strip() else None
        )
        installed_from_venv = (
            installed_location is not None
            and self.venv_root.resolve() in installed_location.resolve().parents
        )
        installed_schemas = sorted((self.venv_root / "schemas").glob("*.schema.json"))
        passed = (
            create.returncode == 0
            and install.returncode == 0
            and pip_check.returncode == 0
            and import_check.returncode == 0
            and installed_from_venv
            and templates.returncode == 0
            and len(installed_schemas)
            == len(list((self.repo_root / "schemas").glob("*.schema.json")))
            and readiness.returncode == 0
            and summary.get("overall_status") == "passed"
        )
        wheel_result = {
            "fresh_wheel_schema_version": "1.0.0",
            "passed": passed,
            "wheel_sha256": _sha256_file(self.wheel),
            "create_venv_returncode": create.returncode,
            "install_returncode": install.returncode,
            "pip_check_returncode": pip_check.returncode,
            "installed_from_fresh_venv": installed_from_venv,
            "installed_schema_count": len(installed_schemas),
            "template_command_returncode": templates.returncode,
            "readiness_returncode": readiness.returncode,
            "readiness_overall_status": summary.get("overall_status"),
            "successful_package_count": len(self.package_cases),
        }
        _write_json(self.stage_root / "fresh_wheel/fresh_wheel_summary.json", wheel_result)
        self._gate(
            "fresh_wheel_readiness",
            passed,
            summary=(
                f"Fresh wheel installation and readiness produced "
                f"{len(self.package_cases)} successful packages; readiness status "
                f"{summary.get('overall_status', 'unavailable')}."
            ),
            evidence=[
                "fresh_wheel/fresh_wheel_summary.json",
                "readiness/readiness_summary.json",
                "readiness/readiness_matrix.csv",
            ],
            details=wheel_result,
        )

    def _run_cross_root_repeatability(self) -> None:
        if self.venv_python is None or not self.venv_python.is_file():
            self._gate(
                "cross_root_repeatability",
                False,
                summary="Fresh-wheel interpreter is unavailable; cross-root run could not execute.",
            )
            return
        pack = self.repo_root / "data/readiness_samples/enterprise_readiness_pack"
        stored_inputs = self.stage_root / "cross_root/inputs"
        stored_inputs.mkdir(parents=True, exist_ok=True)
        input_sources = {
            "workflow_config.json": pack / "workflow_config.json",
            "complete_telemetry.jsonl": pack / "complete_telemetry.jsonl",
            "manifest_complete.json": pack / "manifest_complete.json",
        }
        for name, source in input_sources.items():
            shutil.copy2(source, stored_inputs / name)
        roots = [self.temp_root / "workspace-root-a", self.temp_root / "workspace-root-b"]
        executions = []
        outputs = []
        for index, root in enumerate(roots):
            alias = f"<cross-root-{'a' if index == 0 else 'b'}>"
            self._register_path(root, alias)
            inputs = root / "inputs"
            inputs.mkdir(parents=True)
            for name, source in input_sources.items():
                shutil.copy2(source, inputs / name)
            shutil.copy2(self.repo_root / "uv.lock", root / "uv.lock")
            output = root / "results"
            executions.append(
                self._run_command(
                    f"cross-root-run-{'a' if index == 0 else 'b'}",
                    [
                        self.venv_python,
                        "-m",
                        "agent_eval_toolkit",
                        "run",
                        "--workflow-config",
                        inputs / "workflow_config.json",
                        "--raw-logs",
                        inputs / "complete_telemetry.jsonl",
                        "--evaluation-manifest",
                        inputs / "manifest_complete.json",
                        "--output-dir",
                        output,
                    ],
                    cwd=root,
                    environment=self._clean_environment(),
                )
            )
            outputs.append(output)

        names_a = {
            path.relative_to(outputs[0]).as_posix(): path for path in _relative_files(outputs[0])
        }
        names_b = {
            path.relative_to(outputs[1]).as_posix(): path for path in _relative_files(outputs[1])
        }
        differences: list[str] = []
        for name in sorted(set(names_a) | set(names_b)):
            if name not in names_a or name not in names_b:
                differences.append(name)
            elif names_a[name].read_bytes() != names_b[name].read_bytes():
                differences.append(name)
        roots_absent = True
        for output in outputs:
            for path in _relative_files(output):
                content = path.read_bytes()
                roots_absent &= all(str(root).encode("utf-8") not in content for root in roots)

        copied_outputs = []
        for index, output in enumerate(outputs):
            destination = self.stage_root / f"cross_root/run_{'a' if index == 0 else 'b'}"
            if output.is_dir():
                shutil.copytree(output, destination)
            copied_outputs.append(destination)
            if (destination / "evaluation_package.json").is_file():
                self.package_cases.append(
                    PackageCase(
                        case_id=f"cross_root/run_{'a' if index == 0 else 'b'}",
                        package_dir=destination,
                        workflow_config=stored_inputs / "workflow_config.json",
                        raw_logs=stored_inputs / "complete_telemetry.jsonl",
                        evaluation_manifest=stored_inputs / "manifest_complete.json",
                    )
                )
        passed = (
            all(execution.returncode == 0 for execution in executions)
            and bool(names_a)
            and not differences
            and roots_absent
        )
        result = {
            "cross_root_schema_version": "1.0.0",
            "passed": passed,
            "run_returncodes": [execution.returncode for execution in executions],
            "file_count": len(names_a),
            "byte_identical": not differences and set(names_a) == set(names_b),
            "differing_files": differences,
            "workspace_root_strings_absent": roots_absent,
        }
        _write_json(self.stage_root / "cross_root/cross_root_repeatability.json", result)
        _write_csv(
            self.stage_root / "cross_root/cross_root_repeatability.csv",
            ("check", "expected", "actual", "passed"),
            (
                (
                    "run_exit_codes",
                    "0;0",
                    ";".join(map(str, result["run_returncodes"])),
                    all(item == 0 for item in result["run_returncodes"]),
                ),
                (
                    "byte_identical",
                    "true",
                    str(result["byte_identical"]).lower(),
                    result["byte_identical"],
                ),
                ("workspace_root_strings_absent", "true", str(roots_absent).lower(), roots_absent),
            ),
        )
        self._gate(
            "cross_root_repeatability",
            passed,
            summary=(
                f"Cross-root comparison covered {len(names_a)} files with "
                f"{len(differences)} differences."
            ),
            evidence=[
                "cross_root/cross_root_repeatability.json",
                "cross_root/cross_root_repeatability.csv",
            ],
            details={
                "file_count": len(names_a),
                "difference_count": len(differences),
                "root_strings_absent": roots_absent,
            },
        )

    def _verification_command(
        self,
        case: PackageCase,
        *,
        package_dir: Path | None = None,
        raw_logs: Path | None = None,
        schema_dir: Path | None = None,
    ) -> list[str | Path]:
        assert self.venv_python is not None
        command: list[str | Path] = [
            self.venv_python,
            "-m",
            "agent_eval_toolkit",
            "verify-package",
            "--package-dir",
            package_dir or case.package_dir,
            "--workflow-config",
            case.workflow_config,
            "--raw-logs",
            raw_logs or case.raw_logs,
            "--evaluation-manifest",
            case.evaluation_manifest,
        ]
        if case.baseline_context is not None:
            command.extend(("--baseline-context", case.baseline_context))
        if case.mapping_assessment is not None:
            command.extend(("--mapping-assessment", case.mapping_assessment))
        if schema_dir is not None:
            command.extend(("--schemas-dir", schema_dir))
        return command

    @staticmethod
    def _parse_json_stdout(completed: subprocess.CompletedProcess[str]) -> dict[str, Any] | None:
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _run_verification_probe(
        self,
        *,
        case: PackageCase,
        probe: str,
        package_dir: Path | None = None,
        raw_logs: Path | None = None,
        schema_dir: Path | None = None,
        expected_pass: bool,
        expected_failed_checks: set[str],
    ) -> tuple[bool, dict[str, Any] | None, int]:
        completed = self._run_command(
            f"verify-{_slug(case.case_id)}-{probe}",
            self._verification_command(
                case, package_dir=package_dir, raw_logs=raw_logs, schema_dir=schema_dir
            ),
            environment=self._clean_environment(),
        )
        payload = self._parse_json_stdout(completed)
        checks = payload.get("checks", {}) if payload else {}
        failed_checks = {
            name
            for name, detail in checks.items()
            if isinstance(detail, dict) and not detail.get("passed", False)
        }
        expected_returncode = 0 if expected_pass else 1
        passed = (
            payload is not None
            and bool(payload.get("passed")) == expected_pass
            and completed.returncode == expected_returncode
            and expected_failed_checks <= failed_checks
        )
        return passed, payload, completed.returncode

    @staticmethod
    def _tamper_first_jsonl_line(source: Path, destination: Path) -> None:
        lines = source.read_bytes().splitlines(keepends=True)
        for index, line in enumerate(lines):
            stripped_newline = line.rstrip(b"\r\n")
            newline = line[len(stripped_newline) :]
            if stripped_newline.strip():
                lines[index] = stripped_newline + b" " + newline
                break
        destination.write_bytes(b"".join(lines))

    def _run_package_verification(self) -> None:
        if self.venv_python is None or not self.venv_python.is_file():
            self._gate(
                "package_verification",
                False,
                summary="Fresh-wheel interpreter is unavailable; packages were not verified.",
            )
            return
        rows: list[dict[str, Any]] = []
        results_root = self.stage_root / "package_verification/results"
        probe_root = self.temp_root / "package-probes"
        unique_cases = {case.case_id: case for case in self.package_cases}
        for case_id, case in sorted(unique_cases.items()):
            case_result_root = results_root / _slug(case_id)
            baseline_passed, baseline_payload, baseline_returncode = self._run_verification_probe(
                case=case,
                probe="baseline",
                expected_pass=True,
                expected_failed_checks=set(),
            )
            if baseline_payload is not None:
                _write_json(case_result_root / "baseline.json", baseline_payload)
            rows.append(
                {
                    "package": case_id,
                    "probe": "baseline",
                    "expected_verifier_pass": True,
                    "returncode": baseline_returncode,
                    "passed": baseline_passed,
                    "expected_failed_checks": "",
                }
            )

            raw_tampered = probe_root / _slug(case_id) / "raw_line.jsonl"
            raw_tampered.parent.mkdir(parents=True, exist_ok=True)
            self._tamper_first_jsonl_line(case.raw_logs, raw_tampered)
            probe_passed, payload, returncode = self._run_verification_probe(
                case=case,
                probe="raw-line-whitespace",
                raw_logs=raw_tampered,
                expected_pass=False,
                expected_failed_checks={"input_hashes", "raw_line_hashes"},
            )
            if payload is not None:
                _write_json(case_result_root / "raw_line_whitespace.json", payload)
                probe_passed &= bool(
                    payload.get("checks", {}).get("event_hashes", {}).get("passed")
                )
            rows.append(
                {
                    "package": case_id,
                    "probe": "raw_line_whitespace",
                    "expected_verifier_pass": False,
                    "returncode": returncode,
                    "passed": probe_passed,
                    "expected_failed_checks": "input_hashes;raw_line_hashes",
                }
            )

            for probe, mutation, expected_checks in (
                ("artifact_modified", "modify", {"artifact_hashes"}),
                ("artifact_missing", "delete", {"artifact_hashes"}),
                ("bundle_hash_modified", "bundle", {"bundle_hash"}),
            ):
                copied = probe_root / _slug(case_id) / probe
                if copied.exists():
                    shutil.rmtree(copied)
                shutil.copytree(case.package_dir, copied)
                if mutation in {"modify", "delete"}:
                    target = copied / "scorecard.json"
                    if mutation == "modify":
                        target.write_bytes(target.read_bytes() + b"\n")
                    else:
                        target.unlink()
                else:
                    package_path = copied / "evaluation_package.json"
                    package_payload = json.loads(package_path.read_text(encoding="utf-8"))
                    package_payload["bundle_sha256"] = "0" * 64
                    _write_json(package_path, package_payload)
                probe_passed, payload, returncode = self._run_verification_probe(
                    case=case,
                    probe=probe,
                    package_dir=copied,
                    expected_pass=False,
                    expected_failed_checks=expected_checks,
                )
                if payload is not None:
                    _write_json(case_result_root / f"{probe}.json", payload)
                rows.append(
                    {
                        "package": case_id,
                        "probe": probe,
                        "expected_verifier_pass": False,
                        "returncode": returncode,
                        "passed": probe_passed,
                        "expected_failed_checks": ";".join(sorted(expected_checks)),
                    }
                )

            schemas = probe_root / _slug(case_id) / "schemas"
            if schemas.exists():
                shutil.rmtree(schemas)
            shutil.copytree(self.repo_root / "schemas", schemas)
            schema_target = next(iter(sorted(schemas.glob("*.schema.json"))))
            schema_target.write_bytes(schema_target.read_bytes() + b"\n")
            probe_passed, payload, returncode = self._run_verification_probe(
                case=case,
                probe="schema_modified",
                schema_dir=schemas,
                expected_pass=False,
                expected_failed_checks={"schema_hashes"},
            )
            if payload is not None:
                _write_json(case_result_root / "schema_modified.json", payload)
            rows.append(
                {
                    "package": case_id,
                    "probe": "schema_modified",
                    "expected_verifier_pass": False,
                    "returncode": returncode,
                    "passed": probe_passed,
                    "expected_failed_checks": "schema_hashes",
                }
            )

        _write_csv(
            self.stage_root / "package_verification/package_verification.csv",
            (
                "package",
                "probe",
                "expected_verifier_pass",
                "returncode",
                "expected_failed_checks",
                "passed",
            ),
            (
                (
                    row["package"],
                    row["probe"],
                    str(row["expected_verifier_pass"]).lower(),
                    row["returncode"],
                    row["expected_failed_checks"],
                    str(row["passed"]).lower(),
                )
                for row in rows
            ),
        )
        package_count = len(unique_cases)
        expected_probe_count = package_count * 6
        passed = (
            package_count > 0
            and len(rows) == expected_probe_count
            and all(row["passed"] for row in rows)
        )
        summary = {
            "package_campaign_schema_version": "1.0.0",
            "passed": passed,
            "successful_package_count": package_count,
            "verification_count": len(rows),
            "expected_verification_count": expected_probe_count,
            "passed_verification_count": sum(row["passed"] for row in rows),
            "tamper_probe_count": sum(row["probe"] != "baseline" for row in rows),
        }
        _write_json(
            self.stage_root / "package_verification/package_verification_summary.json", summary
        )
        self._gate(
            "package_verification",
            passed,
            summary=(
                f"Ran {len(rows)} baseline/tamper verifications across {package_count} packages; "
                f"{summary['passed_verification_count']} passed their declared oracle."
            ),
            evidence=[
                "package_verification/package_verification.csv",
                "package_verification/package_verification_summary.json",
            ],
            details=summary,
        )

    def _run_output_schema_validation(self) -> None:
        output_mappings = {
            "diagnostics.json": "diagnostics.schema.json",
            "evaluation_readiness.json": "evaluation_readiness.schema.json",
            "evaluation_package.json": "evaluation_package.schema.json",
            "mapping_assessment.json": "mapping_assessment.schema.json",
            "mapping_friction_summary.json": "mapping_friction.schema.json",
            "readiness_summary.json": "readiness_result.schema.json",
            "scorecard.json": "scorecard.schema.json",
            "source_profile.json": "source_profile.schema.json",
        }
        for basename, schema_name in output_mappings.items():
            for path in sorted(self.stage_root.rglob(basename)):
                if "frozen_inputs" in path.relative_to(self.stage_root).parts:
                    continue
                self._validate_json_file(path, schema_name, phase="generated_output")
        for path in sorted((self.stage_root / "package_verification/results").rglob("*.json")):
            self._validate_json_file(
                path, "package_verification.schema.json", phase="generated_output"
            )
        for path in sorted(self.stage_root.rglob("normalized_events.json")):
            try:
                events = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.schema_checks.append(
                    SchemaCheck(
                        phase="generated_output",
                        schema="canonical_event.schema.json",
                        instance=self._logical_path(path),
                        expected_valid=True,
                        actual_valid=False,
                        passed=False,
                        diagnostic="Normalized event artifact is not valid JSON.",
                    )
                )
                continue
            if not isinstance(events, list):
                self.schema_checks.append(
                    SchemaCheck(
                        phase="generated_output",
                        schema="canonical_event.schema.json",
                        instance=self._logical_path(path),
                        expected_valid=True,
                        actual_valid=False,
                        passed=False,
                        diagnostic="Normalized event artifact must contain a list.",
                    )
                )
                continue
            for index, event in enumerate(events):
                self._validate_schema_instance(
                    phase="generated_output",
                    schema_name="canonical_event.schema.json",
                    instance_name=f"{self._logical_path(path)}:{index + 1}",
                    payload=event,
                )
        self._write_schema_results()

    def _run_report_language_audit(self) -> None:
        protocol_path = self.repo_root / "verification/FROZEN_VERIFICATION_PROTOCOL.md"
        protocol = protocol_path.read_text(encoding="utf-8")
        normalized_protocol = " ".join(protocol.split())
        report_paths = [
            self.repo_root / "README.md",
            *sorted((self.repo_root / "docs").glob("*.md")),
            *sorted((self.repo_root / "report").glob("*.tex")),
            self.stage_root / "readiness/readiness_report.md",
        ]
        checks: list[dict[str, Any]] = []

        boundary = (
            "Hashes support identity and integrity; they do not prove authenticity, "
            "authorship, anonymity, or semantic correctness."
        )
        checks.append(
            {
                "check": "protocol_hash_claim_boundary",
                "passed": boundary in normalized_protocol,
                "matches": 0 if boundary in normalized_protocol else 1,
            }
        )
        generated_report = self.stage_root / "readiness/readiness_report.md"
        generated_text = (
            generated_report.read_text(encoding="utf-8") if generated_report.is_file() else ""
        )
        prohibited_generated = re.findall(
            (
                r"\b(?:independent(?:ly)? reproduc(?:e|ed|tion)|"
                r"proves? authenticity|proves? anonymity)\b"
            ),
            generated_text,
            flags=re.IGNORECASE,
        )
        checks.append(
            {
                "check": "generated_report_uses_repeatability_language",
                "passed": not prohibited_generated,
                "matches": len(prohibited_generated),
            }
        )

        terminology_patterns = {
            "no_deprecated_campaign_name": re.compile(
                r"requirements-based\s+black-box|black-box\s+campaign", re.IGNORECASE
            ),
            "no_independent_reproduction_claim": re.compile(
                r"independent[- ]reproduction|independently\s+reproduced", re.IGNORECASE
            ),
            "standard_fm2_name": re.compile(r"metrics\s+and\s+instrumentation", re.IGNORECASE),
            "standard_fm4_name": re.compile(
                r"validity,\s+reliability,\s+and\s+governance", re.IGNORECASE
            ),
            "raw_value_free_profile_term": re.compile(
                r"(?<!raw-)value-free\s+source\s+profile", re.IGNORECASE
            ),
            "no_supplementary_availability_claim": re.compile(
                r"supplementary\s+(?:technical\s+)?(?:records|artefacts|artifacts)|"
                r"artefacts?\s+(?:are\s+)?available\s+(?:online|at)",
                re.IGNORECASE,
            ),
        }
        for check_name, pattern in terminology_patterns.items():
            locations: list[str] = []
            for path in report_paths:
                if not path.is_file():
                    continue
                for line_number, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
                ):
                    if pattern.search(line):
                        locations.append(f"{self._logical_path(path)}:{line_number}")
            checks.append(
                {
                    "check": check_name,
                    "passed": not locations,
                    "matches": len(locations),
                    "locations": locations,
                }
            )

        overclaim_pattern = re.compile(
            r"hash(?:es|ing)?\s+(?:prove|proves|establish|establishes)\s+"
            r"(?:authenticity|anonymity|authorship)",
            flags=re.IGNORECASE,
        )
        overclaims = []
        stale_body_hash_claims = []
        for path in report_paths:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if overclaim_pattern.search(text):
                overclaims.append(self._logical_path(path))
            for line_number, line in enumerate(text.splitlines(), start=1):
                lowered = line.lower()
                if (
                    ("input" in lowered and "output" in lowered)
                    and "sha-256 hash" in lowered
                    and ("retain" in lowered or "represented" in lowered or "replaced" in lowered)
                    and "hmac" not in lowered
                ):
                    stale_body_hash_claims.append(f"{self._logical_path(path)}:{line_number}")
        checks.append(
            {
                "check": "no_hash_authenticity_or_anonymity_overclaim",
                "passed": not overclaims,
                "matches": len(overclaims),
                "locations": overclaims,
            }
        )
        checks.append(
            {
                "check": "documentation_matches_digest_free_langfuse_default",
                "passed": not stale_body_hash_claims,
                "matches": len(stale_body_hash_claims),
                "locations": stale_body_hash_claims,
            }
        )
        passed = all(check["passed"] for check in checks)
        result = {
            "language_audit_schema_version": "2.0.0",
            "passed": passed,
            "file_count": sum(path.is_file() for path in report_paths),
            "checks": checks,
            "interpretation": (
                "Same-author runs are deterministic repeatability. Hashes establish identity "
                "and integrity only."
            ),
        }
        _write_json(self.stage_root / "audits/report_language_audit.json", result)
        self._gate(
            "report_language_audit",
            passed,
            summary=(
                f"{sum(check['passed'] for check in checks)} of {len(checks)} report-language "
                "and documentation-consistency checks passed."
            ),
            evidence=["audits/report_language_audit.json"],
            details={
                "check_count": len(checks),
                "failed_check_count": sum(not check["passed"] for check in checks),
            },
        )

    def _write_command_records(self) -> None:
        command_root = self.stage_root / "commands"
        for index, capture in enumerate(self.commands, start=1):
            record = {
                "command_record_schema_version": "1.0.0",
                "label": capture.label,
                "command": [
                    self._sanitize_text(item, record_canaries=True) for item in capture.command
                ],
                "cwd": self._sanitize_text(str(capture.cwd), record_canaries=True),
                "returncode": capture.returncode,
                "stdout": self._sanitize_text(capture.stdout, record_canaries=True),
                "stderr": self._sanitize_text(capture.stderr, record_canaries=True),
            }
            _write_json(command_root / f"{index:04d}-{_slug(capture.label)}.json", record)

    @staticmethod
    def _canary_encodings(value: str) -> set[bytes]:
        return {
            value.encode("utf-8"),
            json.dumps(value, ensure_ascii=False)[1:-1].encode("utf-8"),
            json.dumps(value, ensure_ascii=True)[1:-1].encode("utf-8"),
        }

    def _privacy_matches(
        self,
    ) -> tuple[dict[str, str], list[dict[str, str]], list[dict[str, str]]]:
        excluded = {
            "frozen_inputs/": (
                "Frozen source inputs are content-addressed inputs to the campaign, not "
                "derived evidence or retained command output."
            )
        }
        canary_matches = list(self.capture_canary_matches)
        path_matches: list[dict[str, str]] = []
        for path in _relative_files(self.stage_root):
            logical = path.relative_to(self.stage_root).as_posix()
            if logical.startswith("frozen_inputs/"):
                continue
            content = path.read_bytes()
            for canary_id, canary in self.canaries.items():
                if any(encoded in content for encoded in self._canary_encodings(canary)):
                    match = {"canary_id": canary_id, "location": logical}
                    if match not in canary_matches:
                        canary_matches.append(match)
            for raw_path, alias in sorted(self.path_aliases.items()):
                if raw_path.encode("utf-8") in content:
                    match = {"path_id": alias, "location": logical}
                    if match not in path_matches:
                        path_matches.append(match)
        canary_matches.sort(key=lambda item: (item["location"], item["canary_id"]))
        path_matches.sort(key=lambda item: (item["location"], item["path_id"]))
        return excluded, canary_matches, path_matches

    def _run_privacy_path_scan(self) -> None:
        excluded, canary_matches, path_matches = self._privacy_matches()
        passed = not canary_matches and not path_matches
        result = {
            "privacy_scan_schema_version": "1.0.0",
            "passed": passed,
            "scope": (
                "All derived bundle files and retained command stdout/stderr; frozen source "
                "inputs under frozen_inputs/ are excluded."
            ),
            "configured_canary_count": len(self.canaries),
            "canary_match_count": len(canary_matches),
            "canary_matches": canary_matches,
            "forbidden_path_identifier_count": len(self.path_aliases),
            "path_match_count": len(path_matches),
            "path_matches": path_matches,
            "excluded_frozen_inputs": [
                {"path": path, "reason": reason} for path, reason in sorted(excluded.items())
            ],
            "raw_restricted_values_retained_in_report": 0,
            "final_metadata_rescan_enforced_by_finalizer": True,
        }
        _write_json(self.stage_root / "audits/privacy_scan.json", result)
        self._gate(
            "privacy_path_scan",
            passed,
            summary=(
                f"Derived-bundle scan found {len(canary_matches)} restricted-canary and "
                f"{len(path_matches)} forbidden-path matches."
            ),
            evidence=["audits/privacy_scan.json"],
            details={
                "configured_canary_count": len(self.canaries),
                "canary_match_count": len(canary_matches),
                "path_match_count": len(path_matches),
            },
        )

    def _evidence_candidates(self, basename: str) -> list[str]:
        aliases = {
            "canonical-event.schema.json": "canonical_event.schema.json",
            "oracle_workbook.xlsx": "oracle_workbook.xlsx",
            "privacy_scan.json": "privacy_scan.json",
        }
        actual = aliases.get(basename, basename)
        return sorted(
            path.relative_to(self.stage_root).as_posix()
            for path in self.stage_root.rglob(actual)
            if path.is_file()
        )

    def _evidence_gate(self, basename: str) -> str:
        if basename in EVIDENCE_GATE_BY_BASENAME:
            return EVIDENCE_GATE_BY_BASENAME[basename]
        if basename == "requirements_coverage.json":
            return "requirements_coverage"
        if basename in {"canonical-event.schema.json"} or basename.endswith(".schema.json"):
            return "schema_validation"
        if basename == "oracle_workbook.xlsx":
            return "frozen_inputs"
        if basename in {
            "diagnostics.json",
            "evaluation_readiness.json",
            "evaluation_package.json",
            "governance_repeatability.csv",
            "mapping_assessment.json",
            "mapping_friction_summary.json",
            "mapping_issue_register.csv",
            "mapping_readiness_report.md",
            "metric_accuracy.csv",
            "quarantined_runs.json",
            "readiness_matrix.csv",
            "redaction_report.json",
            "scorecard.json",
            "source_profile.json",
            "telemetry_validation.json",
        }:
            return "fresh_wheel_readiness"
        return "frozen_inputs"

    @staticmethod
    def _matrix_values(value: str) -> list[str]:
        return [item.strip() for item in value.split(";") if item.strip()]

    def _run_requirements_coverage(self) -> None:
        matrix_path = self.repo_root / "verification/requirements_matrix.csv"
        with matrix_path.open("r", encoding="utf-8", newline="") as handle:
            matrix = list(csv.DictReader(handle))
        requirement_results: list[dict[str, Any]] = []
        case_map: dict[str, dict[str, Any]] = {}
        for row in matrix:
            requirement_id = row["requirement_id"]
            evidence_names = self._matrix_values(row["final_evidence_artifact"])
            evidence_results = []
            for evidence_name in evidence_names:
                gate_name = self._evidence_gate(evidence_name)
                candidates = self._evidence_candidates(evidence_name)
                planned_self = evidence_name in {
                    "requirements_coverage.json",
                    "bundle_index.json",
                }
                gate_passed = (
                    True
                    if planned_self
                    else bool(self.gates.get(gate_name, {}).get("passed", False))
                )
                evidence_results.append(
                    {
                        "artifact": evidence_name,
                        "gate": gate_name,
                        "locations": candidates,
                        "exists": bool(candidates) or planned_self,
                        "gate_passed": gate_passed,
                        "passed": (bool(candidates) or planned_self) and gate_passed,
                    }
                )
            positive_cases = self._matrix_values(row["positive_case"])
            negative_cases = self._matrix_values(row["negative_or_boundary_case"])
            for direction, cases in (
                ("positive", positive_cases),
                ("negative_or_boundary", negative_cases),
            ):
                for case_id in cases:
                    entry = case_map.setdefault(
                        case_id,
                        {
                            "case_id": case_id,
                            "directions": set(),
                            "requirement_ids": set(),
                            "evidence_artifacts": set(),
                        },
                    )
                    entry["directions"].add(direction)
                    entry["requirement_ids"].add(requirement_id)
                    entry["evidence_artifacts"].update(evidence_names)
            fields_complete = all(
                row.get(name, "").strip()
                for name in (
                    "category",
                    "normative_rule",
                    "implementation_control",
                    "positive_case",
                    "negative_or_boundary_case",
                    "expected_result_or_code",
                    "final_evidence_artifact",
                )
            )
            passed = (
                fields_complete
                and bool(evidence_results)
                and all(item["passed"] for item in evidence_results)
            )
            requirement_results.append(
                {
                    "requirement_id": requirement_id,
                    "category": row["category"],
                    "passed": passed,
                    "fields_complete": fields_complete,
                    "positive_cases": positive_cases,
                    "negative_or_boundary_cases": negative_cases,
                    "evidence": evidence_results,
                }
            )

        case_rows = []
        for case_id, raw in sorted(case_map.items()):
            requirement_ids = sorted(raw["requirement_ids"])
            related = [
                result
                for result in requirement_results
                if result["requirement_id"] in requirement_ids
            ]
            case_rows.append(
                (
                    case_id,
                    ";".join(sorted(raw["directions"])),
                    ";".join(requirement_ids),
                    ";".join(sorted(raw["evidence_artifacts"])),
                    str(
                        bool(requirement_ids) and all(result["passed"] for result in related)
                    ).lower(),
                )
            )
        _write_csv(
            self.stage_root / "requirements/verification_case_index.csv",
            ("case_id", "directions", "requirement_ids", "evidence_artifacts", "passed"),
            case_rows,
        )
        requirement_ids = [result["requirement_id"] for result in requirement_results]
        unique_ids = len(requirement_ids) == len(set(requirement_ids))
        passed_count = sum(result["passed"] for result in requirement_results)
        passed = (
            bool(requirement_results)
            and unique_ids
            and passed_count == len(requirement_results)
            and bool(case_rows)
            and all(row[-1] == "true" for row in case_rows)
        )
        result = {
            "requirements_coverage_schema_version": "1.0.0",
            "passed": passed,
            "requirement_count": len(requirement_results),
            "covered_requirement_count": passed_count,
            "uncovered_requirement_count": len(requirement_results) - passed_count,
            "unique_requirement_ids": unique_ids,
            "verification_case_count": len(case_rows),
            "bidirectional_case_mapping_passed": bool(case_rows)
            and all(row[-1] == "true" for row in case_rows),
            "requirements": requirement_results,
        }
        _write_json(self.stage_root / "requirements/requirements_coverage.json", result)
        self._gate(
            "requirements_coverage",
            passed,
            summary=(
                f"{passed_count} of {len(requirement_results)} normative requirements and "
                f"{len(case_rows)} bidirectional case mappings are covered by passing evidence."
            ),
            evidence=[
                "requirements/requirements_coverage.json",
                "requirements/verification_case_index.csv",
            ],
            details={
                "requirement_count": len(requirement_results),
                "covered_requirement_count": passed_count,
                "verification_case_count": len(case_rows),
            },
        )

    def _campaign_record(self) -> dict[str, Any]:
        failed = sorted(name for name, gate in self.gates.items() if not gate["passed"])
        return {
            "campaign_record_schema_version": "1.0.0",
            "campaign": "frozen_thesis_verification",
            "source_date_epoch": self.source_date_epoch,
            "overall_status": "passed" if not failed else "failed",
            "gate_count": len(self.gates),
            "passed_gate_count": len(self.gates) - len(failed),
            "failed_gate_count": len(failed),
            "failed_gates": failed,
            "gates": dict(sorted(self.gates.items())),
            "freeze_manifest": "frozen_inputs/freeze_manifest.json",
            "command_record_count": len(self.commands),
            "interpretation": (
                "Passing supports implementation conformance under the frozen synthetic "
                "contract and the capability to assess mapping and analytical readiness. "
                "It does not establish representative enterprise mapping burden, enterprise "
                "utility, productivity, causal impact, authenticity, authorship, or anonymity."
            ),
        }

    def _finalize_record_and_privacy(self) -> None:
        _write_json(self.stage_root / "campaign_record.json", self._campaign_record())
        self._run_privacy_path_scan()
        self._run_requirements_coverage()
        _write_json(self.stage_root / "campaign_record.json", self._campaign_record())
        previous_privacy = bool(self.gates["privacy_path_scan"]["passed"])
        self._run_privacy_path_scan()
        if bool(self.gates["privacy_path_scan"]["passed"]) != previous_privacy:
            self._run_requirements_coverage()
        _write_json(self.stage_root / "campaign_record.json", self._campaign_record())
        result_entries = self._bundle_file_entries(exclude=set(RESULT_BUNDLE_HASH_EXCLUSIONS))
        self.result_bundle_sha256 = _canonical_hash(result_entries)

    def _bundle_file_entries(self, *, exclude: set[str]) -> list[dict[str, Any]]:
        entries = []
        for path in _relative_files(self.stage_root):
            logical = path.relative_to(self.stage_root).as_posix()
            if logical in exclude:
                continue
            entries.append(_fingerprint(path, logical))
        return entries

    def _write_bundle_index_and_checksums(self, *, enforce_final_scan: bool = True) -> None:
        index_path = self.stage_root / "bundle_index.json"
        checksum_path = self.stage_root / "bundle_checksums.sha256"
        entries = self._bundle_file_entries(
            exclude={"bundle_index.json", "bundle_checksums.sha256"}
        )
        result_entries = self._bundle_file_entries(exclude=set(RESULT_BUNDLE_HASH_EXCLUSIONS))
        recomputed_result_hash = _canonical_hash(result_entries)
        self.result_bundle_sha256 = recomputed_result_hash
        index = {
            "bundle_index_schema_version": "1.0.0",
            "hash_algorithm": "sha256",
            "file_count_excluding_index_and_checksum_register": len(entries),
            "files": entries,
            "frozen_bindings": {
                "source_tree_sha256": self.frozen_manifest.get("source_tree", {}).get(
                    "combined_sha256"
                ),
                "source_archive_sha256": self.frozen_manifest.get("source_tree", {})
                .get("archive", {})
                .get("sha256"),
                "dependency_lock_sha256": (self.frozen_manifest.get("dependency_lock") or {}).get(
                    "sha256"
                ),
                "fixture_set_sha256": self.frozen_manifest.get("categories", {})
                .get("fixtures", {})
                .get("combined_sha256"),
                "workflow_configuration_set_sha256": self.frozen_manifest.get("categories", {})
                .get("workflow_configurations", {})
                .get("combined_sha256"),
                "evaluation_manifest_set_sha256": self.frozen_manifest.get("categories", {})
                .get("evaluation_manifests", {})
                .get("combined_sha256"),
                "schema_set_sha256": self.frozen_manifest.get("categories", {})
                .get("schemas", {})
                .get("combined_sha256"),
                "protocol_sha256": self.frozen_manifest.get("protocol", {}).get("sha256"),
                "coverage_scope_sha256": self.frozen_manifest.get("coverage_scope", {}).get(
                    "sha256"
                ),
                "requirements_matrix_sha256": self.frozen_manifest.get(
                    "requirements_matrix", {}
                ).get("sha256"),
                "oracle_workbook_sha256": self.frozen_manifest.get("oracle_workbook", {}).get(
                    "sha256"
                ),
                "oracle_formula_export_sha256": self.frozen_manifest.get(
                    "oracle_formula_export", {}
                ).get("sha256"),
                "supplied_wheel_sha256": self.frozen_manifest.get("supplied_wheel", {}).get(
                    "sha256"
                ),
                "result_bundle_sha256": self.result_bundle_sha256,
                "result_bundle_file_count": len(result_entries),
                "result_bundle_hash_exclusions": sorted(RESULT_BUNDLE_HASH_EXCLUSIONS),
            },
        }
        _write_json(index_path, index)
        checksum_entries = self._bundle_file_entries(exclude={"bundle_checksums.sha256"})
        checksum_path.write_text(
            "".join(f"{entry['sha256']}  {entry['path']}\n" for entry in checksum_entries),
            encoding="utf-8",
        )
        if enforce_final_scan:
            _excluded, canary_matches, path_matches = self._privacy_matches()
            if canary_matches or path_matches:
                self._run_privacy_path_scan()
                self._run_requirements_coverage()
                _write_json(self.stage_root / "campaign_record.json", self._campaign_record())
                self._write_bundle_index_and_checksums(enforce_final_scan=False)


def _load_additional_canaries(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        values = {f"external_{index}": value for index, value in enumerate(payload, start=1)}
    elif isinstance(payload, dict):
        values = {str(key): value for key, value in payload.items()}
    else:
        raise ValueError("restricted canary file must contain a JSON list or object")
    if not all(isinstance(value, str) and value for value in values.values()):
        raise ValueError("every restricted canary must be a non-empty string")
    return values


def _publish(stage_root: Path, output_root: Path, *, overwrite: bool) -> None:
    output_root.parent.mkdir(parents=True, exist_ok=True)
    if output_root.exists() and not overwrite:
        raise FileExistsError(f"Refusing to replace {output_root}; use --overwrite")
    backup: Path | None = None
    if output_root.exists():
        backup = output_root.with_name(f".{output_root.name}.verification-backup")
        if backup.exists():
            shutil.rmtree(backup)
        os.replace(output_root, backup)
    try:
        os.replace(stage_root, output_root)
    except OSError:
        if backup is not None and backup.exists() and not output_root.exists():
            os.replace(backup, output_root)
        raise
    if backup is not None and backup.exists():
        shutil.rmtree(backup)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    wheel = Path(args.wheel).expanduser().resolve()
    output_root = Path(args.output_dir).expanduser()
    if not output_root.is_absolute():
        output_root = (repo_root / output_root).resolve()
    if not wheel.is_file() or wheel.suffix != ".whl":
        print(f"Wheel not found or not a .whl file: {wheel}", file=sys.stderr)
        return 2
    if args.source_date_epoch < 315_532_800:
        print("--source-date-epoch must be on or after 1980-01-01", file=sys.stderr)
        return 2
    if output_root.exists() and not args.overwrite:
        print(f"Refusing to replace {output_root}; use --overwrite", file=sys.stderr)
        return 2
    canary_file = (
        Path(args.restricted_canary_file).expanduser().resolve()
        if args.restricted_canary_file
        else None
    )
    try:
        canaries = {
            **DEFAULT_RESTRICTED_CANARIES,
            **_load_additional_canaries(canary_file),
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Invalid restricted canary file: {exc}", file=sys.stderr)
        return 2

    output_root.parent.mkdir(parents=True, exist_ok=True)
    stage_root = Path(
        tempfile.mkdtemp(prefix=f".{output_root.name}.verification-stage-", dir=output_root.parent)
    )
    passed = False
    try:
        with tempfile.TemporaryDirectory(prefix="agent-eval-verification-") as temp:
            campaign = VerificationCampaign(
                repo_root=repo_root,
                wheel=wheel,
                output_root=output_root,
                stage_root=stage_root,
                temp_root=Path(temp),
                source_date_epoch=args.source_date_epoch,
                canaries=canaries,
            )
            try:
                passed = campaign.run()
            except Exception as exc:  # retain an auditable final record for harness defects
                campaign._gate(
                    "campaign_internal_error",
                    False,
                    summary=f"Campaign raised {type(exc).__name__}: {exc}",
                    details={"exception_type": type(exc).__name__},
                )
                campaign._write_command_records()
                campaign._run_privacy_path_scan()
                _write_json(stage_root / "campaign_record.json", campaign._campaign_record())
                campaign._write_bundle_index_and_checksums()
                passed = False
        _publish(stage_root, output_root, overwrite=args.overwrite)
    except (OSError, ValueError) as exc:
        print(f"Verification campaign could not publish: {exc}", file=sys.stderr)
        if stage_root.exists():
            shutil.rmtree(stage_root)
        return 2

    print(f"Verification evidence bundle: {output_root}")
    print(f"Overall status: {'PASSED' if passed else 'FAILED'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
