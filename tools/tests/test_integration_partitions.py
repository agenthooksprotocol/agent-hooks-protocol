"""Public single-suite partition and shared adapter bundle contract."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_sdk_integration as runner


class IntegrationPartitionTests(unittest.TestCase):
    def test_named_suite_retains_full_grid_and_passes_partition(self):
        root = Path('/example/protocol')
        for suite, count in [('lifecycle', 4), ('observation', 3)]:
            for index in range(count):
                rows = runner.suite_commands(root, root/'reports', 4,
                    suite=suite, shard_index=index, shard_count=count)
                self.assertEqual([row[0] for row in rows], [suite])
                command = rows[0][1]
                for flag, value in [('--shard-index', str(index)),
                                    ('--shard-count', str(count)), ('--suite', suite)]:
                    self.assertEqual(command[command.index(flag)+1], value)
                self.assertFalse(any(flag in command for flag in
                                     ('--client', '--server', '--auth', '--transport')))
                if suite == 'observation':
                    self.assertIn(str(root/'interop/observation-chain-scenarios.json'), command)

    def test_every_other_suite_runs_once(self):
        for suite in runner.SUITES:
            rows = runner.suite_commands(Path('/p'), Path('/r'), 1, suite=suite)
            self.assertEqual([row[0] for row in rows], [suite])
            if suite not in runner.SHARDABLE_SUITES:
                with self.assertRaises(ValueError):
                    runner.suite_commands(Path('/p'), Path('/r'), 1, suite=suite, shard_count=2)

    def test_invalid_partitions_are_rejected_before_building(self):
        cases = [('--shard-count', '0'), ('--shard-index', '-1'),
                 ('--suite', 'lifecycle', '--shard-index', '4', '--shard-count', '4'),
                 ('--shard-count', '2'), ('--suite', 'matrix', '--shard-count', '2'),
                 ('--suite', 'matrix', '--suite-group', 'core'), ('--prepare-only',)]
        with patch.object(runner, 'prepare_adapters') as build:
            for flags in cases:
                with self.subTest(flags=flags), redirect_stdout(io.StringIO()), patch('sys.stderr', new=io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        runner.main(['--reports-dir', '/unused', *flags])
                    self.assertEqual(error.exception.code, 2)
            build.assert_not_called()

    def invoke(self, root, reports, flags):
        with patch.object(runner, 'ROOT', root), patch.object(runner, 'validate_manifests', return_value=[]), redirect_stdout(io.StringIO()):
            code = runner.main(['--reports-dir', str(reports), *flags])
        return code, json.loads((reports/'summary.json').read_text())

    def create_bundle(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        for name in (*runner.GO_ADAPTERS, 'rust-interop'):
            binary = directory/name
            binary.write_text('#!/bin/sh\nexit 0\n')
            binary.chmod(0o755)

    def test_reuse_requires_all_executable_binaries_and_never_builds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()/'protocol'; root.mkdir()
            bundle = root/'bin'
            self.create_bundle(bundle)
            for absent in (None, 'interop', 'rust-interop'):
                if absent:
                    (bundle/absent).unlink()
                with patch.object(runner, 'prepare_adapters') as build, patch.object(runner, 'run_command', return_value={'status': 'passed'}) as run:
                    code, summary = self.invoke(root, root/'reports', ['--suite', 'matrix', '--adapter-dir', str(bundle)])
                    self.assertEqual(code, int(absent is not None))
                    self.assertEqual(summary['expected_suites'], ['matrix'])
                    build.assert_not_called()
                    if absent:
                        run.assert_not_called()
                    else:
                        self.assertEqual(run.call_args.kwargs['env']['AHP_RUST_INTEROP'], str(bundle/'rust-interop'))
                        self.assertEqual(run.call_args.kwargs['env']['AHP_GO_ADAPTER_DIR'], str(bundle))
                self.create_bundle(bundle)
            (bundle/'interop').chmod(0o644)
            self.assertEqual(runner.reuse_adapters(bundle)[3]['status'], 'failed')

    def test_missing_partition_report_fails_even_after_successful_command(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()/'protocol'; root.mkdir()
            bundle = root/'bin'; self.create_bundle(bundle)
            with patch.object(runner, 'run_command', return_value={'status': 'passed'}):
                code, summary = self.invoke(root, root/'reports', ['--suite', 'lifecycle',
                    '--shard-index', '1', '--shard-count', '4', '--adapter-dir', str(bundle)])
            self.assertEqual(code, 1)
            self.assertEqual(summary['suite'], 'lifecycle')
            self.assertEqual((summary['shard_index'], summary['shard_count']), (1, 4))
            self.assertEqual(summary['suites'][0]['status'], 'failed')
            self.assertIn('coverage_error', summary['suites'][0])

    def test_prepare_only_builds_and_exports_rust_without_suites(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()/'protocol'; root.mkdir()
            bundle = root/'bin'
            rust = root.parent/'rust-sdk/target/debug/interop'
            rust.parent.mkdir(parents=True); rust.write_text('rust'); rust.chmod(0o755)
            def build(root_arg, directory, reports, timeout):
                self.assertEqual(directory, bundle)
                self.create_bundle(directory)
                (directory/'rust-interop').unlink()
                return [{'status': 'passed'}]
            with patch.object(runner, 'prepare_adapters', side_effect=build), patch.object(runner, 'run_command') as run:
                code, summary = self.invoke(root, root/'reports', ['--prepare-only', '--adapter-dir', str(bundle)])
                self.assertEqual(code, 0)
                self.assertEqual(summary['suites'], [])
                self.assertEqual(summary['expected_suites'], [])
                self.assertTrue(summary['prepare_only'])
                self.assertEqual((bundle/'rust-interop').read_text(), 'rust')
                self.assertEqual(len(summary['prerequisites']), 7)
                run.assert_not_called()

    def test_failed_preparation_and_missing_rust_export_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()/'protocol'; root.mkdir()
            for status in ['failed', 'passed']:
                with patch.object(runner, 'prepare_adapters', return_value=[{'status': status}]), patch.object(runner, 'run_command') as run:
                    code, summary = self.invoke(root, root/'reports', ['--prepare-only', '--adapter-dir', str(root/'bin')])
                    self.assertEqual(code, 1)
                    self.assertEqual(summary['status'], 'failed')
                    run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
