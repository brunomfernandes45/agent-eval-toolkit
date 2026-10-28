from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .exceptions import ConfigurationError
from .models import NormalizedEvent


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_text(canonical)


def hash_document(payload: dict[str, Any], *, exclude_fields: Iterable[str] = ()) -> str:
    excluded = set(exclude_fields)
    canonical_payload = {key: value for key, value in payload.items() if key not in excluded}
    return hash_payload(canonical_payload)


def hash_raw_line(line_content: str) -> str:
    return sha256_text(line_content)


def file_fingerprint(path: str | Path, *, logical_path: str | None = None) -> dict[str, Any]:
    target = Path(path)
    digest = hashlib.sha256()
    size_bytes = 0
    with target.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size_bytes += len(chunk)
            digest.update(chunk)
    return {
        "path": logical_path if logical_path is not None else str(target),
        "sha256": digest.hexdigest(),
        "size_bytes": size_bytes,
    }


def build_redaction_report(
    events: list[NormalizedEvent], workflow_config: dict[str, Any]
) -> dict[str, Any]:
    restricted_fields = sorted(workflow_config.get("restricted_fields", []))
    redact_raw_fields = sorted(workflow_config.get("redact_raw_fields", []))
    redacted_counts: dict[str, int] = {}
    evidence_refs_with_redaction = 0

    for event in events:
        if not event.evidence or not event.evidence.redacted_fields:
            continue
        evidence_refs_with_redaction += 1
        for field_name in event.evidence.redacted_fields:
            redacted_counts[field_name] = redacted_counts.get(field_name, 0) + 1

    unprotected_restricted_fields = sorted(set(restricted_fields) - set(redact_raw_fields))
    return {
        "policy": {
            "evidence_mode": workflow_config.get("evidence_mode", "anchor_hash_redacted_snapshot"),
            "restricted_fields": restricted_fields,
            "redact_raw_fields": redact_raw_fields,
            "capture_raw_fields": workflow_config.get("capture_raw_fields", []),
            "allowed_metadata_fields": workflow_config.get(
                "allowed_metadata_fields", workflow_config.get("metadata_fields", [])
            ),
        },
        "passed": not unprotected_restricted_fields,
        "redacted_field_counts": dict(sorted(redacted_counts.items())),
        "evidence_refs_with_redaction": evidence_refs_with_redaction,
        "blocked_fields": [],
        "unprotected_restricted_fields": unprotected_restricted_fields,
    }


def build_artifact_index(output_dir: str | Path, artifact_names: list[str]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    output_root = Path(output_dir)
    for artifact_name in artifact_names:
        artifact_path = output_root / artifact_name
        if artifact_path.exists():
            artifacts.append(file_fingerprint(artifact_path, logical_path=artifact_name))
    return artifacts


def configured_restricted_canaries(
    workflow_config: dict[str, Any],
) -> list[tuple[str, str]]:
    """Return explicitly configured leak-test values without adding them to artifacts."""

    candidates: list[Any] = []
    for field_name in (
        "restricted_canaries",
        "restricted_value_canaries",
        "redaction_canaries",
    ):
        if field_name in workflow_config:
            candidates.append(workflow_config[field_name])
    privacy = workflow_config.get("privacy")
    if isinstance(privacy, dict) and "restricted_canaries" in privacy:
        candidates.append(privacy["restricted_canaries"])

    canaries: list[tuple[str, str]] = []
    for candidate in candidates:
        if isinstance(candidate, list):
            for index, value in enumerate(candidate, start=1):
                if not isinstance(value, str) or not value:
                    raise ConfigurationError("Restricted canary values must be non-empty strings")
                canaries.append((f"restricted_canary_{index}", value))
        elif isinstance(candidate, dict):
            for label, raw_value in sorted(candidate.items(), key=lambda item: str(item[0])):
                values = raw_value if isinstance(raw_value, list) else [raw_value]
                for index, value in enumerate(values, start=1):
                    if not isinstance(value, str) or not value:
                        raise ConfigurationError(
                            "Restricted canary values must be non-empty strings"
                        )
                    suffix = f"_{index}" if len(values) > 1 else ""
                    canaries.append((f"{label}{suffix}", value))
        else:
            raise ConfigurationError("Restricted canaries must be a list of strings or an object")
    return sorted(set(canaries), key=lambda item: (item[0], item[1]))


def scan_derived_bundle(
    output_dir: str | Path,
    canaries: Iterable[tuple[str, str]],
    *,
    captured_output: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Scan every derived byte plus supplied captured output for restricted canaries."""

    root = Path(output_dir)
    configured = list(canaries)
    matches: list[dict[str, str]] = []
    if configured and root.exists():
        for path in sorted(
            (candidate for candidate in root.rglob("*") if candidate.is_file()),
            key=lambda candidate: candidate.relative_to(root).as_posix(),
        ):
            file_content = path.read_bytes()
            logical_path = path.relative_to(root).as_posix()
            for label, value in configured:
                if any(encoded in file_content for encoded in _canary_encodings(value)):
                    matches.append({"canary_id": label, "location": logical_path})

    for stream_name, captured_text in sorted((captured_output or {}).items()):
        for label, value in configured:
            escaped_values = {
                json.dumps(value, ensure_ascii=True)[1:-1],
                json.dumps(value, ensure_ascii=False)[1:-1],
            }
            if value in captured_text or any(item in captured_text for item in escaped_values):
                matches.append({"canary_id": label, "location": f"captured_output:{stream_name}"})

    return {
        "passed": not matches,
        "configured_canary_count": len(configured),
        "match_count": len(matches),
        "matches": matches,
    }


def _canary_encodings(value: str) -> set[bytes]:
    return {
        value.encode("utf-8"),
        json.dumps(value, ensure_ascii=True)[1:-1].encode("utf-8"),
        json.dumps(value, ensure_ascii=False)[1:-1].encode("utf-8"),
    }
