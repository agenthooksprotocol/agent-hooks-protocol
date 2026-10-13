#!/usr/bin/env python3
"""Verify downloaded CI artifacts (download-artifact merge-multiple: false).

Unit artifacts: sdk-report-{python,interop}-INDEX/unit-KIND-INDEX.json.
Integration artifacts: sdk-report-integration-SUITE-INDEX/**/summary.json.
Use --require-tools-unit for protocol CI only; its artifact directory must have
"tools-unit" in its name and contain one JSON report with status=passed.
Language-job success remains the workflow's needs/matrix responsibility.
"""
import argparse
import json
from pathlib import Path
import sys


SINGLE_SUITES = ('matrix', 'catalogue', 'elicitation', 'compaction',
                 'compaction-wire', 'auth', 'sender-isolation')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_strings(value, label, nonempty=False):
    require(isinstance(value, list) and all(isinstance(key, str) for key in value), f'{label}: expected string list')
    require(len(value) == len(set(value)), f'{label}: duplicate IDs')
    require(not nonempty or bool(value), f'{label}: empty inventory')
    return set(value)


def artifact_reports(root, prefix, count, filename):
    directories = [path for path in root.glob(prefix + '*')
                   if not prefix.startswith('sdk-report-integration-')
                   or path.name[len(prefix):].isdigit()]
    expected = {prefix + str(index) for index in range(count)}
    require({path.name for path in directories} == expected and all(path.is_dir() for path in directories), f'{prefix}: missing or unexpected shard artifacts')
    reports = []
    for index in range(count):
        directory = root / (prefix + str(index))
        name = filename.format(index=index)
        paths = list(directory.rglob(name))
        require(len(paths) == 1, f'{directory.name}: expected exactly one {name}, found {len(paths)}')
        report = json.loads(paths[0].read_text())
        require(isinstance(report, dict), f'{paths[0]}: expected JSON object')
        require(report.get('status') == 'passed', f'{directory.name}: status is not passed')
        require(type(report.get('shard_index')) is int and report['shard_index'] == index, f'{directory.name}: wrong shard index')
        require(type(report.get('shard_count')) is int and report['shard_count'] == count, f'{directory.name}: wrong shard count')
        reports.append(report)
    return reports


def verify_partition(reports, inventory_key, assigned_key, completed_key=None):
    inventory = unique_strings(reports[0].get(inventory_key), inventory_key, nonempty=True)
    union = set()
    for report in reports:
        current = unique_strings(report.get(inventory_key), inventory_key, nonempty=True)
        require(current == inventory, f'{inventory_key}: differing inventories')
        assigned = unique_strings(report.get(assigned_key), assigned_key)
        require(assigned <= inventory, f'{assigned_key}: unknown IDs')
        require(not union.intersection(assigned), f'{assigned_key}: overlapping shards')
        union.update(assigned)
        if completed_key:
            completed = unique_strings(report.get(completed_key), completed_key)
            require(completed == assigned, f'{completed_key}: incomplete or unexpected execution')
    require(union == inventory, f'{assigned_key}: incomplete union')


def verify_units(root, kind, count):
    reports = artifact_reports(root, f'sdk-report-{kind}-', count, f'unit-{kind}-{{index}}.json')
    require(all(report.get('kind') == 'unit' for report in reports), f'{kind}: wrong report kind')
    require(all(report.get('start_directory') == reports[0].get('start_directory') and report.get('pattern') == reports[0].get('pattern') for report in reports), f'{kind}: differing discovery configuration')
    verify_partition(reports, 'inventory', 'assigned_ids')
    for report in reports:
        records = report.get('results')
        require(isinstance(records, list) and all(isinstance(row, dict) for row in records), f'{kind}: missing results')
        completed = [row.get('id') for row in records]
        require(unique_strings(completed, 'results') == set(report['assigned_ids']), f'{kind}: incomplete results')
        require(all(row.get('status') in ('passed', 'skipped', 'expected_failure') for row in records), f'{kind}: unsuccessful test result')
        require(report.get('errors') == [] and report.get('failures') == [] and report.get('unexpected_successes') == [], f'{kind}: unsuccessful unittest outcome')


