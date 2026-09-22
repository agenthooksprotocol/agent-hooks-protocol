"""Regression checks for the shared local/CI artifact pipeline."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class SdkGenerationTests(unittest.TestCase):
    def test_staging_formats_before_packaging_and_uses_content_lock(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if 'generate' in command:
                Path(command[command.index('--output') + 1]).write_text('source\n')
            elif command[0] in ('gofmt', 'rustfmt'):
                Path(command[-1]).write_text('formatted\n')
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch.object(sys, 'argv', ['generate_sdk.py', '--output-dir', directory]), \
                    patch('subprocess.run', side_effect=run), \
                    patch('subprocess.check_output', return_value=b'{"target_directory":"/unused"}'), \
                    contextlib.redirect_stdout(io.StringIO()):
                runpy.run_path(str(ROOT / 'tools/generate_sdk.py'), run_name='__main__')
            manifest = ROOT / 'schema/draft/manifest.json'
            for language, extension in [('typescript', 'ts'), ('python', 'py'), ('go', 'go'), ('rust', 'rs')]:
                with self.subTest(language=language):
                    folder = output / language
                    expected = 'formatted\n' if language in ('go', 'rust') else 'source\n'
                    self.assertEqual(expected, (folder / ('generated.' + extension)).read_text())
                    lock = json.loads((folder / 'ahp-codegen.lock.json').read_text())
                    self.assertEqual(language, lock['language'])
                    self.assertEqual(hashlib.sha256(manifest.read_bytes()).hexdigest(), lock['schemaManifestSha256'])
                    self.assertEqual(json.loads(manifest.read_text())['documents'], lock['documents'])
                    self.assertTrue((folder / ('schemas.ts' if language == 'typescript' else 'schemas.json')).is_file())
            self.assertEqual(1, sum(command[:2] == ['cargo', 'build'] for command in calls))
            self.assertTrue(any(command[:2] == ['rustfmt', '+1.88.0'] for command in calls))

    def test_ci_uses_shared_pipeline(self):
        workflow = (ROOT / '.github/workflows/sync-sdks.yml').read_text()
        self.assertIn('python3 tools/generate_sdk.py --output-dir generated', workflow)
        self.assertNotIn('jq -n', workflow)
        self.assertIn('output: packages/sdk/src/draft/generated.ts', workflow)
        self.assertIn('${{ matrix.directory }}/ahp-codegen.lock.json', workflow)

    def test_pinned_sources_disable_line_ending_conversion(self):
        result = subprocess.check_output(['git', 'check-attr', 'text', '--', 'upstream/mcp/2025-11-25/schema.json', 'upstream/mcp/2025-11-25/schema.ts'], cwd=ROOT, text=True)
        self.assertEqual(2, result.count(': text: unset'))
