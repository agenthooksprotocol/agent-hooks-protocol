"""Coordinated validation must exercise locked optional SDK dependencies."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github/actions/setup-sdk-check/action.yml"


class SDKDependencySetupTests(unittest.TestCase):
    def test_python_sdk_environment_uses_lock_and_all_declared_extras(self):
        action = ACTION.read_text()
        self.assertIn("astral-sh/setup-uv@v7", action)
        self.assertIn("version: '0.13.4'", action)
        self.assertIn("uv sync --locked --all-extras --python python3", action)
        self.assertIn("working-directory: python-sdk", action)

    def test_no_unlocked_editable_or_typing_install_overrides_sdk_lock(self):
        action = ACTION.read_text()
        self.assertNotIn("pip install -e", action)
        self.assertNotIn("mypy>=", action)


if __name__ == "__main__":
    unittest.main()
