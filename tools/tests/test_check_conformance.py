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
import freeze_snapshot


class SnapshotCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        for name in (
            "spec",
            "schema",
            "fixtures",
            "conformance",
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
            (16, 31, 22, 5),
            (
                result.schema_count,
                result.fixture_count,
                result.requirement_count,
                result.profile_count,
            ),
        )

    def test_rejects_cross_root_version_mismatch(self) -> None:
        manifest = self.read_json("schema/draft/manifest.json")
        manifest["snapshotVersion"] = "2026-08-27"
        self.write_json("schema/draft/manifest.json", manifest)

        result = checker.run_checks(self.root)

        self.assert_has_error(result, "schema manifest snapshotVersion mismatch")

    def test_accepts_only_literal_draft_or_calendar_date_snapshot_keys(self) -> None:
        valid = ("draft", "2024-02-29", "2026-08-27")
        invalid = (
            "Draft",
            "latest",
            "1.2.3",
            "v2026-08-27",
            "2026-8-27",
            "2026-08-7",
            "2026-02-29",
            "2026-13-01",
            "2026-00-01",
            "2026-01-00",
            "2026-01-32",
        )

        for key in valid:
            with self.subTest(key=key):
                self.assertTrue(checker.is_snapshot_key(key))
        for key in invalid:
            with self.subTest(key=key):
                self.assertFalse(checker.is_snapshot_key(key))

    def test_draft_aggregate_accepts_literal_draft_and_rejects_old_version(self) -> None:
        snapshot = checker.Snapshot.resolve(self.root)
        store = checker.SchemaStore(snapshot)
        validator = checker.SubsetValidator(store)
        aggregate_path = snapshot.schema_dir / "schema.json"
        aggregate = store.load(aggregate_path)
        fixture = self.read_json("fixtures/draft/http/intercept-request.valid.json")

        self.assertEqual([], validator.validate(fixture, aggregate, aggregate_path))
        fixture["params"]["protocolVersion"] = "1.2.3"
        self.assertNotEqual([], validator.validate(fixture, aggregate, aggregate_path))

    def test_rejects_protocol_version_constant_different_from_snapshot(self) -> None:
        common = self.read_json("schema/draft/common.schema.json")
        common["$defs"]["protocolVersion"]["const"] = "2026-08-27"
        self.write_json("schema/draft/common.schema.json", common)

        result = checker.run_checks(self.root)

        self.assert_has_error(
            result,
            "protocolVersion constant must equal 'draft'",
        )

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
        version = "2026-08-27"
        freeze_snapshot.freeze_snapshot(self.root, version)
        manifest_paths = (
            self.root / f"schema/{version}/manifest.json",
            self.root / f"fixtures/{version}/manifest.json",
            self.root / f"conformance/{version}/manifest.json",
        )
        before = {path: path.read_bytes() for path in manifest_paths}

        result = checker.run_checks(self.root, version, update=True)

        self.assert_has_error(
            result, f"cannot update immutable frozen snapshot '{version}'"
        )
        self.assertEqual(before, {path: path.read_bytes() for path in manifest_paths})

    def test_release_freeze_retargets_every_version_layer(self) -> None:
        version = "2026-08-27"

        freeze_snapshot.freeze_snapshot(self.root, version)

        result = checker.run_checks(self.root, version)
        self.assertEqual([], result.errors)
        requirements = self.read_json(f"spec/{version}/requirements.json")
        schema_manifest = self.read_json(f"schema/{version}/manifest.json")
        fixture_manifest = self.read_json(f"fixtures/{version}/manifest.json")
        conformance_manifest = self.read_json(
            f"conformance/{version}/manifest.json"
        )
        common = self.read_json(f"schema/{version}/common.schema.json")
        fixture = self.read_json(
            f"fixtures/{version}/http/intercept-request.valid.json"
        )

        for metadata in (
            requirements,
            schema_manifest,
            fixture_manifest,
            conformance_manifest,
        ):
            self.assertEqual("Published", metadata["status"])
            self.assertEqual(version, metadata["snapshotVersion"])
            self.assertEqual(version, metadata["protocolVersion"])
        self.assertEqual(
            f"https://agenthooksprotocol.org/schemas/{version}/common.schema.json",
            common["$id"],
        )
        self.assertEqual(version, common["$defs"]["protocolVersion"]["const"])
        self.assertEqual(version, fixture["params"]["protocolVersion"])
        self.assertTrue(
            all(
                item["path"].startswith(f"schema/{version}/")
                for item in schema_manifest["documents"]
            )
        )
        self.assertTrue(
            all(
                case["path"].startswith(f"fixtures/{version}/")
                and case["schema"].startswith(f"schema/{version}/")
                for case in fixture_manifest["cases"]
            )
        )
        self.assertEqual(
            f"spec/{version}/requirements.json",
            conformance_manifest["canonicalRequirements"],
        )
        self.assertNotIn(
            "protocol version `draft`",
            (self.root / f"spec/{version}/base/index.md").read_text(
                encoding="utf-8"
            ),
        )
        for path in (self.root / f"fixtures/{version}").rglob("*.json*"):
            if path.name != "manifest.json":
                self.assertNotIn('"draft"', path.read_text(encoding="utf-8"))

    def test_release_freeze_rejects_invalid_date_and_existing_destination(self) -> None:
        with self.assertRaisesRegex(
            freeze_snapshot.FreezeFailure,
            "valid YYYY-MM-DD calendar date",
        ):
            freeze_snapshot.freeze_snapshot(self.root, "2026-02-29")

        version = "2026-08-27"
        (self.root / f"spec/{version}").mkdir()
        with self.assertRaisesRegex(
            freeze_snapshot.FreezeFailure,
            "release destination already exists",
        ):
            freeze_snapshot.freeze_snapshot(self.root, version)


if __name__ == "__main__":
    unittest.main()
