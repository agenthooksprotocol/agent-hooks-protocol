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
        self.manifest = {"version": 1, "sdks": {language: {
            "path": f"{language}-sdk", "repository": f"agenthooksprotocol/{language}-sdk",
            "revision": "a" * 40,
        } for language in resolver.LANGUAGES}}

    def test_main_snapshots_each_repository_once(self):
        replies = [{'object': {'type': 'commit', 'sha': str(i) * 40}}
                   for i in range(1, 5)]
        with patch.object(resolver, 'api', side_effect=replies) as api, redirect_stdout(io.StringIO()):
            resolved = resolver.resolve()
        self.assertEqual(api.call_count, 4)
        for i, (language, sdk) in enumerate(resolved['sdks'].items(), 1):
            self.assertEqual(sdk['revision'], str(i) * 40)
            self.assertEqual(api.call_args_list[i - 1].args,
                             (f'repos/agenthooksprotocol/{language}-sdk/git/ref/heads/main',))

    def test_invalid_repository_and_main_ref_fail_closed(self):
        invalid = copy.deepcopy(self.manifest)
        invalid['sdks']['go']['repository'] = 'unexpected/repo'
        with self.assertRaises(ValueError):
            resolver.validate_manifest(invalid)
        for obj in ({'type': 'tag', 'sha': 'a' * 40}, {'type': 'commit', 'sha': 'main'}):
            with patch.object(resolver, 'api', return_value={'object': obj}), self.assertRaises(ValueError):
                resolver.resolve()

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
