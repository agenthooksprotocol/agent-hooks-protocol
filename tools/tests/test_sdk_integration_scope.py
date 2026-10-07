import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch


WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/sdk-integration.yml'


class ScopeTests(unittest.TestCase):
    def test_changed_paths_and_manual_dispatch(self):
        script = textwrap.dedent(WORKFLOW.read_text().split("python3 - <<'PYCODE'\n", 1)[1].split('          PYCODE', 1)[0])
        cases = [('workflow_dispatch', [], True), ('pull_request', ['docs/guide.md', 'README.md'], False)]
        cases += [('pull_request', [path], True) for path in (
            'spec/draft/x', 'schema/draft/x', 'fixtures/x', 'conformance/x',
            'interop/x', 'tools/x', '.github/workflows/sdk-integration.yml',
            '.github/workflows/ci.yml', '.github/workflows/sync-sdks.yml',
        )]
        cases += [('pull_request', ['.github/workflows/sync-website-docs.yml'], False)]
        # --no-renames represents moves as deletion plus addition, so moving
        # a contract file out of scope still requests compatibility.
        cases += [('pull_request', ['spec/old.md', 'docs/new.md'], True)]
        for event, paths, expected in cases:
            with self.subTest(event=event, paths=paths), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'output'
                with patch.dict(os.environ, EVENT=event, BASE='base', HEAD='head', GITHUB_OUTPUT=str(output)), \
                        patch.object(subprocess, 'check_output', return_value='\0'.join(paths).encode()) as git:
                    exec(compile(script, str(WORKFLOW), 'exec'), {})
                self.assertEqual(output.read_text(), f'needed={str(expected).lower()}\n')
                self.assertEqual(git.call_count, 0 if event == 'workflow_dispatch' else 1)
                if git.called:
                    self.assertIn('--no-renames', git.call_args.args[0])
                    self.assertEqual(git.call_args.args[0][-1], 'base...head')

    def test_required_aggregate_accepts_only_success_or_intentional_skip(self):
        script = textwrap.dedent(WORKFLOW.read_text().rsplit('        run: |\n', 1)[1])
        for scope, needed, resolve, shards, expected in (
            ('success', 'false', 'skipped', 'skipped', True),
            ('success', 'true', 'success', 'success', True),
            ('failure', 'false', 'skipped', 'skipped', False),
            ('cancelled', '', 'skipped', 'skipped', False),
            ('success', 'true', 'failure', 'skipped', False),
            ('success', 'true', 'success', 'failure', False),
            ('success', 'true', 'success', 'cancelled', False),
            ('success', 'true', 'skipped', 'skipped', False),
            ('success', '', 'skipped', 'skipped', False),
            ('success', 'false', 'success', 'success', False),
        ):
            env = dict(os.environ, SCOPE_RESULT=scope, NEEDED=needed, RESOLVE_RESULT=resolve, SHARDS_RESULT=shards)
            result = subprocess.run(['bash', '-e', '-c', script], env=env, capture_output=True)
            self.assertEqual(result.returncode == 0, expected, (scope, needed, resolve, shards))


if __name__ == '__main__':
    unittest.main()
