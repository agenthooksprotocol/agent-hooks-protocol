import os
from pathlib import Path
import unittest
from unittest.mock import patch

from adapter_builds import GO_ADAPTERS, GO_SDK, go_command, _builds, prepared_go_manifest


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


class PreparedManifestTests(unittest.TestCase):
    def manifest(self):
        return {'language': 'go',
                'client': ['go', 'run', './cmd/interop', 'client'],
                'server': ['go', 'run', './cmd/interop', 'server'],
                'lifecycleClient': ['go', 'run', './cmd/lifecycle-client'],
                'lifecycleServer': ['go', 'run', './cmd/lifecycle-server']}

    def test_exact_commands_and_no_stale_fallback(self):
        manifest = self.manifest()
        roles = ('client', 'server', 'lifecycleClient', 'lifecycleServer')
        with patch.dict(os.environ, {'AHP_GO_ADAPTER_DIR': '/missing/run-owned'}), patch('adapter_builds.subprocess.run') as run:
            prepared = prepared_go_manifest(manifest, roles)
            self.assertEqual(prepared['client'], ['/missing/run-owned/interop', 'client'])
            self.assertEqual(prepared['server'], ['/missing/run-owned/interop', 'server'])
            self.assertEqual(prepared['lifecycleClient'], ['/missing/run-owned/lifecycle-client'])
            self.assertEqual(prepared['lifecycleServer'], ['/missing/run-owned/lifecycle-server'])
            run.assert_not_called()
        self.assertEqual(manifest, self.manifest())

    def test_rejects_arbitrary_launchers_for_every_role(self):
        for role in ('client', 'server', 'lifecycleClient', 'lifecycleServer'):
            for command in (['sh', '-c', 'go run ./cmd/interop'], ['go', 'run', './cmd/other'], self.manifest()[role] + ['--extra'], None):
                with self.subTest(role=role, command=command), patch.dict(os.environ, {'AHP_GO_ADAPTER_DIR': '/prepared'}):
                    manifest = self.manifest(); manifest[role] = command
                    with self.assertRaisesRegex(ValueError, 'unexpected Go'):
                        prepared_go_manifest(manifest, (role,))

    def test_unprepared_and_other_languages_are_unchanged(self):
        manifest = self.manifest()
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(prepared_go_manifest(manifest, ('client',)), manifest)
        manifest['language'] = 'python'
        with patch.dict(os.environ, {'AHP_GO_ADAPTER_DIR': '/prepared'}):
            self.assertEqual(prepared_go_manifest(manifest, ('client',)), manifest)
