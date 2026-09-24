#!/usr/bin/env python3
"""Run installed sibling SDKs through the shared harness.

Go matrix adapters are built into a fresh run-owned directory before tests.
Rust interop is built before the matrix so nested stdio startup never runs Cargo.

--jobs controls runners that expose concurrency flags. Elicitation and compaction
currently fix their own pools at four; suites run sequentially. Observation wire
and chain coverage uses lifecycle's scenarios CLI. Upload reference tests supplement
real uploads in lifecycle and elicitation. Use a Python environment with jsonschema.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ('typescript', 'python', 'go', 'rust')
GO_ADAPTERS = ('elicitation', 'compaction', 'compaction-wire')
MANIFEST_COMMANDS = ('client', 'server', 'lifecycleClient', 'lifecycleServer')


def validate_manifests(root: Path) -> list[str]:
    errors = []
    for language in LANGUAGES:
        path = root.parent / f'{language}-sdk/interop/adapter.json'
        try:
            manifest = json.loads(path.read_text())
        except (OSError, ValueError):
            errors.append(f'{language}: missing or invalid adapter.json')
            continue
        if not isinstance(manifest, dict):
            errors.append(f'{language}: adapter must be an object')
            continue
        if manifest.get('language') != language:
            errors.append(f'{language}: incorrect manifest language')
        for key in MANIFEST_COMMANDS:
            command = manifest.get(key)
            if not isinstance(command, list) or not command or not all(
                isinstance(arg, str) and arg.strip() for arg in command
            ):
                errors.append(f'{language}: missing or invalid {key} command')
    return errors


def suite_commands(root: Path, reports: Path, jobs: int):
    """Use public CLIs without narrowing language/transport/auth grids."""
    python = sys.executable
    interop = root / 'interop'
    suites = [
        ('tools-unit', [python, '-m', 'unittest', 'discover', '-s', 'tools/tests', '-q'], root),
        ('interop-unit', [python, '-m', 'unittest', 'discover', '-s', '.', '-p', 'test_*.py', '-q'], interop),
    ]
    definitions = [
        ('matrix', 'matrix.py', ['--jobs', str(jobs)]),
        ('lifecycle', 'lifecycle_matrix.py', ['--workers', str(jobs)]),
        ('catalogue', 'catalogue_matrix.py', ['--workers', str(jobs)]),
        ('elicitation', 'elicitation_matrix.py', []),
        ('observation', 'lifecycle_matrix.py', ['--workers', str(jobs), '--scenarios', str(interop / 'observation-chain-scenarios.json')]),
        ('compaction', 'compaction_matrix.py', []),
        ('compaction-wire', 'compaction_wire_matrix.py', []),
        ('auth', 'test_auth_matrix.py', []),
        ('sender-isolation', 'sender_isolation_matrix.py', ['--workers', str(jobs)]),
    ]
    for name, script, flags in definitions:
        report_flag = '--report' if name == 'auth' else '--output'
        suites.append((name, [python, str(interop / script), *flags,
                              report_flag, str(reports / f'{name}.json')], root))
    return suites


def run_command(command, cwd: Path, log: Path, timeout: int, env=None) -> dict:
    started = time.monotonic()
    status = 'failed'
    code = None
    with log.open('w') as output:
        try:
            process = subprocess.Popen(command, cwd=cwd, stdout=output,
                                       stderr=subprocess.STDOUT, start_new_session=True,
                                       **({'env': env} if env is not None else {}))
            try:
                code = process.wait(timeout=timeout)
                status = 'passed' if code == 0 else 'failed'
            except subprocess.TimeoutExpired:
                # Include adapter descendants in timeout cleanup.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                status = 'timeout'
                output.write(f'\nCommand exceeded {timeout} seconds.\n')
        except OSError as error:
            output.write(f'Unable to execute command: {type(error).__name__}\n')
    return {'status': status, 'exit_code': code,
            'seconds': round(time.monotonic() - started, 3), 'log': str(log)}


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def prepare_adapters(root, directory, reports, timeout):
    """Build declared adapters before timed protocol exchanges."""
    results = []
    for name in GO_ADAPTERS:
        result = {'name': 'go-' + name, **run_command(
            ['go', 'build', '-o', str(directory / name), './cmd/' + name],
            root.parent / 'go-sdk', reports / f'build-go-{name}.log', timeout)}
        results.append(result)
        print(f"build-go-{name}: {result['status']}", flush=True)
    name = 'rust-interop'
    sdk = root.parent / 'rust-sdk'
    result = {'name': name, **run_command(
        ['cargo', 'build', '--locked', '--bin', 'interop', '--target-dir', str(sdk / 'target')],
        sdk, reports / 'build-rust-interop.log', timeout)}
    results.append(result)
    print(f"build-{name}: {result['status']}", flush=True)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports-dir', type=Path, required=True)
    parser.add_argument('--jobs', type=positive_int, default=4)
    parser.add_argument('--timeout', type=positive_int, default=1800,
                        help='maximum seconds per suite command (default: 1800)')
    args = parser.parse_args(argv)
    reports = args.reports_dir.resolve()
    reports.mkdir(parents=True, exist_ok=True)
    errors = validate_manifests(ROOT)
    # Remove stale reports even when preparation fails before any suite starts.
    for name, _, _ in suite_commands(ROOT, reports, args.jobs):
        (reports / f'{name}.json').unlink(missing_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='ahp-go-adapters-') as directory:
        prerequisites = prepare_adapters(ROOT, Path(directory), reports, args.timeout)
        env = {**os.environ, 'AHP_GO_ADAPTER_DIR': directory,
               'AHP_RUST_INTEROP': str(ROOT.parent / 'rust-sdk/target/debug/interop')}
        if all(row['status'] == 'passed' for row in prerequisites):
            for name, command, cwd in suite_commands(ROOT, reports, args.jobs):
                result = {'name': name, **run_command(command, cwd, reports / f'{name}.log', args.timeout, env=env)}
                results.append(result)
                print(f"{name}: {result['status']}", flush=True)
    failed = bool(errors) or any(row['status'] != 'passed' for row in prerequisites + results)
    summary = {'status': 'failed' if failed else 'passed', 'manifest_errors': errors,
               'prerequisites': prerequisites, 'suites': results}
    (reports / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(f"SDK integration: {summary['status']}; {len(errors)} manifest errors; reports: {reports}")
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
