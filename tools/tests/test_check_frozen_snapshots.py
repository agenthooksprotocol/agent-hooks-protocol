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

    def test_discovers_each_exact_semver_snapshot_once_in_sorted_order(self) -> None:
        self.add_complete_snapshot("10.2.3")
        self.add_complete_snapshot("0.1.0")

        self.assertEqual(
            ("0.1.0", "10.2.3"),
            frozen_checker.discover_frozen_snapshots(self.root),
        )

    def test_rejects_non_semver_snapshot_directory_names(self) -> None:
        invalid_names = (
            "v1.2.3",
            "1.2",
            "01.2.3",
            "1.02.3",
            "1.2.03",
            "1.2.3-rc.1",
            "latest",
        )
        for invalid_name in invalid_names:
            with self.subTest(invalid_name=invalid_name):
                path = self.root / "spec" / invalid_name
                path.mkdir()
                with self.assertRaisesRegex(
                    frozen_checker.FrozenSnapshotFailure,
                    "must use exact MAJOR.MINOR.PATCH",
                ):
                    frozen_checker.discover_frozen_snapshots(self.root)
                path.rmdir()

    def test_partial_snapshot_fails_through_conformance_checker(self) -> None:
        self.add_snapshot("spec", "0.1.0")
        stdout = io.StringIO()
        stderr = io.StringIO()

        exit_code = frozen_checker.run(
            self.root,
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(1, exit_code)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("snapshot '0.1.0' is incomplete", stderr.getvalue())
        self.assertIn("missing schema root schema/0.1.0", stderr.getvalue())
        self.assertIn("fixture root fixtures/0.1.0", stderr.getvalue())
        self.assertIn(
            "conformance root conformance/0.1.0",
            stderr.getvalue(),
        )

    def test_validates_every_discovered_snapshot_with_existing_checker(self) -> None:
        self.add_complete_snapshot("1.0.0")
        self.add_complete_snapshot("0.1.0")
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
            [(self.root, "0.1.0"), (self.root, "1.0.0")],
            calls,
        )
        self.assertEqual(2, stdout.getvalue().count("conformance checks passed"))

    def test_stops_after_first_failed_snapshot(self) -> None:
        self.add_complete_snapshot("0.1.0")
        self.add_complete_snapshot("1.0.0")
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
        self.assertEqual(["0.1.0"], calls)

    def test_git_base_rejects_uncatalogued_spec_prose_change(self) -> None:
        self.seed_snapshot_files("0.1.0")
        base_revision = self.commit_all()
        prose = self.root / "spec/0.1.0/uncatalogued.md"
        prose.write_text(
            "# Frozen prose\n\nA backend MAY replace this text.\n",
            encoding="utf-8",
        )
        stderr = io.StringIO()

        exit_code = frozen_checker.run(
            self.root,
            base_revision=base_revision,
            check_snapshot=self.passing_result,
            stdout=io.StringIO(),
            stderr=stderr,
        )

        self.assertEqual(1, exit_code)
        self.assertIn(
            "spec/0.1.0/uncatalogued.md: modified from frozen snapshot",
            stderr.getvalue(),
        )

    def test_git_base_rejects_manifest_refresh_that_blesses_frozen_edit(self) -> None:
        self.seed_snapshot_files("0.1.0")
        base_revision = self.commit_all()
        prose = self.root / "spec/0.1.0/uncatalogued.md"
        prose.write_text(prose.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        manifest = self.root / "schema/0.1.0/manifest.json"
        manifest.write_text('{"refreshed": true}\n', encoding="utf-8")
        stderr = io.StringIO()

        exit_code = frozen_checker.run(
            self.root,
            base_revision=base_revision,
            check_snapshot=self.passing_result,
            stdout=io.StringIO(),
            stderr=stderr,
        )

        self.assertEqual(1, exit_code)
        self.assertIn("spec/0.1.0/uncatalogued.md: modified", stderr.getvalue())
        self.assertIn("schema/0.1.0/manifest.json: modified", stderr.getvalue())

    def test_git_base_rejects_frozen_file_deletion(self) -> None:
        self.seed_snapshot_files("0.1.0")
        base_revision = self.commit_all()
        (self.root / "fixtures/0.1.0/manifest.json").unlink()
        stderr = io.StringIO()

        exit_code = frozen_checker.run(
            self.root,
            base_revision=base_revision,
            check_snapshot=self.passing_result,
            stdout=io.StringIO(),
            stderr=stderr,
        )

        self.assertEqual(1, exit_code)
        self.assertIn(
            "fixtures/0.1.0/manifest.json: deleted from frozen snapshot",
            stderr.getvalue(),
        )

    def test_git_base_rejects_frozen_file_rename(self) -> None:
        self.seed_snapshot_files("0.1.0")
        base_revision = self.commit_all()
        source = self.root / "conformance/0.1.0/manifest.json"
        source.rename(source.with_name("profiles.json"))
        stderr = io.StringIO()

        exit_code = frozen_checker.run(
            self.root,
            base_revision=base_revision,
            check_snapshot=self.passing_result,
            stdout=io.StringIO(),
            stderr=stderr,
        )

        self.assertEqual(1, exit_code)
        self.assertIn(
            "conformance/0.1.0/manifest.json: deleted from frozen snapshot",
            stderr.getvalue(),
        )
        self.assertIn(
            "conformance/0.1.0/profiles.json: added to frozen snapshot",
            stderr.getvalue(),
        )

    def test_git_base_allows_wholly_new_complete_snapshot_set(self) -> None:
        self.seed_snapshot_files("0.1.0")
        base_revision = self.commit_all()
        self.seed_snapshot_files("0.2.0")
        calls: list[str] = []

        def check_snapshot(
            root: Path,
            snapshot: str,
        ) -> check_conformance.CheckResult:
            calls.append(snapshot)
            return self.passing_result(root, snapshot)

        exit_code = frozen_checker.run(
            self.root,
            base_revision=base_revision,
            check_snapshot=check_snapshot,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )

        self.assertEqual(0, exit_code)
        self.assertEqual(["0.1.0", "0.2.0"], calls)


if __name__ == "__main__":
    unittest.main()
