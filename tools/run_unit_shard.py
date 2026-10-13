#!/usr/bin/env python3
"""Discover once and shard individual unittest cases without re-importing by ID.

Run from the same working directory/environment as unittest discover. Discovery
uses unittest's normal import rules; --top-level-directory is its optional -t.
Reports include the full inventory and durations (seconds), which can be passed
back with --duration-file. Missing historical timings default to one second.
Assignment is sorted-ID round robin, or deterministic longest-first greedy
balancing with timings. Execution retains discovery order for shared fixtures.
Class/module fixtures may run in several shards; subtests remain indivisible.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import time
import traceback
import unittest


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def allocate(ids, shard_count, durations=None):
    """Return disjoint assignments covering every ID, independent of input order."""
    shards = [[] for _ in range(shard_count)]
    if durations is None:
        for index, test_id in enumerate(sorted(ids)):
            shards[index % shard_count].append(test_id)
    else:
        loads = [0.0] * shard_count
        ordered = sorted(ids, key=lambda key: (-durations.get(key, 1.0), key))
        for test_id in ordered:
            index = min(range(shard_count), key=lambda i: (loads[i], len(shards[i]), i))
            shards[index].append(test_id)
            loads[index] += durations.get(test_id, 1.0)
    return [sorted(shard) for shard in shards]


def read_durations(path):
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict) and 'durations' in data:
        data = data['durations']
    if not isinstance(data, dict):
        raise ValueError('duration file must be an ID-to-seconds object or a shard report')
    for key, value in data.items():
        if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError('durations must have string IDs and finite nonnegative seconds')
    return data


class TimedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = {}
        self.started = {}

    def startTest(self, test):
        super().startTest(test)
        self.started[test.id()] = time.perf_counter()
        self.records[test.id()] = {'id': test.id(), 'status': 'passed'}

    def stopTest(self, test):
        self.records[test.id()]['elapsed_seconds'] = time.perf_counter() - self.started[test.id()]
        super().stopTest(test)

    def outcome(self, test, status, reason=None):
        if test.id() in self.records:
            self.records[test.id()]['status'] = status
            if reason is not None:
                self.records[test.id()]['reason'] = reason

    def addError(self, test, err):
        super().addError(test, err)
        self.outcome(test, 'error')

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.outcome(test, 'failed')

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.outcome(test, 'skipped', reason)

    def propagate_fixture_skips(self, selected_tests):
        # unittest reports a skipped setup fixture through an _ErrorHolder,
        # without startTest/stopTest for its affected cases. Match the exact
        # fixture identity, not an ID prefix, and never mask other unrun cases.
        reasons = {test.id(): reason for test, reason in self.skipped}
        for test in selected_tests:
            if test.id() in self.records:
                continue
            cls = type(test)
            holders = (f'setUpModule ({cls.__module__})',
                       f'setUpClass ({cls.__module__}.{cls.__qualname__})')
            for holder in holders:
                if holder in reasons:
                    self.records[test.id()] = {
                        'id': test.id(), 'status': 'skipped', 'reason': reasons[holder]}
                    break

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self.outcome(test, 'expected_failure')

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.outcome(test, 'unexpected_success')

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            status = 'failed' if issubclass(err[0], test.failureException) else 'error'
            self.outcome(test, status)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-directory', required=True)
    parser.add_argument('--pattern', default='test*.py')
    parser.add_argument('--top-level-directory')
    parser.add_argument('--shard-index', type=int, required=True)
    parser.add_argument('--shard-count', type=int, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--duration-file', type=Path)
    args = parser.parse_args(argv)
    report = {
        'schema_version': 1,
        'kind': 'unit',
        'start_directory': str(Path(args.start_directory).resolve()),
        'pattern': args.pattern,
        'status': 'error',
        'shard_index': args.shard_index,
        'shard_count': args.shard_count,
        'inventory': [],
        'assigned_ids': [],
        'results': [],
        'durations': {},
        'errors': [],
    }
    started = time.perf_counter()
    code = 2
    try:
        if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
            raise ValueError('require shard-count > 0 and 0 <= shard-index < shard-count')
        if not args.pattern:
            raise ValueError('pattern must not be empty')
        durations = read_durations(args.duration_file) if args.duration_file else None
        report['allocation'] = 'greedy_duration' if durations is not None else 'round_robin'
        loader = unittest.TestLoader()
        suite = loader.discover(args.start_directory, pattern=args.pattern,
                                top_level_dir=args.top_level_directory)
        tests = list(flatten(suite))
        # unittest converts failed imports/load_tests into synthetic tests. Never
        # let a shard hide these by assigning the failed test to another shard.
        if loader.errors:
            report['status'] = 'discovery_error'
            report['errors'] = list(loader.errors)
        else:
            ids = [test.id() for test in tests]
            if not ids:
                raise ValueError('discovery found no tests')
            if len(set(ids)) != len(ids):
                raise ValueError('discovery returned duplicate test IDs')
            report['inventory'] = sorted(ids)
            assigned = allocate(ids, args.shard_count, durations)[args.shard_index]
            report['assigned_ids'] = assigned
            selected = set(assigned)
            selected_tests = [test for test in tests if test.id() in selected]
            run_suite = unittest.TestSuite(selected_tests)
            result = unittest.TextTestRunner(verbosity=1, resultclass=TimedResult).run(run_suite)
            result.propagate_fixture_skips(selected_tests)
            report['results'] = [result.records.get(key, {'id': key, 'status': 'not_run'}) for key in assigned]
            report['durations'] = {key: record['elapsed_seconds'] for key, record in result.records.items()
                                   if 'elapsed_seconds' in record}
            report['tests_run'] = result.testsRun
            report['skipped'] = [{'id': test.id(), 'reason': reason} for test, reason in result.skipped]
            report['failures'] = [{'id': test.id(), 'traceback': trace} for test, trace in result.failures]
            report['errors'] = [{'id': test.id(), 'traceback': trace} for test, trace in result.errors]
            report['unexpected_successes'] = [test.id() for test in result.unexpectedSuccesses]
            successful = result.wasSuccessful() and all(row['status'] != 'not_run' for row in report['results'])
            report['status'] = 'passed' if successful else 'failed'
            code = 0 if successful else 1
    except Exception as exc:
        report['errors'].append(f'{type(exc).__name__}: {exc}')
        report['exception_traceback'] = traceback.format_exc()
        print(report['errors'][-1], file=sys.stderr)
    finally:
        report['elapsed_seconds'] = time.perf_counter() - started
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
