"""Pair sharding preserves the complete matrix transport/auth/scenario grid."""
from collections import Counter
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import matrix


class MatrixPartitionTests(unittest.TestCase):
    def setUp(self):
        self.adapters = [{'language': name} for name in ('go', 'python', 'rust', 'typescript')]
        self.scenarios = matrix.load(matrix.HERE/'scenarios.json')['scenarios']

    def rows(self, pairs):
        return [{'client': client['language'], 'server': server['language'],
                 'transport': transport, 'auth': mode, 'status': status, 'errors': [],
                 'results': [{'id': scenario['id'], 'status': status} for scenario in self.scenarios]}
                for client, server in pairs for transport in ('http', 'stdio') for mode in matrix.MODES
                for status in ['inapplicable' if transport == 'stdio' and mode != 'none' else 'passed']]

    def test_two_shards_cover_original_sixteen_pairs_once(self):
        original = [(client, server) for client in self.adapters for server in self.adapters]
        keys = []
        for index in range(2):
            expected, assigned = matrix.partition_pairs(self.adapters, shard_index=index, shard_count=2)
            self.assertEqual(expected, original)
            self.assertEqual(assigned, original[index::2])
            self.assertEqual(len(assigned), 8)
            keys.extend(map(matrix.pair_key, assigned))
            rows = self.rows(assigned)
            self.assertEqual(len(rows), 80)
            self.assertEqual(sum(row['status'] == 'inapplicable' for row in rows), 32)
            coverage = matrix.coverage_inventory(expected, assigned, self.scenarios, rows)
            self.assertTrue(coverage['complete'])
            self.assertEqual(coverage['assigned_group_keys'], list(map(matrix.pair_key, assigned)))
            self.assertEqual(len(coverage['expected_inventory']), 16)
            for item in coverage['expected_inventory']:
                self.assertEqual(item['scenario_ids'], [scenario['id'] for scenario in self.scenarios])
                self.assertEqual(len(item['transport_auth_inventory']), 10)
                for combination in item['transport_auth_inventory']:
                    ids = combination['scenario_ids'] + combination['inapplicable_scenario_ids']
                    self.assertEqual(ids, item['scenario_ids'])
        self.assertEqual(Counter(keys), Counter(map(matrix.pair_key, original)))

    def test_completion_rejects_missing_duplicate_failed_or_misclassified_evidence(self):
        expected, assigned = matrix.partition_pairs(self.adapters, shard_count=2)
        rows = self.rows(assigned)
        invalid = [rows[:-1], rows + [rows[0]], rows + self.rows(expected[1:2])]
        for field, value in [('status', 'failed'), ('errors', ['error']), ('status', 'inapplicable')]:
            modified = deepcopy(rows); modified[0][field] = value; invalid.append(modified)
        for alteration in ('missing', 'duplicate', 'failed', 'false-inapplicable'):
            modified = deepcopy(rows)
            if alteration == 'missing':
                modified[0]['results'].pop()
            elif alteration == 'duplicate':
                modified[0]['results'].append(modified[0]['results'][0])
            elif alteration == 'failed':
                modified[0]['results'][0]['status'] = 'failed'
            else:
                modified[6]['status'] = 'passed'
            invalid.append(modified)
        for rows in invalid:
            self.assertFalse(matrix.coverage_inventory(expected, assigned, self.scenarios, rows)['complete'])

    def test_filters_and_invalid_indices(self):
        expected, assigned = matrix.partition_pairs(self.adapters, clients=['python'],
            servers=['rust', 'typescript'], shard_index=1, shard_count=2)
        self.assertEqual(list(map(matrix.pair_key, expected)), ['python->rust', 'python->typescript'])
        self.assertEqual(list(map(matrix.pair_key, assigned)), ['python->typescript'])
        for index, count in [(-1, 2), (2, 2), (0, 0)]:
            with self.assertRaises(ValueError):
                matrix.partition_pairs(self.adapters, shard_index=index, shard_count=count)
            with patch.object(matrix, 'load_adapter') as discover, patch('sys.stderr', new=io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    matrix.main(['--shard-index', str(index), '--shard-count', str(count)])
                self.assertEqual(error.exception.code, 2)
                discover.assert_not_called()

    def test_cli_loads_all_adapters_and_emits_pair_inventory(self):
        paths = [Path('/workspace')/f"{adapter['language']}-sdk/interop/adapter.json" for adapter in self.adapters]
        adapters = dict(zip(paths, self.adapters))
        executed = []
        def run(client, server, scenarios, scenario_file, issuer, timeout):
            self.assertEqual(scenarios, self.scenarios)
            executed.append(matrix.pair_key((client, server)))
            return self.rows([(client, server)])
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)/'matrix.json'
            with patch.object(Path, 'glob', return_value=iter(paths)), patch.object(matrix, 'load_adapter', side_effect=adapters.__getitem__) as discover, patch.object(matrix, 'Issuer'), patch.object(matrix, 'run_group', side_effect=run), redirect_stdout(io.StringIO()):
                code = matrix.main(['--shard-index', '1', '--shard-count', '2', '--output', str(output)])
            report = json.loads(output.read_text())
            self.assertEqual(code, 0)
            self.assertEqual(discover.call_count, 4)
            self.assertEqual(report['suite'], 'matrix')
            self.assertEqual((report['shard_index'], report['shard_count']), (1, 2))
            self.assertEqual(len(report['coverage']['expected_group_keys']), 16)
            self.assertEqual(len(report['coverage']['assigned_group_keys']), 8)
            self.assertEqual(Counter(executed), Counter(report['coverage']['assigned_group_keys']))
            self.assertTrue(report['coverage']['complete'])
            self.assertEqual(len(report['scenario_sha256']), 64)
            self.assertEqual(report['summary']['groups'], {'passed': 48, 'inapplicable': 32})


if __name__ == '__main__':
    unittest.main()
