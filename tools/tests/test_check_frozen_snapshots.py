from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "tools"))

import check_conformance
import check_frozen_snapshots as frozen_checker


class FrozenSnapshotCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        for root_name in frozen_checker.SNAPSHOT_ROOTS:
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
        for root_name in frozen_checker.SNAPSHOT_ROOTS:
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
    ) -> check_conformance.CheckResult:
        return check_conformance.CheckResult(
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
            frozen_checker.discover_frozen_snapshots(self.root),
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
                    frozen_checker.FrozenSnapshotFailure,
                    "must use a valid YYYY-MM-DD calendar date",
                ):
                    frozen_checker.discover_frozen_snapshots(self.root)
                path.rmdir()

    def test_partial_snapshot_fails_through_conformance_checker(self) -> None:
        self.add_snapshot("spec", "2026-08-27")
        stdout = io.StringIO()
        stderr = io.StringIO()

        exit_code = frozen_checker.run(
            self.root,
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
        ) -> check_conformance.CheckResult:
            calls.append((root, snapshot))
            return check_conformance.CheckResult(
                errors=[],
                schema_count=1,
                fixture_count=2,
                requirement_count=3,
                profile_count=4,
            )

        stdout = io.StringIO()
        exit_code = frozen_checker.run(
            self.root,
            check_snapshot=check_snapshot,
            stdout=stdout,
            stderr=io.StringIO(),
        )

        self.assertEqual(0, exit_code)
        self.assertEqual(
            [(self.root, "2024-11-05"), (self.root, "2025-06-18")],
            calls,
        )
        self.assertEqual(2, stdout.getvalue().count("conformance checks passed"))
        self.assertIn("first-release snapshot set", stdout.getvalue())

    def test_stops_after_first_failed_snapshot(self) -> None:
        self.add_complete_snapshot("2024-11-05")
        self.add_complete_snapshot("2025-06-18")
        calls: list[str] = []

        def check_snapshot(
            root: Path,
            snapshot: str,
        ) -> check_conformance.CheckResult:
            calls.append(snapshot)
            return check_conformance.CheckResult(
                errors=["failure"],
                schema_count=0,
                fixture_count=0,
                requirement_count=0,
                profile_count=0,
            )

        exit_code = frozen_checker.run(
            self.root,
            check_snapshot=check_snapshot,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )

        self.assertEqual(1, exit_code)
        self.assertEqual(["2024-11-05"], calls)

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

        exit_code = frozen_checker.run(
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

        exit_code = frozen_checker.run(
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

        exit_code = frozen_checker.run(
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

        exit_code = frozen_checker.run(
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

        exit_code = frozen_checker.run(
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
        ) -> check_conformance.CheckResult:
            calls.append(snapshot)
            return self.passing_result(root, snapshot)

        exit_code = frozen_checker.run(
            self.root,
            check_snapshot=check_snapshot,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )

        self.assertEqual(0, exit_code)
        self.assertEqual([released, new], calls)

    def test_first_release_bootstrap_validates_new_snapshot_without_tag(self) -> None:
        version = "2024-11-05"
        self.seed_snapshot_files(version)
        stdout = io.StringIO()
        calls: list[str] = []

        def check_snapshot(
            root: Path,
            snapshot: str,
        ) -> check_conformance.CheckResult:
            calls.append(snapshot)
            return self.passing_result(root, snapshot)

        exit_code = frozen_checker.run(
            self.root,
            check_snapshot=check_snapshot,
            stdout=stdout,
            stderr=io.StringIO(),
        )

        self.assertEqual(0, exit_code)
        self.assertEqual([version], calls)
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
            frozen_checker.newest_reachable_release_tag(self.root),
        )

        prose = self.root / f"spec/{older}/uncatalogued.md"
        prose.write_text(prose.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        stderr = io.StringIO()
        exit_code = frozen_checker.run(
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
            frozen_checker.newest_reachable_release_tag(self.root),
        )

    def test_release_tag_must_target_its_matching_snapshot(self) -> None:
        snapshot = "2024-11-05"
        retargeted_release = "2025-03-26"
        self.seed_snapshot_files(snapshot)
        self.commit_all()
        self.tag(retargeted_release)
        stderr = io.StringIO()

        exit_code = frozen_checker.run(
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
            frozen_checker.FrozenSnapshotFailure,
            "not a valid calendar date",
        ):
            frozen_checker.newest_reachable_release_tag(self.root)

    def test_ignores_nonfinal_release_tags(self) -> None:
        self.seed_snapshot_files("2024-11-05")
        self.commit_all()
        self.tag("2024-11-05-RC")
        self.tag("v2024-11-05")
        self.tag("1.2.3")

        self.assertIsNone(
            frozen_checker.newest_reachable_release_tag(self.root)
        )


if __name__ == "__main__":
    unittest.main()
