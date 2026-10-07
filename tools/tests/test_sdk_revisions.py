import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import resolve_sdk_revisions as resolver
import run_sdk_integration as runner


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(resolver.MANIFEST.read_text())

    def test_main_snapshots_each_repository_once(self):
        replies = [{'object': {'type': 'commit', 'sha': str(i) * 40}}
                   for i in range(1, 5)]
        with patch.object(resolver, 'api', side_effect=replies) as api, redirect_stdout(io.StringIO()):
            resolved = resolver.resolve(copy.deepcopy(self.manifest), 'main')
        self.assertEqual(api.call_count, 4)
        for i, (language, sdk) in enumerate(resolved['sdks'].items(), 1):
            self.assertEqual(sdk['revision'], str(i) * 40)
            self.assertEqual(api.call_args_list[i - 1].args,
                             (f'repos/agenthooksprotocol/{language}-sdk/git/ref/heads/main',))

    def test_stable_release_selection_is_semver_not_api_order(self):
        def release(tag, **kwargs):
            return dict(tag_name=tag, draft=False, prerelease=False, published_at='date', **kwargs)
        rows = [release('v0.9.0'), release('v0.10.0'), release('v1.0.0-rc.1')]
        rows += [{**release('v9.0.0'), 'draft': True}]
        with patch.object(resolver, 'api', return_value=[rows]):
            self.assertEqual(resolver.latest_release('repo', 'go'), 'v0.10.0')
        with patch.object(resolver, 'api', return_value=[[release('agenthooksprotocol-v0.1.0')]]):
            self.assertEqual(resolver.latest_release('repo', 'typescript'), 'agenthooksprotocol-v0.1.0')

    def test_annotated_tag_resolves_to_commit(self):
        with patch.object(resolver, 'api', side_effect=[
            {'object': {'type': 'tag', 'sha': 'a' * 40}},
            {'object': {'type': 'commit', 'sha': 'b' * 40}},
        ]):
            self.assertEqual(resolver.tag_commit('repo', 'v1.0.0'), 'b' * 40)

    def test_release_requires_completed_run_and_publish_job(self):
        run = dict(head_sha='a' * 40, head_branch='main', event='push',
                   path='.github/workflows/release.yml', status='completed',
                   conclusion='success', id=1, run_attempt=1)
        for job, expected in [('publish', True), ('release-please', False)]:
            with patch.object(resolver, 'api', side_effect=[
                [{'workflow_runs': [run]}],
                [{'jobs': [dict(name=job, status='completed', conclusion='success')]}],
            ]):
                self.assertEqual(resolver.release_succeeded('repo', 'python', 'a' * 40), expected)
        with patch.object(resolver, 'api', return_value=[{'workflow_runs': [{**run, 'status': 'in_progress'}]}]):
            self.assertFalse(resolver.release_succeeded('repo', 'go', 'a' * 40))

    def test_invalid_repository_and_unready_release_fail_closed(self):
        invalid = copy.deepcopy(self.manifest)
        invalid['sdks']['go']['repository'] = 'unexpected/repo'
        with self.assertRaises(ValueError):
            resolver.validate_manifest(invalid)
        with patch.object(resolver, 'latest_release', return_value='v1.0.0'), \
                patch.object(resolver, 'tag_commit', return_value='a' * 40), \
                patch.object(resolver, 'release_succeeded', return_value=False), \
                self.assertRaises(ValueError):
            resolver.resolve(self.manifest, 'release')

    def test_runner_records_explicit_snapshot_and_rejects_wrong_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input.json'
            source.write_text(json.dumps(self.manifest))
            heads = [sdk['revision'] + '\n' for sdk in self.manifest['sdks'].values()]
            with patch.object(runner.subprocess, 'check_output', side_effect=heads):
                runner.record_sdk_revisions(source, root, root)
            self.assertEqual(json.loads((root / 'sdk-revisions.json').read_text()), self.manifest)
            with patch.object(runner.subprocess, 'check_output', return_value='f' * 40), self.assertRaises(ValueError):
                runner.record_sdk_revisions(source, root, root)


if __name__ == '__main__':
    unittest.main()
