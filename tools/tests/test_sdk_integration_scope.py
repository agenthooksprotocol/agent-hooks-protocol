import os
import json
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
            'interop/x', 'tools/x', '.github/actions/setup-sdk-check/action.yml', '.github/workflows/sdk-integration.yml',
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

    def test_parallel_jobs_preserve_coverage_and_bound_deadlines(self):
        workflow = WORKFLOW.read_text()
        shards = workflow.split('  sdk-integration-shards:\n', 1)[1].split('\n  sdk-integration:\n', 1)[0]
        self.assertIn('timeout-minutes: 15', shards)
        self.assertIn('--timeout 600', shards)
        self.assertIn('--adapter-dir "$GITHUB_WORKSPACE/adapters"', shards)
        self.assertIn('--sdk-revisions "$GITHUB_WORKSPACE/reports/sdk-revisions.json"', shards)
        self.assertNotIn('continue-on-error', workflow)
        self.assertNotIn('Install and test Python SDK', shards)
        rows = re.findall(r'\{suite: ([\w-]+), shard: (\d+), count: (\d+)\}', shards)
        expected = {'lifecycle': 4, 'observation': 3, 'matrix': 2, 'catalogue': 1,
                    'elicitation': 1, 'compaction': 1, 'compaction-wire': 1,
                    'auth': 1, 'sender-isolation': 1}
        self.assertEqual(len(rows), sum(expected.values()))
        for suite, count in expected.items():
            self.assertEqual([(int(index), int(total)) for name, index, total in rows if name == suite],
                             [(index, count) for index in range(count)])
        for job in ('python-tests', 'interop-unit'):
            section = re.split(r'\n  [\w-]+:\n', workflow.split('  ' + job + ':\n', 1)[1], maxsplit=1)[0]
            if job == 'python-tests':
                self.assertIn('timeout --kill-after=30s \"$TEST_TIMEOUT\"', section)
                self.assertIn('matrix.shard == 0 && 45 || 15', section)
                self.assertIn('--isolate-test test_content_lifetime.ContentLifetimeTests.test_more_than_4096_calls_release_sources_and_keep_results', section)
            else:
                self.assertIn('timeout --kill-after=30s 10m', section)
                self.assertIn('--duration-file ../tools/ci-interop-durations.json', section)
            self.assertIn('run_unit_shard.py', section)
        self.assertIn('go test -count=1 -vet=off ./...', workflow)
        self.assertIn('node --test --test-concurrency=2 --test-shard=', workflow)
        self.assertEqual(workflow.count('tools/generate_sdk.py --all\n'), 1)
        self.assertIn('tools/check_ci_reports.py --reports-dir reports', workflow)

    def test_source_archive_preserves_all_nul_delimited_paths(self):
        import tarfile
        block = WORKFLOW.read_text().split('      - name: Package complete generated SDK sources\n', 1)[1]
        script = textwrap.dedent(block.split("python3 - <<'PYCODE'\n", 1)[1].split('          PYCODE', 1)[0])
        with patch.object(subprocess, 'check_output', return_value=b'a.py\0nested/b.py\0'), \
                patch.object(tarfile, 'open') as archive:
            exec(compile(script, str(WORKFLOW), 'exec'), {})
        calls = archive.return_value.__enter__.return_value.add.call_args_list
        self.assertEqual(len(calls), 8)
        self.assertEqual({str(call.args[0]) for call in calls},
                         {f'{language}-sdk/{name}' for language in ('python', 'typescript', 'go', 'rust')
                          for name in ('a.py', 'nested/b.py')})

    def test_required_aggregate_accepts_only_success_or_intentional_skip(self):
        block = WORKFLOW.read_text().split('      - name: Require every SDK check to succeed\n', 1)[1]
        script = textwrap.dedent(block.split("python3 - <<'PYCODE'\n", 1)[1].split('          PYCODE', 1)[0])
        downstream = ('resolve-sdks', 'generate', 'build-runtime', 'python-tests',
                      'language-tests', 'interop-unit', 'sdk-integration-shards')
        for needed, default in (('true', 'success'), ('false', 'skipped')):
            jobs = {name: {'result': default} for name in downstream}
            jobs['scope'] = {'result': 'success', 'outputs': {'needed': needed}}
            with patch.dict(os.environ, RESULTS=json.dumps(jobs)):
                exec(compile(script, str(WORKFLOW), 'exec'), {})
            for name in ('scope', *downstream):
                for invalid in ('failure', 'cancelled', 'skipped' if default == 'success' else 'success'):
                    if name == 'scope' and invalid == 'success':
                        continue
                    bad = json.loads(json.dumps(jobs))
                    bad[name]['result'] = invalid
                    with self.subTest(needed=needed, name=name, result=invalid), \
                            patch.dict(os.environ, RESULTS=json.dumps(bad)), self.assertRaises(AssertionError):
                        exec(compile(script, str(WORKFLOW), 'exec'), {})
        jobs['scope']['outputs']['needed'] = ''
        with patch.dict(os.environ, RESULTS=json.dumps(jobs)), self.assertRaises(AssertionError):
            exec(compile(script, str(WORKFLOW), 'exec'), {})


if __name__ == '__main__':
    unittest.main()
