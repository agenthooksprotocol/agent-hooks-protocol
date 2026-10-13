"""Exercise the public CLI in fresh processes, as CI invokes each shard."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_unit_shard as runner


class UnitShardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tests = self.root / 'tests'
        self.tests.mkdir()

    def source(self, name, content):
        (self.tests / name).write_text(content)

    def invoke(self, index=0, count=1, extra=()):
        report = self.root / 'reports' / f'{index}.json'
        result = subprocess.run([
            sys.executable, str(Path(runner.__file__).resolve()),
            '--start-directory', str(self.tests), '--pattern', 'test*.py',
            '--shard-index', str(index), '--shard-count', str(count),
            '--report', str(report), *extra,
        ], cwd=self.root, capture_output=True, text=True, timeout=20)
        return result.returncode, json.loads(report.read_text())

    def make_inventory(self, count=11):
        self.source('test_many.py', 'import unittest\nclass Many(unittest.TestCase):\n' + ''.join(
            f'    def test_{i:02d}(self): pass\n' for i in range(count)))

    def test_union_disjoint_counts_and_individual_execution(self):
        self.make_inventory()
        reports = []
        for index in range(4):
            code, report = self.invoke(index, 4)
            self.assertEqual(code, 0, report)
            self.assertEqual(report['status'], 'passed')
            self.assertEqual(report['tests_run'], len(report['assigned_ids']))
            self.assertEqual(set(report['durations']), set(report['assigned_ids']))
            self.assertTrue(all(row['status'] == 'passed' and row['elapsed_seconds'] >= 0 for row in report['results']))
            reports.append(report)
        inventory = reports[0]['inventory']
        self.assertEqual(len(inventory), 11)
        assigned = [key for report in reports for key in report['assigned_ids']]
        self.assertEqual(sorted(assigned), inventory)
        self.assertEqual(len(set(assigned)), len(assigned))
        self.assertLessEqual(max(len(r['assigned_ids']) for r in reports) - min(len(r['assigned_ids']) for r in reports), 1)
        self.assertTrue(all(r['inventory'] == inventory for r in reports))
        self.assertEqual(self.invoke(0, 4)[1]['assigned_ids'], reports[0]['assigned_ids'])

    def test_balancing_is_deterministic_and_complete(self):
        ids = ['a', 'b', 'c', 'd', 'e']
        durations = dict(zip(ids, [8, 7, 3, 2, 0]))
        shards = runner.allocate(ids, 2, durations)
        self.assertEqual(shards, runner.allocate(list(reversed(ids)), 2, durations))
        self.assertEqual(sorted(key for shard in shards for key in shard), ids)
        self.assertEqual([sum(durations[key] for key in shard) for shard in shards], [10, 10])
        self.assertEqual(runner.allocate(ids, 2), runner.allocate(list(reversed(ids)), 2))

    def test_duration_report_can_be_reused(self):
        self.make_inventory(3)
        code, report = self.invoke()
        self.assertEqual(code, 0)
        timings = self.root / 'timings.json'
        timings.write_text(json.dumps(report))
        code, report = self.invoke(0, 2, ['--duration-file', str(timings)])
        self.assertEqual(code, 0, report)
        self.assertEqual(report['allocation'], 'greedy_duration')

    def test_invalid_durations_fail_before_execution(self):
        self.make_inventory()
        path = self.root / 'durations.json'
        for data in ([], {'x': -1}, {'x': True}, {'x': '1'}, {'x': float('nan')}):
            with self.subTest(data=data):
                path.write_text(json.dumps(data))
                code, report = self.invoke(extra=['--duration-file', str(path)])
                self.assertNotEqual(code, 0)
                self.assertEqual(report['assigned_ids'], [])
                self.assertTrue(report['errors'])

    def test_duplicate_discovery_ids_fail_closed(self):
        self.source('test_duplicate.py', '''import unittest
class Case(unittest.TestCase):
    def test_one(self): pass
def load_tests(loader, tests, pattern):
    return unittest.TestSuite([Case('test_one'), Case('test_one')])
''')
        code, report = self.invoke()
        self.assertNotEqual(code, 0)
        self.assertIn('duplicate test IDs', report['errors'][0])
        self.assertEqual(report['results'], [])

    def test_discovered_objects_run_once_and_keep_shared_fixtures(self):
        self.source('test_once.py', '''import unittest
from pathlib import Path
def setUpModule():
    Path('module_setup').write_text('once')
class Case(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path('class_setup')
        assert not path.exists(), 'class fixture repeated'
        path.write_text('once')
    def test_a(self):
        path = Path('a')
        self.assertFalse(path.exists())
        path.write_text('once')
    def test_b(self):
        path = Path('b')
        self.assertFalse(path.exists())
        path.write_text('once')
''')
        code, report = self.invoke()
        self.assertEqual(code, 0, report)
        self.assertEqual(report['tests_run'], 2)
        self.assertTrue(all((self.root / name).exists() for name in ('a', 'b', 'module_setup', 'class_setup')))

    def test_invalid_shard_parameters_fail_closed(self):
        self.make_inventory()
        for index, count in ((-1, 2), (2, 2), (0, 0), (0, -1)):
            with self.subTest(index=index, count=count):
                code, report = self.invoke(index, count)
                self.assertNotEqual(code, 0)
                self.assertEqual(report['results'], [])
                self.assertTrue(report['errors'])

    def test_empty_discovery_is_not_success(self):
        code, report = self.invoke()
        self.assertNotEqual(code, 0)
        self.assertIn('no tests', report['errors'][0])

    def test_import_errors_reject_every_shard_without_running_good_tests(self):
        self.make_inventory(3)
        self.source('test_broken.py', 'raise ImportError("intentional broken import")\n')
        for index in range(3):
            code, report = self.invoke(index, 3)
            self.assertNotEqual(code, 0)
            self.assertEqual(report['status'], 'discovery_error')
            self.assertEqual(report['results'], [])
            self.assertIn('intentional broken import', '\n'.join(report['errors']))

    def test_load_tests_errors_are_discovery_errors(self):
        self.source('test_custom.py', 'def load_tests(loader, tests, pattern):\n    raise RuntimeError("broken load_tests")\n')
        code, report = self.invoke()
        self.assertNotEqual(code, 0)
        self.assertEqual(report['status'], 'discovery_error')
        self.assertIn('broken load_tests', '\n'.join(report['errors']))

    def test_reports_failure_error_skip_and_subtest_without_rerunning(self):
        self.source('test_outcomes.py', '''import unittest
class Outcomes(unittest.TestCase):
    def test_failure(self): self.fail("expected assertion")
    def test_error(self): raise RuntimeError("expected error")
    @unittest.skip("skip reason")
    def test_skip(self): pass
    def test_subtests(self):
        for i in range(3):
            with self.subTest(i=i): self.assertEqual(i, 0)
    @unittest.expectedFailure
    def test_expected(self): self.fail("expected")
    @unittest.expectedFailure
    def test_unexpected(self): pass
''')
        code, report = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['tests_run'], 6)
        statuses = {row['id'].rsplit('.', 1)[-1]: row['status'] for row in report['results']}
        self.assertEqual(statuses, {'test_failure': 'failed', 'test_error': 'error', 'test_skip': 'skipped', 'test_subtests': 'failed', 'test_expected': 'expected_failure', 'test_unexpected': 'unexpected_success'})
        self.assertEqual(report['skipped'][0]['reason'], 'skip reason')
        self.assertEqual(len(report['failures']), 3)
        self.assertEqual(len(report['errors']), 1)
        self.assertEqual(len(report['durations']), 6)

    def test_class_fixture_skip_marks_only_affected_cases_skipped(self):
        self.source('test_fixture.py', '''import unittest
class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls): raise unittest.SkipTest("class unavailable")
    def test_one(self): self.fail("must not run")
    def test_two(self): self.fail("must not run")
class Other(unittest.TestCase):
    def test_pass(self): pass
''')
        code, report = self.invoke()
        self.assertEqual(code, 0, report)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['tests_run'], 1)
        for row in report['results']:
            if '.Fixture.' in row['id']:
                self.assertEqual(row['status'], 'skipped')
                self.assertEqual(row['reason'], 'class unavailable')
                self.assertNotIn(row['id'], report['durations'])
            else:
                self.assertEqual(row['status'], 'passed')

    def test_module_fixture_skip_marks_only_selected_module_cases_skipped(self):
        self.source('test_fixture.py', '''import unittest
def setUpModule(): raise unittest.SkipTest("module unavailable")
class Fixture(unittest.TestCase):
    def test_one(self): self.fail("must not run")
    def test_two(self): self.fail("must not run")
''')
        self.source('test_other.py', 'import unittest\nclass Other(unittest.TestCase):\n    def test_pass(self): pass\n')
        for index in range(2):
            code, report = self.invoke(index, 2)
            self.assertEqual(code, 0, report)
            self.assertEqual(set(row['id'] for row in report['results']), set(report['assigned_ids']))
            for row in report['results']:
                if row['id'].startswith('test_fixture.'):
                    self.assertEqual(row['status'], 'skipped')
                    self.assertEqual(row['reason'], 'module unavailable')
                    self.assertNotIn(row['id'], report['durations'])
                else:
                    self.assertEqual(row['status'], 'passed')

    def test_module_fixture_errors_stay_failed_with_unrun_cases(self):
        self.source('test_fixture.py', '''import unittest
def setUpModule(): raise RuntimeError("broken module fixture")
class Fixture(unittest.TestCase):
    def test_one(self): pass
    def test_two(self): pass
''')
        code, report = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['tests_run'], 0)
        self.assertTrue(all(row['status'] == 'not_run' for row in report['results']))
        self.assertEqual(report['durations'], {})
        self.assertIn('broken module fixture', report['errors'][0]['traceback'])

    def test_genuine_unrun_cases_fail_even_without_unittest_errors(self):
        self.source('test_unrun.py', '''import unittest
class NeverRuns(unittest.TestCase):
    def test_one(self): pass
    def run(self, result=None): return result
''')
        code, report = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['results'][0]['status'], 'not_run')
        self.assertEqual(report['errors'], [])

    def test_fixture_errors_fail_and_report_unrun_assigned_tests(self):
        self.source('test_fixture.py', '''import unittest
class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls): raise RuntimeError("broken class fixture")
    def test_one(self): pass
    def test_two(self): pass
''')
        code, report = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(report['tests_run'], 0)
        self.assertTrue(all(row['status'] == 'not_run' for row in report['results']))
        self.assertIn('broken class fixture', report['errors'][0]['traceback'])

    def test_package_relative_imports_use_unittest_top_level_option(self):
        (self.tests / '__init__.py').write_text('')
        (self.tests / 'helper.py').write_text('VALUE = 7\n')
        self.source('test_relative.py', 'import unittest\nfrom .helper import VALUE\nclass Relative(unittest.TestCase):\n    def test_value(self): self.assertEqual(VALUE, 7)\n')
        code, report = self.invoke(extra=['--top-level-directory', str(self.root)])
        self.assertEqual(code, 0, report)
        self.assertEqual(report['inventory'], ['tests.test_relative.Relative.test_value'])

    def test_empty_assignment_is_valid_if_complete_inventory_is_nonempty(self):
        self.make_inventory(1)
        code, report = self.invoke(1, 2)
        self.assertEqual(code, 0, report)
        self.assertEqual(len(report['inventory']), 1)
        self.assertEqual(report['assigned_ids'], [])
        self.assertEqual(report['tests_run'], 0)


if __name__ == '__main__':
    unittest.main()
