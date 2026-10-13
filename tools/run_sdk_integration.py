#!/usr/bin/env python3
"""Run installed sibling SDKs through the shared harness.

Go matrix adapters are built into a fresh run-owned directory before tests.
Rust interop is built before the matrix so nested stdio startup never runs Cargo.

--jobs is a maximum for runners that expose concurrency flags, capped by CPU
affinity (or host CPU count when affinity is unavailable). Effective jobs are
logged and recorded in summary.json. Elicitation and compaction
currently fix their own pools at four. Use --suite for one named suite, with
zero-based --shard-index and positive --shard-count for lifecycle/observation.
--suite-group remains available (all by default, or disjoint core and extended).
--prepare-only --adapter-dir builds a reusable adapter bundle; --adapter-dir
without --prepare-only validates and reuses it without rebuilding. Observation wire
and chain coverage uses lifecycle's scenarios CLI. Upload reference tests supplement
real uploads in lifecycle and elicitation. Use a Python environment with jsonschema.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
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
GO_ADAPTERS = ('elicitation', 'compaction', 'compaction-wire',
               'interop', 'lifecycle-client', 'lifecycle-server')
SUITE_GROUPS = {
    'core': ('tools-unit', 'interop-unit', 'matrix', 'lifecycle', 'catalogue'),
    'extended': ('elicitation', 'observation', 'compaction', 'compaction-wire',
                 'auth', 'sender-isolation'),
}
SUITES = tuple(name for names in SUITE_GROUPS.values() for name in names)
SHARDABLE_SUITES = ('lifecycle', 'observation')
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


def suite_commands(root: Path, reports: Path, jobs: int, suite_group: str = 'all',
                   suite: str | None = None, shard_index: int = 0, shard_count: int = 1):
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
    validate_partition(suite, shard_index, shard_count)
    if suite is not None:
        suites = [row for row in suites if row[0] == suite]
        if suite in SHARDABLE_SUITES:
            suites = [(name, command + ['--shard-index', str(shard_index),
                       '--shard-count', str(shard_count), '--suite', suite], cwd)
                      for name, command, cwd in suites]
        return suites
    if suite_group == 'all':
        return suites
    return [row for row in suites if row[0] in SUITE_GROUPS[suite_group]]


def run_command(command, cwd: Path, log: Path, timeout: int, env=None) -> dict:
    started = time.monotonic()
    status = 'failed'
    code = None
    with log.open('w') as output:
        try:
            process = subprocess.Popen(command, cwd=cwd, stdout=output,
                                       stderr=subprocess.STDOUT, start_new_session=True,
                                       **({'env': env} if env is not None else {}))
            previous_handler = signal.getsignal(signal.SIGTERM)

            def terminate_group(signum, frame):
                # Suite commands start new sessions; an outer timeout cannot
                # reach them by signalling only the runner's process group.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                raise SystemExit(128 + signum)

            signal.signal(signal.SIGTERM, terminate_group)
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
            finally:
                signal.signal(signal.SIGTERM, previous_handler)
        except OSError as error:
            output.write(f'Unable to execute command: {type(error).__name__}\n')
    return {'status': status, 'exit_code': code,
            'seconds': round(time.monotonic() - started, 3), 'log': str(log)}


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def nonnegative_int(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError('must be nonnegative')
    return number


def validate_partition(suite, shard_index, shard_count):
    if suite is not None and suite not in SUITES:
        raise ValueError('unknown suite')
    if shard_count < 1 or not 0 <= shard_index < shard_count:
        raise ValueError('require shard-count > 0 and 0 <= shard-index < shard-count')
    if shard_count != 1 and suite not in SHARDABLE_SUITES:
        raise ValueError('multiple shards require --suite lifecycle or observation')


def reuse_adapters(directory: Path):
    """Fail closed rather than accidentally rebuilding absent shared artifacts."""
    results = []
    for name in (*GO_ADAPTERS, 'rust-interop'):
        path = directory / name
        valid = path.is_file() and os.access(path, os.X_OK)
        results.append({'name': name, 'status': 'passed' if valid else 'failed',
                        'path': str(path), 'reused': True})
    return results


def effective_jobs(requested):
    try:
        available = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        available = os.cpu_count() or 1
    return min(requested, max(1, available))


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
        ['cargo', 'build', '--locked', '--features', 'interop', '--bin', 'interop', '--target-dir', str(sdk / 'target')],
        sdk, reports / 'build-rust-interop.log', timeout)}
    results.append(result)
    print(f"build-{name}: {result['status']}", flush=True)
    return results


def record_sdk_revisions(path: Path, root: Path, reports: Path):
    """Verify checkout HEADs against an explicit snapshot and retain provenance."""
    from resolve_sdk_revisions import validate_manifest

    manifest = json.loads(path.read_text())
    validate_manifest(manifest)
    for language, sdk in manifest['sdks'].items():
        actual = subprocess.check_output(
            ['git', '-C', str(root.parent / sdk['path']), 'rev-parse', 'HEAD'],
            text=True,
        ).strip()
        if actual != sdk['revision']:
            raise ValueError(f"{language}: checkout {actual} does not match {sdk['revision']}")
    (reports / 'sdk-revisions.json').write_text(json.dumps(manifest, indent=4) + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports-dir', type=Path, required=True)
    parser.add_argument('--sdk-revisions', type=Path,
                        help='verify checkout HEADs and record this explicit snapshot')
    parser.add_argument('--suite-group', choices=('all', *SUITE_GROUPS), default='all',
                        help='suite partition to run (default: all)')
    parser.add_argument('--suite', choices=SUITES, help='run exactly one existing suite')
    parser.add_argument('--shard-index', type=nonnegative_int, default=0)
    parser.add_argument('--shard-count', type=positive_int, default=1)
    parser.add_argument('--prepare-only', action='store_true', help='build shared adapters, without running suites')
    parser.add_argument('--adapter-dir', type=Path, help='persistent adapter build destination or existing binaries to reuse')
    parser.add_argument('--jobs', type=positive_int, default=4)
    parser.add_argument('--timeout', type=positive_int, default=1800,
                        help='maximum seconds per suite command (default: 1800)')
    args = parser.parse_args(argv)
    try:
        validate_partition(args.suite, args.shard_index, args.shard_count)
    except ValueError as error:
        parser.error(str(error))
    if args.suite is not None and args.suite_group != 'all':
        parser.error('--suite and a non-default --suite-group are mutually exclusive')
    if args.prepare_only and args.adapter_dir is None:
        parser.error('--prepare-only requires --adapter-dir')
    jobs = effective_jobs(args.jobs)
    print(f'Integration jobs: {jobs} effective (requested maximum: {args.jobs})', flush=True)
    reports = args.reports_dir.resolve()
    reports.mkdir(parents=True, exist_ok=True)
    if args.sdk_revisions is not None:
        record_sdk_revisions(args.sdk_revisions, ROOT, reports)
    errors = validate_manifests(ROOT)
    # Remove stale reports even when preparation fails before any suite starts.
    for name, _, _ in suite_commands(ROOT, reports, jobs):
        (reports / f'{name}.json').unlink(missing_ok=True)
    results = []
    selected = suite_commands(ROOT, reports, jobs, args.suite_group, args.suite,
                              args.shard_index, args.shard_count)
    context = (nullcontext(str(args.adapter_dir.resolve())) if args.adapter_dir is not None
               else tempfile.TemporaryDirectory(prefix='ahp-go-adapters-'))
    with context as directory:
        if args.adapter_dir is not None and not args.prepare_only:
            prerequisites = reuse_adapters(Path(directory))
        else:
            Path(directory).mkdir(parents=True, exist_ok=True)
            prerequisites = prepare_adapters(ROOT, Path(directory), reports, args.timeout)
            if args.prepare_only and all(row['status'] == 'passed' for row in prerequisites):
                import shutil
                try:
                    shutil.copy2(ROOT.parent / 'rust-sdk/target/debug/interop', Path(directory) / 'rust-interop')
                    prerequisites = reuse_adapters(Path(directory))
                except OSError as error:
                    prerequisites.append({'name': 'copy-rust-interop', 'status': 'failed',
                                          'error': str(error)})
        env = {**os.environ, 'AHP_GO_ADAPTER_DIR': directory,
               'AHP_RUST_INTEROP': str(Path(directory) / 'rust-interop' if args.adapter_dir is not None
                                      else ROOT.parent / 'rust-sdk/target/debug/interop')}
        if not args.prepare_only and all(row['status'] == 'passed' for row in prerequisites):
            for name, command, cwd in selected:
                result = {'name': name, **run_command(command, cwd, reports / f'{name}.log', args.timeout, env=env)}
                if name in SHARDABLE_SUITES and args.suite is not None:
                    try:
                        report = json.loads((reports / f'{name}.json').read_text())
                        result['coverage'] = report['coverage']
                        result['scenario_sha256'] = report['scenario_sha256']
                        result['suite'] = report['suite']
                        result['shard_index'] = report['shard_index']
                        result['shard_count'] = report['shard_count']
                        if (report['suite'] != name or report['shard_index'] != args.shard_index
                                or report['shard_count'] != args.shard_count
                                or not report['coverage']['complete']):
                            raise ValueError('incomplete or mismatched shard coverage')
                    except (OSError, ValueError, KeyError, TypeError) as error:
                        result['status'] = 'failed'
                        result['coverage_error'] = str(error)
                results.append(result)
                print(f"{name}: {result['status']}", flush=True)
    failed = bool(errors) or any(row['status'] != 'passed' for row in prerequisites + results)
    summary = {'suite': args.suite, 'shard_index': args.shard_index,
               'shard_count': args.shard_count, 'prepare_only': args.prepare_only,
               'expected_suites': [] if args.prepare_only else [row[0] for row in selected],
               'suite_group': args.suite_group, 'requested_jobs': args.jobs, 'effective_jobs': jobs, 'status': 'failed' if failed else 'passed', 'manifest_errors': errors,
               'prerequisites': prerequisites, 'suites': results}
    (reports / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(f"SDK integration: {summary['status']}; {len(errors)} manifest errors; reports: {reports}")
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
