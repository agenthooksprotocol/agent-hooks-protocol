import os
import re
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

    def test_shard_deadlines_preserve_coverage_and_budget_cleanup(self):
        workflow = WORKFLOW.read_text()
        shards = workflow.split('  sdk-integration-shards:\n', 1)[1].split('\n  sdk-integration:\n', 1)[0]
        job_minutes = int(re.search(r'^    timeout-minutes: (\d+)$', shards, re.M).group(1))
        step_minutes = [int(value) for value in re.findall(r'^        timeout-minutes: (\d+)$', shards, re.M)]
        self.assertGreaterEqual(job_minutes, sum(step_minutes) + 5)
        self.assertLessEqual(job_minutes, 150)
        python_step = shards.split('      - name: Install and test Python SDK\n', 1)[1].split('      - name:', 1)[0]
        integration_step = shards.split('      - name: Run SDK integration shard\n', 1)[1].split('      - name:', 1)[0]
        for step, deadline, budget in ((python_step, 45, 47), (integration_step, 40, 42)):
            self.assertIn(f'timeout-minutes: {budget}', step)
            self.assertIn(f'timeout --kill-after=30s {deadline}m', step)
            self.assertIn('2>&1 | tee', step)
        self.assertIn('.venv/bin/python -m unittest discover -s tests', python_step)
        self.assertIn('--sdk-revisions "$GITHUB_WORKSPACE/reports/sdk-revisions.json"', integration_step)
        self.assertIn('--suite-group "${{ matrix.suite-group }}"', integration_step)
        self.assertNotIn('continue-on-error', shards)

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
