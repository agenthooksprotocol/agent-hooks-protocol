import os
from pathlib import Path
import unittest
from unittest.mock import patch

from adapter_builds import GO_ADAPTERS, GO_SDK, go_command, _builds


class AdapterBuildTests(unittest.TestCase):
    def setUp(self):
        _builds.clear()

    def tearDown(self):
        _builds.clear()

    def test_unselected_go_does_not_build(self):
        import elicitation_matrix, compaction_matrix, compaction_wire_matrix
        with patch('adapter_builds.subprocess.run') as run:
            for module in (elicitation_matrix, compaction_matrix, compaction_wire_matrix):
                self.assertIsNone(module.commands(('typescript',))['go'])
            run.assert_not_called()

    def test_standalone_prebuilds_native_once(self):
        with patch.dict(os.environ, {}, clear=True), patch('adapter_builds.subprocess.run') as run:
            for name in GO_ADAPTERS:
                command = go_command(name)
                self.assertEqual(len(command), 1)
                self.assertEqual(Path(command[0]).name, name)
                self.assertEqual(go_command(name), command)
                self.assertEqual(run.call_args.args[0],
                                 ['go', '-C', str(GO_SDK.resolve()), 'build', '-o', command[0], './cmd/' + name])
            self.assertEqual(run.call_count, len(GO_ADAPTERS))

    def test_prepared_uses_only_declared_directory(self):
        with patch.dict(os.environ, {'AHP_GO_ADAPTER_DIR': '/isolated/run/bin'}):
            for name in GO_ADAPTERS:
                self.assertEqual(go_command(name), [str(Path('/isolated/run/bin') / name)])
            with self.assertRaises(ValueError):
                go_command('../unknown')

    def test_isolated_source_root(self):
        with patch.dict(os.environ, {}, clear=True), patch('adapter_builds.subprocess.run') as run:
            go_command('elicitation', Path('/isolated/go-sdk'))
            self.assertEqual(run.call_args.args[0][2], '/isolated/go-sdk')

    def test_failed_build_is_not_cached(self):
        with patch.dict(os.environ, {}, clear=True), patch('adapter_builds.subprocess.run', side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                go_command('elicitation')
            self.assertEqual(_builds, {})
