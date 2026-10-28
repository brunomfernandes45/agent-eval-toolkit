from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_eval_toolkit.diagnostics import STABLE_DIAGNOSTIC_CODES
from agent_eval_toolkit.mapping import assess_mapping
from agent_eval_toolkit.readiness import blocked_readiness
from tests.helpers import ROOT

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - optional developer validation dependency
    Draft202012Validator = None  # type: ignore[assignment,misc]


class PublishedSchemasTest(unittest.TestCase):
    def test_required_schema_contracts_are_published(self) -> None:
        schema_root = Path(ROOT, "schemas")
        required = {
            "workflow_config.schema.json",
            "evaluation_manifest.schema.json",
            "langfuse_mapping.schema.json",
            "canonical_event.schema.json",
            "diagnostics.schema.json",
            "scorecard.schema.json",
            "evaluation_package.schema.json",
            "readiness_result.schema.json",
            "package_verification.schema.json",
            "source_profile.schema.json",
            "mapping_spec.schema.json",
            "mapping_activity.schema.json",
            "mapping_assessment.schema.json",
            "mapping_friction.schema.json",
            "evaluation_readiness.schema.json",
        }
        actual = {path.name for path in schema_root.glob("*.schema.json")}

        self.assertEqual(actual, required)
        identifiers = set()
        for path in sorted(schema_root.glob("*.schema.json")):
            schema = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertEqual(schema["type"], "object")
            self.assertTrue(schema["required"])
            identifiers.add(schema["$id"])
        self.assertEqual(len(identifiers), len(required))

    @unittest.skipUnless(Draft202012Validator is not None, "jsonschema is not installed")
    def test_published_contracts_pass_the_draft_2020_12_meta_schema(self) -> None:
        assert Draft202012Validator is not None
        for path in sorted(Path(ROOT, "schemas").glob("*.schema.json")):
            with self.subTest(schema=path.name):
                Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))

    def test_diagnostics_schema_enumerates_every_stable_implementation_code(self) -> None:
        schema = json.loads(
            Path(ROOT, "schemas", "diagnostics.schema.json").read_text(encoding="utf-8")
        )
        schema_codes = set(schema["$defs"]["diagnostic"]["properties"]["code"]["enum"])

        self.assertEqual(schema_codes, STABLE_DIAGNOSTIC_CODES)

    @unittest.skipUnless(Draft202012Validator is not None, "jsonschema is not installed")
    def test_mapping_and_readiness_artifacts_validate_against_published_contracts(self) -> None:
        assert Draft202012Validator is not None
        schema_root = Path(ROOT, "schemas")
        sample_root = Path(ROOT, "data", "mapping_samples")
        source_profile = json.loads(
            (sample_root / "source_profile.complete.json").read_text(encoding="utf-8")
        )
        mapping_spec = json.loads(
            (sample_root / "mapping_spec.confirmed.json").read_text(encoding="utf-8")
        )
        activity_rows = [
            json.loads(line)
            for line in (sample_root / "mapping_activity.complete.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "mapping"
            assess_mapping(
                sample_root / "source_profile.complete.json",
                sample_root / "mapping_spec.confirmed.json",
                sample_root / "manifest.mapping.json",
                output,
                activity_log_path=sample_root / "mapping_activity.complete.jsonl",
            )
            generated = {
                "mapping_assessment.schema.json": json.loads(
                    (output / "mapping_assessment.json").read_text(encoding="utf-8")
                ),
                "mapping_friction.schema.json": json.loads(
                    (output / "mapping_friction_summary.json").read_text(encoding="utf-8")
                ),
                "diagnostics.schema.json": json.loads(
                    (output / "diagnostics.json").read_text(encoding="utf-8")
                ),
            }

        fixtures = {
            "source_profile.schema.json": source_profile,
            "mapping_spec.schema.json": mapping_spec,
            "evaluation_readiness.schema.json": blocked_readiness("complete", "fixture"),
            **generated,
        }
        for schema_name, instance in fixtures.items():
            schema = json.loads((schema_root / schema_name).read_text(encoding="utf-8"))
            Draft202012Validator(schema).validate(instance)
        activity_schema = json.loads(
            (schema_root / "mapping_activity.schema.json").read_text(encoding="utf-8")
        )
        for row in activity_rows:
            Draft202012Validator(activity_schema).validate(row)


if __name__ == "__main__":
    unittest.main()