def verify_integration(root, suite, count):
    reports = artifact_reports(root, f'sdk-report-integration-{suite}-', count, 'summary.json')
    coverage = []
    hashes = []
    for report in reports:
        require(report.get('suite') == suite and report.get('prepare_only') is False, f'{suite}: wrong suite or preparation-only report')
        require(report.get('expected_suites') == [suite], f'{suite}: wrong expected suites')
        rows = report.get('suites')
        require(isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict), f'{suite}: missing or duplicate suite results')
        row = rows[0]
        require(row.get('name') == suite and row.get('status') == 'passed', f'{suite}: suite did not pass')
        prerequisites = report.get('prerequisites', [])
        require(isinstance(prerequisites, list) and all(isinstance(item, dict) and item.get('status') == 'passed' for item in prerequisites) and not report.get('manifest_errors'), f'{suite}: prerequisite errors')
        if suite in ('lifecycle', 'observation'):
            require(row.get('suite') == suite and type(row.get('shard_index')) is int and row['shard_index'] == report['shard_index'] and type(row.get('shard_count')) is int and row['shard_count'] == count, f'{suite}: mismatched nested shard metadata')
            value = row.get('coverage')
            require(isinstance(value, dict) and value.get('complete') is True, f'{suite}: incomplete coverage')
            coverage.append(value)
            hashes.append(row.get('scenario_sha256'))
    if coverage:
        require(all(isinstance(value, str) and value and value == hashes[0] for value in hashes), f'{suite}: differing or missing scenario hashes')
        verify_partition(coverage, 'expected_group_keys', 'assigned_group_keys', 'completed_group_keys')
        inventory = coverage[0].get('expected_inventory')
        require(isinstance(inventory, list) and inventory, f'{suite}: missing scenario inventory')
        require(all(value.get('expected_inventory') == inventory for value in coverage), f'{suite}: differing scenario inventories')
        require(all(isinstance(row, dict) for row in inventory), f'{suite}: malformed scenario inventory')
        groups = unique_strings([row.get('group') for row in inventory], 'scenario inventory groups')
        require(groups == set(coverage[0]['expected_group_keys']), f'{suite}: scenario inventory group mismatch')
        for row in inventory:
            selected = unique_strings(row.get('scenario_ids'), 'scenario_ids')
            excluded = unique_strings(row.get('inapplicable_scenario_ids'), 'inapplicable_scenario_ids')
            require(not selected.intersection(excluded), f'{suite}: scenario is both selected and excluded')


def verify(root, python_shards=6, interop_shards=2, lifecycle_shards=4,
           observation_shards=3, require_tools_unit=False):
    root = Path(root)
    require(root.is_dir(), 'reports directory does not exist')
    counts = (python_shards, interop_shards, lifecycle_shards, observation_shards)
    require(all(type(count) is int and count > 0 for count in counts), 'shard counts must be positive integers')
    verify_units(root, 'python', python_shards)
    verify_units(root, 'interop', interop_shards)
    suites = {'lifecycle': lifecycle_shards, 'observation': observation_shards,
              **dict.fromkeys(SINGLE_SUITES, 1)}
    expected_dirs = {f'sdk-report-integration-{suite}-{index}' for suite, count in suites.items() for index in range(count)}
    require({path.name for path in root.glob('sdk-report-integration-*')} == expected_dirs, 'missing or unexpected integration artifacts')
    for suite, count in suites.items():
        verify_integration(root, suite, count)
    if require_tools_unit:
        directories = [path for path in root.iterdir() if path.is_dir() and 'tools-unit' in path.name]
        paths = [path for directory in directories for path in directory.rglob('*.json')]
        require(len(paths) == 1, 'tools-unit: expected exactly one JSON report')
        report = json.loads(paths[0].read_text())
        require(isinstance(report, dict) and report.get('status') == 'passed', 'tools-unit: status is not passed')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports-dir', type=Path, required=True)
    for kind, count in (('python', 6), ('interop', 2), ('lifecycle', 4), ('observation', 3)):
        parser.add_argument(f'--{kind}-shards', type=int, default=count)
    parser.add_argument('--require-tools-unit', action='store_true')
    args = parser.parse_args(argv)
    try:
        verify(args.reports_dir, args.python_shards, args.interop_shards,
               args.lifecycle_shards, args.observation_shards, args.require_tools_unit)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f'CI report verification failed: {exc}', file=sys.stderr)
        return 1
    print('CI reports passed: complete disjoint unit and integration coverage')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
