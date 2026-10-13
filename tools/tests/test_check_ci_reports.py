import copy
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_ci_reports as checker


class CheckCIReportsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.populate()

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def mutate(self, relative, change):
        path = self.root / relative
        value = json.loads(path.read_text())
        change(value)
        path.write_text(json.dumps(value))

    def unit_path(self, index=0):
        return f'sdk-report-python-{index}/unit-python-{index}.json'

    def integration_path(self, suite='lifecycle', index=0):
        return f'sdk-report-integration-{suite}-{index}/nested/summary.json'

    def populate(self):
        for kind, count in (('python', 6), ('interop', 2)):
            ids = [f'{kind}.test_{index}' for index in range(count + 1)]
            for index in range(count):
                assigned = ids[index::count]
                self.write(f'sdk-report-{kind}-{index}/unit-{kind}-{index}.json', {
                    'kind': 'unit', 'status': 'passed', 'start_directory': f'/sdk/{kind}/tests', 'pattern': 'test*.py',
                    'shard_index': index, 'shard_count': count, 'inventory': ids, 'assigned_ids': assigned,
                    'results': [{'id': key, 'status': 'passed'} for key in assigned],
                    'errors': [], 'failures': [], 'unexpected_successes': []})
        for suite, count in {'lifecycle': 4, 'observation': 3, **dict.fromkeys(checker.SINGLE_SUITES, 1)}.items():
            groups = [f'group-{i}' for i in range(count + 1)]
            inventory = [{'group': key, 'scenario_ids': ['a', 'b'], 'inapplicable_scenario_ids': ['c']} for key in groups]
            for index in range(count):
                row = {'name': suite, 'status': 'passed'}
                if count > 1:
                    row.update(suite=suite, shard_index=index, shard_count=count, scenario_sha256='abc123', coverage={
                        'expected_group_keys': groups, 'assigned_group_keys': groups[index::count],
                        'completed_group_keys': groups[index::count], 'expected_inventory': inventory, 'complete': True})
                self.write(self.integration_path(suite, index), {
                    'suite': suite, 'shard_index': index, 'shard_count': count, 'prepare_only': False,
                    'status': 'passed', 'expected_suites': [suite], 'suites': [row],
                    'prerequisites': [{'status': 'passed'}], 'manifest_errors': []})

    def invoke(self, *extra):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return checker.main(['--reports-dir', str(self.root), *extra])

    def test_complete_default_layout_passes(self):
        self.assertEqual(self.invoke(), 0)

    def test_tools_unit_is_protocol_only(self):
        self.assertEqual(self.invoke('--require-tools-unit'), 1)
        self.write('sdk-report-tools-unit/unit-tools.json', {'status': 'passed'})
        self.assertEqual(self.invoke('--require-tools-unit'), 0)
        self.mutate('sdk-report-tools-unit/unit-tools.json', lambda r: r.update(status='failed'))
        self.assertEqual(self.invoke('--require-tools-unit'), 1)
        self.assertEqual(self.invoke(), 0)

    def test_missing_duplicate_wrong_count_and_failed_reports(self):
        path = self.root / self.unit_path()
        original = path.read_text()
        for patch in ({'status': 'failed'}, {'shard_index': 1}, {'shard_count': 7}, {'shard_index': False}):
            with self.subTest(patch=patch):
                value = json.loads(original)
                value.update(patch)
                path.write_text(json.dumps(value))
                self.assertEqual(self.invoke(), 1)
        path.write_text(original)
        duplicate = path.parent / 'nested' / path.name
        duplicate.parent.mkdir()
        duplicate.write_text(original)
        self.assertEqual(self.invoke(), 1)
        duplicate.unlink()
        path.unlink()
        self.assertEqual(self.invoke(), 1)

    def test_unknown_artifact_index_rejected(self):
        (self.root / 'sdk-report-python-6').mkdir()
        self.assertEqual(self.invoke(), 1)

    def test_units_reject_inventory_overlap_missing_union_and_bad_results(self):
        path = self.root / self.unit_path()
        original = json.loads(path.read_text())
        patches = [
            lambda r: r['inventory'].append('extra'),
            lambda r: r['assigned_ids'].append('python.test_1'),
            lambda r: r['assigned_ids'].pop(),
            lambda r: r['assigned_ids'].append(r['assigned_ids'][0]),
            lambda r: r['results'].pop(),
            lambda r: r['results'][0].update(status='not_run'),
            lambda r: r['errors'].append('error'),
        ]
        for patch in patches:
            with self.subTest(patch=patch):
                value = copy.deepcopy(original)
                patch(value)
                path.write_text(json.dumps(value))
                self.assertEqual(self.invoke(), 1)
        path.write_text(json.dumps(original))
        self.assertEqual(self.invoke(), 0)

    def test_integration_rejects_incomplete_or_differing_coverage(self):
        path = self.root / self.integration_path()
        original = json.loads(path.read_text())
        patches = [
            lambda r: r.update(prepare_only=True),
            lambda r: r.update(expected_suites=[]),
            lambda r: r['suites'].append(copy.deepcopy(r['suites'][0])),
            lambda r: r['suites'][0].update(status='failed'),
            lambda r: r['suites'][0].update(shard_count=9),
            lambda r: r['suites'][0].update(scenario_sha256='different'),
            lambda r: r['suites'][0]['coverage'].update(complete=False),
            lambda r: r['suites'][0]['coverage']['completed_group_keys'].pop(),
            lambda r: r['suites'][0]['coverage']['assigned_group_keys'].append('group-1'),
            lambda r: r['suites'][0]['coverage']['expected_inventory'][0]['scenario_ids'].append('different'),
            lambda r: r['suites'][0]['coverage']['expected_group_keys'].append('different'),
        ]
        for patch in patches:
            with self.subTest(patch=patch):
                value = copy.deepcopy(original)
                patch(value)
                path.write_text(json.dumps(value))
                self.assertEqual(self.invoke(), 1)
        path.write_text(json.dumps(original))
        self.assertEqual(self.invoke(), 0)

    def test_single_integration_suite_is_required(self):
        (self.root / self.integration_path('auth')).unlink()
        self.assertEqual(self.invoke(), 1)

    def test_invalid_counts_and_malformed_json_fail(self):
        self.assertEqual(self.invoke('--python-shards', '0'), 1)
        self.assertEqual(self.invoke('--lifecycle-shards', '5'), 1)
        (self.root / self.integration_path()).write_text('{broken')
        self.assertEqual(self.invoke(), 1)

    def test_small_count_overrides(self):
        root = self.root / 'small'
        for kind in ('python', 'interop'):
            self.write(f'small/sdk-report-{kind}-0/unit-{kind}-0.json', {
                'kind': 'unit', 'status': 'passed', 'shard_index': 0, 'shard_count': 1,
                'inventory': ['a'], 'assigned_ids': ['a'], 'results': [{'id': 'a', 'status': 'skipped'}],
                'errors': [], 'failures': [], 'unexpected_successes': []})
        for suite in ('lifecycle', 'observation', *checker.SINGLE_SUITES):
            report = json.loads((self.root / self.integration_path(suite)).read_text())
            report['shard_count'] = 1
            if suite in ('lifecycle', 'observation'):
                row = report['suites'][0]
                row['shard_count'] = 1
                value = row['coverage']
                value['assigned_group_keys'] = value['expected_group_keys']
                value['completed_group_keys'] = value['expected_group_keys']
            self.write(f'small/sdk-report-integration-{suite}-0/summary.json', report)
        checker.verify(root, 1, 1, 1, 1)


if __name__ == '__main__':
    unittest.main()
