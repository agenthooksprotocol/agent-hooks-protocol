from __future__ import annotations

import io
import json
import shutil
import subprocess
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

    def test_august_proposal_updates_span_every_artifact_layer(self) -> None:
        requirements = self.read_json("spec/draft/requirements.json")
        by_id = {
            requirement["id"]: requirement
            for requirement in requirements["requirements"]
        }
        self.assertEqual(
            "Every `hooks/intercept` request contains `capabilities.effects`, "
            "and a Tool Interception harness includes `deny` there for "
            "`tool.before`.",
            by_id["AHP-CAP-002"]["text"],
        )
        self.assertEqual(
            "A harness sends an event to a backend only when that backend has "
            "a subscription whose `events` array includes the exact event name "
            "and whose `mode` matches the delivery method.",
            by_id["AHP-REG-002"]["text"],
        )

        registration = self.read_json("schema/draft/registration.schema.json")
        intercept_subscription = registration["$defs"]["interceptSubscription"]
        self.assertEqual(
            "tool.before",
            intercept_subscription["properties"]["events"]["items"]["const"],
        )
        self.assertEqual(
            "intercept",
            intercept_subscription["properties"]["mode"]["const"],
        )
        request = self.read_json("schema/draft/intercept-request.schema.json")
        params = request["allOf"][1]["properties"]["params"]
        self.assertIn("capabilities", params["required"])
        self.assertEqual(
            "deny",
            params["properties"]["capabilities"]["allOf"][1]["properties"][
                "effects"
            ]["contains"]["const"],
        )

        fixture_manifest = self.read_json("fixtures/draft/manifest.json")
        fixture_cases = {
            case["id"]: case for case in fixture_manifest["cases"]
        }
        self.assertFalse(
            fixture_cases[
                "registration.intercept-observation-event.invalid"
            ]["expectedValid"]
        )
        self.assertFalse(
            fixture_cases[
                "http.intercept-request-effects-missing.invalid"
            ]["expectedValid"]
        )

        conformance = self.read_json("conformance/draft/manifest.json")
        covered = {
            requirement
            for profile in conformance["profiles"]
            for requirement in profile["requirements"]
        }
        self.assertTrue({"AHP-CAP-002", "AHP-REG-002"} <= covered)
        base_profile = (
            self.root / "conformance/draft/profiles/base.md"
        ).read_text(encoding="utf-8")
        tool_profile = (
            self.root / "conformance/draft/profiles/tool-interception.md"
        ).read_text(encoding="utf-8")
        self.assertIn("exact event-name subscription", base_profile)
        self.assertIn("mode matches the delivery method", base_profile)
        self.assertIn("`capabilities.effects`, including `deny`", tool_profile)
        self.assertFalse((self.root / "proposals").exists())
        self.assertFalse((self.root / "spec/source-migration.json").exists())

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
                source.startswith(f"schema/{version}/")
                for source in schema_manifest["sdkGeneration"]["stableNames"]
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



class AllSnapshotsCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        for root_name in checker.SNAPSHOT_ROOTS:
            (self.root / root_name / "draft").mkdir(parents=True)
        subprocess.run(
            ["git", "init", "--quiet", str(self.root)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        (self.root / "README.md").write_text("# Test repository\n", encoding="utf-8")
        self.commit_all("test: initialize repository")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def add_snapshot(self, root_name: str, snapshot: str) -> None:
        (self.root / root_name / snapshot).mkdir()

    def add_complete_snapshot(self, snapshot: str) -> None:
        for root_name in checker.SNAPSHOT_ROOTS:
            self.add_snapshot(root_name, snapshot)

    def write_snapshot_file(
        self,
        root_name: str,
        snapshot: str,
        relative: str,
        content: str,
    ) -> None:
        path = self.root / root_name / snapshot / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def seed_snapshot_files(self, snapshot: str) -> None:
        self.write_snapshot_file(
            "spec",
            snapshot,
            "uncatalogued.md",
            "# Frozen prose\n\nA backend MUST preserve this text.\n",
        )
        for root_name in ("schema", "fixtures", "conformance"):
            self.write_snapshot_file(
                root_name,
                snapshot,
                "manifest.json",
                json.dumps({"snapshot": snapshot, "root": root_name}) + "\n",
            )

    def commit_all(self, message: str = "test: freeze snapshot") -> str:
        subprocess.run(
            ["git", "-C", str(self.root), "add", "--all"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                "user.name=AHP Tests",
                "-c",
                "user.email=tests@example.com",
                "commit",
                "--quiet",
                "-m",
                message,
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return (
            subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "HEAD"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            .stdout.strip()
        )

    def tag(self, name: str, revision: str = "HEAD") -> None:
        subprocess.run(
            ["git", "-C", str(self.root), "tag", name, revision],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def orphan_commit(self, message: str = "test: unreachable release") -> str:
        return (
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.root),
                    "-c",
                    "user.name=AHP Tests",
                    "-c",
                    "user.email=tests@example.com",
                    "commit-tree",
                    "HEAD^{tree}",
                    "-m",
                    message,
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            .stdout.strip()
        )

    @staticmethod
    def passing_result(
        root: Path,
        snapshot: str,
    ) -> checker.CheckResult:
        return checker.CheckResult(
            errors=[],
            schema_count=1,
            fixture_count=1,
            requirement_count=1,
            profile_count=1,
        )

    def test_discovers_each_exact_date_snapshot_once_in_sorted_order(self) -> None:
        self.add_complete_snapshot("2026-08-27")
        self.add_complete_snapshot("2024-11-05")

        self.assertEqual(
            ("2024-11-05", "2026-08-27"),
            checker.discover_frozen_snapshots(self.root),
        )

    def test_rejects_non_date_and_impossible_date_snapshot_directories(self) -> None:
        invalid_names = (
            "1.2.3",
            "v2026-08-27",
            "2026-8-27",
            "2026-08-7",
            "2026-02-29",
            "2026-13-01",
            "2026-01-32",
            "2026-08-27-RC",
            "latest",
        )
        for invalid_name in invalid_names:
            with self.subTest(invalid_name=invalid_name):
                path = self.root / "spec" / invalid_name
                path.mkdir()
                with self.assertRaisesRegex(
                    checker.FrozenSnapshotFailure,
                    "must use a valid YYYY-MM-DD calendar date",
                ):
                    checker.discover_frozen_snapshots(self.root)
                path.rmdir()

    def test_partial_snapshot_fails_through_conformance_checker(self) -> None:
        self.add_snapshot("spec", "2026-08-27")
        stdout = io.StringIO()
        stderr = io.StringIO()

        def check_snapshot(root: Path, snapshot: str) -> checker.CheckResult:
            if snapshot == "draft":
                return self.passing_result(root, snapshot)
            return checker.run_checks(root, snapshot)

        exit_code = checker.run_all_checks(
            self.root,
            check_snapshot=check_snapshot,
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(1, exit_code)
        self.assertIn("first-release snapshot set", stdout.getvalue())
        self.assertIn("snapshot '2026-08-27' is incomplete", stderr.getvalue())
        self.assertIn("missing schema root schema/2026-08-27", stderr.getvalue())
        self.assertIn("fixture root fixtures/2026-08-27", stderr.getvalue())
        self.assertIn(
            "conformance root conformance/2026-08-27",
            stderr.getvalue(),
        )

    def test_validates_every_discovered_snapshot_with_existing_checker(self) -> None:
        self.add_complete_snapshot("2025-06-18")
        self.add_complete_snapshot("2024-11-05")
        calls: list[tuple[Path, str]] = []

        def check_snapshot(
            root: Path,
            snapshot: str,
        ) -> checker.CheckResult:
            calls.append((root, snapshot))
            return checker.CheckResult(
                errors=[],
                schema_count=1,
                fixture_count=2,
                requirement_count=3,
                profile_count=4,
            )

        stdout = io.StringIO()
        exit_code = checker.run_all_checks(
            self.root,
            check_snapshot=check_snapshot,
            stdout=stdout,
            stderr=io.StringIO(),
        )

        self.assertEqual(0, exit_code)
        self.assertEqual(
            [
                (self.root, "draft"),
                (self.root, "2024-11-05"),
                (self.root, "2025-06-18"),
            ],
            calls,
        )
        self.assertEqual(3, stdout.getvalue().count("conformance checks passed"))
        self.assertIn("first-release snapshot set", stdout.getvalue())

    def test_stops_after_first_failed_snapshot(self) -> None:
        self.add_complete_snapshot("2024-11-05")
        self.add_complete_snapshot("2025-06-18")
        calls: list[str] = []

        def check_snapshot(
            root: Path,
            snapshot: str,
        ) -> checker.CheckResult:
            calls.append(snapshot)
            return checker.CheckResult(
                errors=[] if snapshot == "draft" else ["failure"],
                schema_count=0,
                fixture_count=0,
                requirement_count=0,
                profile_count=0,
            )

        exit_code = checker.run_all_checks(
            self.root,
            check_snapshot=check_snapshot,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )

        self.assertEqual(1, exit_code)
        self.assertEqual(["draft", "2024-11-05"], calls)

    def test_release_tag_rejects_uncatalogued_spec_prose_change(self) -> None:
        version = "2024-11-05"
        self.seed_snapshot_files(version)
        self.commit_all()
        self.tag(version)
        prose = self.root / f"spec/{version}/uncatalogued.md"
        prose.write_text(
            "# Frozen prose\n\nA backend MAY replace this text.\n",
            encoding="utf-8",
        )
        stderr = io.StringIO()

        exit_code = checker.run_all_checks(
            self.root,
            check_snapshot=self.passing_result,
            stdout=io.StringIO(),
            stderr=stderr,
        )

        self.assertEqual(1, exit_code)
        self.assertIn(
            f"spec/{version}/uncatalogued.md: modified from frozen snapshot",
            stderr.getvalue(),
        )

    def test_release_tag_rejects_manifest_refresh_that_blesses_edit(self) -> None:
        version = "2024-11-05"
        self.seed_snapshot_files(version)
        self.commit_all()
        self.tag(version)
        prose = self.root / f"spec/{version}/uncatalogued.md"
        prose.write_text(prose.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        manifest = self.root / f"schema/{version}/manifest.json"
        manifest.write_text('{"refreshed": true}\n', encoding="utf-8")
        stderr = io.StringIO()

        exit_code = checker.run_all_checks(
            self.root,
            check_snapshot=self.passing_result,
            stdout=io.StringIO(),
            stderr=stderr,
        )

        self.assertEqual(1, exit_code)
        self.assertIn(f"spec/{version}/uncatalogued.md: modified", stderr.getvalue())
        self.assertIn(
            f"schema/{version}/manifest.json: modified", stderr.getvalue()
        )

    def test_release_tag_rejects_frozen_file_deletion(self) -> None:
        version = "2024-11-05"
        self.seed_snapshot_files(version)
        self.commit_all()
        self.tag(version)
        (self.root / f"fixtures/{version}/manifest.json").unlink()
        stderr = io.StringIO()

        exit_code = checker.run_all_checks(
            self.root,
            check_snapshot=self.passing_result,
            stdout=io.StringIO(),
            stderr=stderr,
        )

        self.assertEqual(1, exit_code)
        self.assertIn(
            f"fixtures/{version}/manifest.json: deleted from frozen snapshot",
            stderr.getvalue(),
        )

    def test_release_tag_rejects_file_added_to_existing_snapshot(self) -> None:
        version = "2024-11-05"
        self.seed_snapshot_files(version)
        self.commit_all()
        self.tag(version)
        self.write_snapshot_file(
            "schema",
            version,
            "added.schema.json",
            "{}\n",
        )
        stderr = io.StringIO()

        exit_code = checker.run_all_checks(
            self.root,
            check_snapshot=self.passing_result,
            stdout=io.StringIO(),
            stderr=stderr,
        )

        self.assertEqual(1, exit_code)
        self.assertIn(
            f"schema/{version}/added.schema.json: added to frozen snapshot",
            stderr.getvalue(),
        )

    def test_release_tag_rejects_frozen_file_rename(self) -> None:
        version = "2024-11-05"
        self.seed_snapshot_files(version)
        self.commit_all()
        self.tag(version)
        source = self.root / f"conformance/{version}/manifest.json"
        source.rename(source.with_name("profiles.json"))
        stderr = io.StringIO()

        exit_code = checker.run_all_checks(
            self.root,
            check_snapshot=self.passing_result,
            stdout=io.StringIO(),
            stderr=stderr,
        )

        self.assertEqual(1, exit_code)
        self.assertIn(
            f"conformance/{version}/manifest.json: deleted from frozen snapshot",
            stderr.getvalue(),
        )
        self.assertIn(
            f"conformance/{version}/profiles.json: added to frozen snapshot",
            stderr.getvalue(),
        )

    def test_release_tag_allows_wholly_new_complete_snapshot_set(self) -> None:
        released = "2024-11-05"
        new = "2025-03-26"
        self.seed_snapshot_files(released)
        self.commit_all()
        self.tag(released)
        self.seed_snapshot_files(new)
        calls: list[str] = []

        def check_snapshot(
            root: Path,
            snapshot: str,
        ) -> checker.CheckResult:
            calls.append(snapshot)
            return self.passing_result(root, snapshot)

        exit_code = checker.run_all_checks(
            self.root,
            check_snapshot=check_snapshot,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )

        self.assertEqual(0, exit_code)
        self.assertEqual(["draft", released, new], calls)

    def test_first_release_bootstrap_validates_new_snapshot_without_tag(self) -> None:
        version = "2024-11-05"
        self.seed_snapshot_files(version)
        stdout = io.StringIO()
        calls: list[str] = []

        def check_snapshot(
            root: Path,
            snapshot: str,
        ) -> checker.CheckResult:
            calls.append(snapshot)
            return self.passing_result(root, snapshot)

        exit_code = checker.run_all_checks(
            self.root,
            check_snapshot=check_snapshot,
            stdout=stdout,
            stderr=io.StringIO(),
        )

        self.assertEqual(0, exit_code)
        self.assertEqual(["draft", version], calls)
        self.assertIn("first-release snapshot set", stdout.getvalue())

    def test_selects_newest_reachable_release_date_as_baseline(self) -> None:
        older = "2024-11-05"
        newer = "2025-03-26"
        self.seed_snapshot_files(older)
        self.commit_all("test: first release")
        self.tag(older)
        self.seed_snapshot_files(newer)
        self.commit_all("test: second release")
        self.tag(newer)

        self.assertEqual(
            newer,
            checker.newest_reachable_release_tag(self.root),
        )

        prose = self.root / f"spec/{older}/uncatalogued.md"
        prose.write_text(prose.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        stderr = io.StringIO()
        exit_code = checker.run_all_checks(
            self.root,
            check_snapshot=self.passing_result,
            stdout=io.StringIO(),
            stderr=stderr,
        )

        self.assertEqual(1, exit_code)
        self.assertIn(f"released at {newer}", stderr.getvalue())

    def test_ignores_newer_release_tag_not_reachable_from_revision(self) -> None:
        reachable = "2024-11-05"
        unreachable = "2026-08-27"
        self.seed_snapshot_files(reachable)
        self.commit_all()
        self.tag(reachable)
        self.tag(unreachable, self.orphan_commit())

        self.assertEqual(
            reachable,
            checker.newest_reachable_release_tag(self.root),
        )

    def test_release_tag_must_target_its_matching_snapshot(self) -> None:
        snapshot = "2024-11-05"
        retargeted_release = "2025-03-26"
        self.seed_snapshot_files(snapshot)
        self.commit_all()
        self.tag(retargeted_release)
        stderr = io.StringIO()

        exit_code = checker.run_all_checks(
            self.root,
            check_snapshot=self.passing_result,
            stdout=io.StringIO(),
            stderr=stderr,
        )

        self.assertEqual(1, exit_code)
        self.assertIn(
            f"release tag '{retargeted_release}'",
            stderr.getvalue(),
        )
        self.assertIn(
            f"does not contain its complete date snapshot",
            stderr.getvalue(),
        )

    def test_rejects_impossible_calendar_date_release_tag(self) -> None:
        self.seed_snapshot_files("2024-11-05")
        self.commit_all()
        self.tag("2026-02-29")

        with self.assertRaisesRegex(
            checker.FrozenSnapshotFailure,
            "not a valid calendar date",
        ):
            checker.newest_reachable_release_tag(self.root)

    def test_ignores_nonfinal_release_tags(self) -> None:
        self.seed_snapshot_files("2024-11-05")
        self.commit_all()
        self.tag("2024-11-05-RC")
        self.tag("v2024-11-05")
        self.tag("1.2.3")

        self.assertIsNone(
            checker.newest_reachable_release_tag(self.root)
        )



if __name__ == "__main__":
    unittest.main()
