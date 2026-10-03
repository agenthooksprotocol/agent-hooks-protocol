from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_sdk_integration as runner


class SDKIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'agent-hooks-protocol'
        self.root.mkdir()
        self.reports = self.root / 'reports'
        for language in runner.LANGUAGES:
            path = self.root.parent / f'{language}-sdk/interop/adapter.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'language': language, **{
                key: ['adapter', key] for key in runner.MANIFEST_COMMANDS
            }}))

    def invoke(self, popen):
        output = io.StringIO()
        with patch.object(runner, 'ROOT', self.root), patch.object(
            runner.subprocess, 'Popen', popen
        ), patch.object(runner, 'prepare_adapters', return_value=[{'status': 'passed'}]), redirect_stdout(output):
            code = runner.main(['--reports-dir', str(self.reports), '--jobs', '7', '--timeout', '12'])
        return code, json.loads((self.reports / 'summary.json').read_text()), output.getvalue()

    def test_declared_build_commands_use_run_owned_directory(self):
        directory = self.root / 'isolated-bin'
        directory.mkdir()
        with patch.object(runner, 'run_command', return_value={'status': 'passed'}) as run:
            with redirect_stdout(io.StringIO()):
                results = runner.prepare_adapters(self.root, directory, self.reports, 12)
        self.assertEqual(len(results), 4)
        self.assertEqual(tuple(row['name'] for row in results),
                         ('go-elicitation', 'go-compaction', 'go-compaction-wire', 'rust-interop'))
        for call, name in zip(run.call_args_list, runner.GO_ADAPTERS):
            self.assertEqual(call.args, (
                ['go', 'build', '-o', str(directory / name), './cmd/' + name],
                self.root.parent / 'go-sdk', self.reports / f'build-go-{name}.log', 12))

        sdk = self.root.parent / 'rust-sdk'
        self.assertEqual(run.call_args_list[-1].args, (
            ['cargo', 'build', '--locked', '--bin', 'interop', '--target-dir', str(sdk / 'target')],
            sdk, self.reports / 'build-rust-interop.log', 12))

    def test_failed_preparation_blocks_suites_and_clears_stale_reports(self):
        self.reports.mkdir()
        (self.reports / 'matrix.json').write_text('{}')
        with patch.object(runner, 'ROOT', self.root), patch.object(
            runner, 'prepare_adapters', return_value=[{'status': 'failed'}]
        ), patch.object(runner, 'run_command') as run, redirect_stdout(io.StringIO()):
            code = runner.main(['--reports-dir', str(self.reports)])
        self.assertEqual(code, 1)
        run.assert_not_called()
        self.assertFalse((self.reports / 'matrix.json').exists())
        summary = json.loads((self.reports / 'summary.json').read_text())
        self.assertEqual(summary['suites'], [])
        self.assertEqual(summary['prerequisites'], [{'status': 'failed'}])

    def test_full_suite_inventory_and_actual_cli_flags(self):
        suites = {name: (cmd, cwd) for name, cmd, cwd in runner.suite_commands(self.root, self.reports, 7)}
        self.assertEqual(set(suites), {'tools-unit', 'interop-unit', 'matrix', 'lifecycle',
            'catalogue', 'elicitation', 'observation', 'compaction', 'compaction-wire', 'auth', 'sender-isolation'})
        self.assertIn('discover', suites['interop-unit'][0])
        self.assertIn('test_*.py', suites['interop-unit'][0])
        self.assertEqual(suites['interop-unit'][1], self.root / 'interop')
        for name, script, flags in [
            ('matrix', 'matrix.py', ['--jobs', '7']),
            ('lifecycle', 'lifecycle_matrix.py', ['--workers', '7']),
            ('catalogue', 'catalogue_matrix.py', ['--workers', '7']),
            ('elicitation', 'elicitation_matrix.py', []),
            ('observation', 'lifecycle_matrix.py', ['--workers', '7', '--scenarios', str(self.root / 'interop/observation-chain-scenarios.json')]),
            ('compaction', 'compaction_matrix.py', []),
            ('compaction-wire', 'compaction_wire_matrix.py', []),
            ('auth', 'test_auth_matrix.py', []),
            ('sender-isolation', 'sender_isolation_matrix.py', ['--workers', '7']),
        ]:
            self.assertEqual(suites[name][0], [sys.executable, str(self.root / 'interop' / script),
                *flags, '--report' if name == 'auth' else '--output', str(self.reports / f'{name}.json')])

    def test_success_logs_and_lean_stdout(self):
        directories = set()
        def execute(command, **kwargs):
            self.assertIn('AHP_GO_ADAPTER_DIR', kwargs['env'])
            self.assertTrue(Path(kwargs['env']['AHP_GO_ADAPTER_DIR']).is_dir())
            directories.add(kwargs['env']['AHP_GO_ADAPTER_DIR'])
            self.assertTrue(kwargs['start_new_session'])
            self.assertEqual(kwargs['stderr'], subprocess.STDOUT)
            kwargs['stdout'].write('verbose adapter details\n' * 50)
            return Mock(wait=Mock(return_value=0))
        popen = Mock(side_effect=execute)
        code, summary, output = self.invoke(popen)
        self.assertEqual(code, 0)
        self.assertEqual(summary['manifest_errors'], [])
        self.assertEqual(popen.call_count, 11)
        self.assertEqual(len(output.splitlines()), 12)
        self.assertNotIn('verbose adapter details', output)
        self.assertEqual(len(list(self.reports.glob('*.log'))), 11)
        self.assertEqual(len(directories), 1)
        self.assertTrue(all(not Path(directory).exists() for directory in directories))

    def test_failure_and_spawn_error_do_not_short_circuit(self):
        processes = [Mock(wait=Mock(return_value=9)), OSError('secret details')]
        processes += [Mock(wait=Mock(return_value=0)) for _ in range(9)]
        popen = Mock(side_effect=processes)
        code, summary, output = self.invoke(popen)
        self.assertEqual(code, 1)
        self.assertEqual(popen.call_count, 11)
        self.assertEqual(summary['suites'][0]['exit_code'], 9)
        self.assertEqual(summary['suites'][1]['status'], 'failed')
        self.assertEqual(summary['suites'][-1]['status'], 'passed')
        self.assertNotIn('secret details', output)

    def test_timeout_kills_process_group_and_continues(self):
        process = Mock(pid=123, wait=Mock(side_effect=[subprocess.TimeoutExpired('cmd', 12), -9]))
        popen = Mock(side_effect=[process] + [Mock(wait=Mock(return_value=0)) for _ in range(10)])
        with patch.object(runner.os, 'killpg') as kill:
            code, summary, _ = self.invoke(popen)
        kill.assert_called_once_with(123, signal.SIGKILL)
        self.assertEqual(process.wait.call_args_list[0].kwargs, {'timeout': 12})
        self.assertEqual(code, 1)
        self.assertEqual(summary['suites'][0]['status'], 'timeout')
        self.assertEqual(popen.call_count, 11)

    def test_missing_manifest_fails_even_if_commands_succeed(self):
        (self.root.parent / 'rust-sdk/interop/adapter.json').unlink()
        popen = Mock(return_value=Mock(wait=Mock(return_value=0)))
        code, summary, _ = self.invoke(popen)
        self.assertEqual(code, 1)
        self.assertIn('rust', summary['manifest_errors'][0])
        self.assertEqual(popen.call_count, 11)

    def test_all_manifest_command_keys_are_required(self):
        path = self.root.parent / 'python-sdk/interop/adapter.json'
        for key in runner.MANIFEST_COMMANDS:
            for invalid in (None, [], 'command', [''], [3]):
                manifest = {'language': 'python', **{k: ['cmd'] for k in runner.MANIFEST_COMMANDS}}
                manifest[key] = invalid
                path.write_text(json.dumps(manifest))
                self.assertEqual(runner.validate_manifests(self.root), [f'python: missing or invalid {key} command'])
        for invalid in ('{', '[]', '{"language": "wrong"}'):
            path.write_text(invalid)
            self.assertTrue(runner.validate_manifests(self.root))

    def test_stale_reports_are_removed(self):
        self.reports.mkdir()
        (self.reports / 'matrix.json').write_text('{"passed": true}')
        self.invoke(Mock(return_value=Mock(wait=Mock(return_value=1))))
        self.assertFalse((self.reports / 'matrix.json').exists())

    def test_jobs_and_timeout_must_be_positive(self):
        for flag in ('--jobs', '--timeout'):
            for value in ('0', '-1'):
                with redirect_stdout(io.StringIO()), patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit) as error:
                    runner.main(['--reports-dir', str(self.reports), flag, value])
                self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
