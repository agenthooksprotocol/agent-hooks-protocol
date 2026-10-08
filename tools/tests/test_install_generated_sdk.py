"""Regression coverage for the SDK sync install/staging contract."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "install_generated_sdk", Path(__file__).resolve().parents[1] / "install_generated_sdk.py"
)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
COMMIT = "b" * 40
WORKFLOW = (
    "steps:\n"
    "  - uses: actions/checkout@" + "a" * 40 + "\n"
    "    with:\n"
    "      repository: agenthooksprotocol/agent-hooks-protocol\n"
    "      ref: " + "a" * 40 + " # immutable\n"
    "      path: protocol\n"
)


class InstallGeneratedSdkTests(unittest.TestCase):
    def test_install_and_stage_all_languages(self):
        artifacts = {
            "typescript": ["generated.ts", "schemas.ts"],
            "python": ["generated.py", "schemas.json", "_boundaries.py", "future/nested.py"],
            "go": ["generated.go", "schemas.json", "client/boundaries_generated.go",
                   "client/facade_generated_test.go", "facade_generated_test.go",
                   "event/generated.go", "internal/canonical/schemas.json"],
            "rust": ["generated.rs", "schemas.json"],
        }
        for language, files in artifacts.items():
            with self.subTest(language=language), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, sdk = root / "generated", root / "sdk"
                source.mkdir()
                (sdk / ".github/workflows").mkdir(parents=True)
                workflow = sdk / ".github/workflows/ci.yml"
                workflow.write_text(WORKFLOW)
                (sdk / "unrelated.txt").write_text("do not stage\n")
                for name in files:
                    artifact = source / name
                    artifact.parent.mkdir(parents=True, exist_ok=True)
                    artifact.write_text(name + "\n")
                    artifact.chmod(0o755)
                if language == "python":
                    (source / "__pycache__").mkdir()
                    (source / "__pycache__/generated.cpython-311.pyc").write_bytes(b"cache")
                lock = source / "ahp-codegen.lock.json"
                lock.write_text(json.dumps({"language": language, "sourceCommit": COMMIT}))
                subprocess.run(["git", "init", "-q", str(sdk)], check=True)
                paths = installer.install_sdk(source, sdk, language)
                expected = {(installer.DESTINATIONS[language] / name).as_posix()
                            for name in files + ["ahp-codegen.lock.json"]}
                if language in ("python", "rust"):
                    expected.add("ahp-codegen.lock.json")
                    self.assertEqual((sdk / "ahp-codegen.lock.json").read_bytes(), lock.read_bytes())
                self.assertEqual(set(paths), expected | {".github/workflows/ci.yml"})
                self.assertEqual(workflow.read_text(), WORKFLOW.replace("ref: " + "a" * 40, "ref: " + COMMIT))
                for name in expected:
                    self.assertEqual((sdk / name).stat().st_mode & 0o777, 0o644)
                subprocess.run(["git", "-C", str(sdk), "add", "--", *paths], check=True)
                staged = subprocess.check_output(
                    ["git", "-C", str(sdk), "diff", "--cached", "--name-only"], text=True
                ).splitlines()
                self.assertEqual(set(staged), set(paths))
                self.assertEqual(installer.install_sdk(source, sdk, language), paths)
                subprocess.run(["git", "-C", str(sdk), "diff", "--exit-code"], check=True)

    def test_reject_invalid_pin_before_installing(self):
        for commit, workflow in [("main", WORKFLOW), (COMMIT, "steps: []\n"),
                                 (COMMIT, WORKFLOW + WORKFLOW)]:
            with self.subTest(commit=commit, workflow=workflow), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, sdk = root / "generated", root / "sdk"
                source.mkdir()
                (sdk / ".github/workflows").mkdir(parents=True)
                (sdk / ".github/workflows/ci.yml").write_text(workflow)
                (source / "ahp-codegen.lock.json").write_text(
                    json.dumps({"language": "python", "sourceCommit": commit})
                )
                with self.assertRaises(ValueError):
                    installer.install_sdk(source, sdk, "python")
                self.assertFalse((sdk / "src").exists())
