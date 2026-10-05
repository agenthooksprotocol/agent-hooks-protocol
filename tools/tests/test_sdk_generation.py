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
                target = Path(command[command.index('--output') + 1])
                if 'go-facade' in command:
                    target = target / 'event/generated.go'
                    target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('source\n')
            elif Path(command[0]).name == 'gofmt':
                for source in command[2:]:
                    Path(source).write_text('formatted\n')
            elif command[0] in ('rustfmt', 'npx') or 'ruff' in command:
                Path(command[-1]).write_text('formatted\n')
            return subprocess.CompletedProcess(command, 0)

        def check_output(command, **kwargs):
            if command[0] == 'git':
                return 'a' * 40 + '\n'
            if command == ['go', 'env', 'GOROOT']:
                self.assertEqual('go1.27.0+auto', kwargs['env']['GOTOOLCHAIN'])
                return '/unused/go\n'
            if 'ruff' in command:
                return 'ruff 0.12.12\n'
            return b'{"target_directory":"/unused"}'

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch.object(sys, 'argv', ['generate_sdk.py', '--output-dir', directory]), \
                    patch('subprocess.run', side_effect=run), \
                    patch('subprocess.check_output', side_effect=check_output), \
                    contextlib.redirect_stdout(io.StringIO()):
                runpy.run_path(str(ROOT / 'tools/generate_sdk.py'), run_name='__main__')
            manifest = ROOT / 'schema/draft/manifest.json'
            for language, extension in [('typescript', 'ts'), ('python', 'py'), ('go', 'go'), ('rust', 'rs')]:
                with self.subTest(language=language):
                    folder = output / language
                    expected = 'formatted\n'
                    self.assertEqual(expected, (folder / ('generated.' + extension)).read_text())
                    lock = json.loads((folder / 'ahp-codegen.lock.json').read_text())
                    self.assertEqual(language, lock['language'])
                    self.assertEqual('a' * 40, lock['sourceCommit'])
                    self.assertEqual('agenthooksprotocol/agent-hooks-protocol', lock['sourceRepository'])
                    self.assertEqual(hashlib.sha256(manifest.read_bytes()).hexdigest(), lock['schemaManifestSha256'])
                    self.assertEqual(json.loads(manifest.read_text())['documents'], lock['documents'])
                    self.assertTrue((folder / ('schemas.ts' if language == 'typescript' else 'schemas.json')).is_file())
            self.assertEqual('formatted\n', (output / 'go/event/generated.go').read_text())
            self.assertEqual((output / 'go/schemas.json').read_bytes(), (output / 'go/internal/canonical/schemas.json').read_bytes())
            self.assertEqual('formatted\n', (output / 'typescript/schemas.ts').read_text())
            self.assertEqual(2, sum(command[:3] == ['npx', '--yes', 'prettier@3.6.2'] for command in calls))
            self.assertTrue(any(command[1:7] == ['-m', 'ruff', 'format', '--isolated', '--target-version', 'py311'] for command in calls))
            self.assertEqual(1, sum(command[:2] == ['cargo', 'build'] for command in calls))
            self.assertTrue(any(command[:4] == ['rustfmt', '+1.88.0', '--edition', '2024'] for command in calls))

    def test_rejects_unpinned_python_formatter(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(sys, 'argv', ['generate_sdk.py', '--output-dir', directory]), \
                patch('subprocess.check_output', return_value='ruff 0.13.0\n'), \
                patch('subprocess.run') as run:
            with self.assertRaisesRegex(SystemExit, 'ruff==0.12.12'):
                runpy.run_path(str(ROOT / 'tools/generate_sdk.py'), run_name='__main__')
            run.assert_not_called()

    def test_ci_uses_shared_pipeline(self):
        workflow = (ROOT / '.github/workflows/sync-sdks.yml').read_text()
        self.assertIn('python3 tools/generate_sdk.py --output-dir generated', workflow)
        self.assertNotIn('jq -n', workflow)
        for name in ('sync-sdks.yml', 'sdk-integration.yml'):
            contents = (ROOT / '.github/workflows' / name).read_text()
            self.assertIn('ruff==0.12.12', contents)
            self.assertIn('actions/setup-node@', contents)
        self.assertIn('output: packages/sdk/src/draft/generated.ts', workflow)
        self.assertIn('${{ matrix.directory }}/ahp-codegen.lock.json', workflow)

    def test_pinned_sources_disable_line_ending_conversion(self):
        result = subprocess.check_output(['git', 'check-attr', 'text', '--', 'upstream/mcp/2025-11-25/schema.json', 'upstream/mcp/2025-11-25/schema.ts'], cwd=ROOT, text=True)
        self.assertEqual(2, result.count(': text: unset'))
