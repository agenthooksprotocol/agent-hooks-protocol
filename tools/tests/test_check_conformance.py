from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "tools"))

import check_conformance as checker


class SnapshotCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        for name in (
            "spec",
            "schema",
            "fixtures",
            "conformance",
            "proposals",
            "tools",
            "docs",
            "governance",
        ):
            source = REPOSITORY_ROOT / name
            destination = self.root / name
            if source.is_dir():
                shutil.copytree(source, destination)
        for source in REPOSITORY_ROOT.glob("*.md"):
            shutil.copy2(source, self.root / source.name)
        shutil.copy2(REPOSITORY_ROOT / "LICENSE", self.root / "LICENSE")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def read_json(self, relative: str) -> dict:
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

    def write_json(self, relative: str, value: dict) -> None:
        (self.root / relative).write_text(
            json.dumps(value, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def assert_has_error(self, result: checker.CheckResult, text: str) -> None:
        self.assertTrue(
            any(text in error for error in result.errors),
            f"expected an error containing {text!r}, got {result.errors!r}",
        )

    def test_draft_snapshot_has_completed_aggregate_counts(self) -> None:
        result = checker.run_checks(self.root)

        self.assertEqual([], result.errors)
        self.assertEqual(
            (16, 29, 20, 5),
            (
                result.schema_count,
                result.fixture_count,
                result.requirement_count,
                result.profile_count,
            ),
        )

    def test_rejects_cross_root_version_mismatch(self) -> None:
        manifest = self.read_json("schema/draft/manifest.json")
        manifest["draftVersion"] = "0.1.0-draft.other"
        self.write_json("schema/draft/manifest.json", manifest)

        result = checker.run_checks(self.root)

        self.assert_has_error(result, "schema manifest draftVersion mismatch")

    def test_rejects_missing_snapshot_root(self) -> None:
        shutil.rmtree(self.root / "fixtures/draft")

        result = checker.run_checks(self.root)

        self.assert_has_error(result, "missing fixture root fixtures/draft")

    def test_rejects_noncanonical_or_traversing_manifest_path(self) -> None:
        manifest = self.read_json("schema/draft/manifest.json")
        manifest["documents"][0]["path"] = "schema/draft/../outside.schema.json"
        self.write_json("schema/draft/manifest.json", manifest)

        result = checker.run_checks(self.root)

        self.assert_has_error(result, "invalid repository-relative path")

    def test_detects_manifest_hash_drift(self) -> None:
        schema = self.root / "schema/draft/capabilities.schema.json"
        schema.write_text(schema.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        result = checker.run_checks(self.root)

        self.assert_has_error(result, "schema manifest hash drift")

    def test_rejects_ref_that_escapes_schema_snapshot(self) -> None:
        snapshot = checker.Snapshot.resolve(self.root)
        store = checker.SchemaStore(snapshot)
        current = snapshot.schema_dir / "capabilities.schema.json"

        with self.assertRaisesRegex(checker.CheckFailure, "parent traversal in \\$ref"):
            store.resolve("../outside.schema.json", current)

    def test_detects_incomplete_aggregate_branch_coverage(self) -> None:
        manifest = self.read_json("fixtures/draft/manifest.json")
        removed = next(
            case for case in manifest["cases"] if case["id"] == "http.error-response.valid"
        )
        manifest["cases"].remove(removed)
        self.write_json("fixtures/draft/manifest.json", manifest)
        (self.root / removed["path"]).unlink()

        result = checker.run_checks(self.root)

        self.assert_has_error(
            result,
            "aggregate branches lack valid fixture coverage: common.schema.json#/$defs/errorResponse",
        )

    def test_detects_incomplete_observe_event_variant_coverage(self) -> None:
        manifest = self.read_json("fixtures/draft/manifest.json")
        removed = next(
            case
            for case in manifest["cases"]
            if case["id"] == "http.observe-session-end.valid"
        )
        manifest["cases"].remove(removed)
        self.write_json("fixtures/draft/manifest.json", manifest)
        (self.root / removed["path"]).unlink()

        result = checker.run_checks(self.root)

        self.assert_has_error(
            result,
            "observe event variants lack valid fixture coverage: session-end.schema.json",
        )

    def test_requires_aggregate_expectation_for_wire_fixture(self) -> None:
        manifest = self.read_json("fixtures/draft/manifest.json")
        case = next(
            case for case in manifest["cases"] if case["id"] == "http.intercept-request.valid"
        )
        del case["aggregateExpectedValid"]
        self.write_json("fixtures/draft/manifest.json", manifest)

        result = checker.run_checks(self.root)

        self.assert_has_error(
            result,
            "http.intercept-request.valid: wire fixture is missing aggregateExpectedValid",
        )

    def test_rejects_manifest_updates_for_frozen_snapshot(self) -> None:
        for root_name in ("spec", "schema", "fixtures", "conformance"):
            shutil.copytree(
                self.root / root_name / "draft",
                self.root / root_name / "0.1.0",
            )
        manifest_paths = (
            self.root / "schema/0.1.0/manifest.json",
            self.root / "fixtures/0.1.0/manifest.json",
            self.root / "conformance/0.1.0/manifest.json",
        )
        before = {path: path.read_bytes() for path in manifest_paths}

        result = checker.run_checks(self.root, "0.1.0", update=True)

        self.assert_has_error(result, "cannot update immutable frozen snapshot '0.1.0'")
        self.assertEqual(before, {path: path.read_bytes() for path in manifest_paths})


if __name__ == "__main__":
    unittest.main()
