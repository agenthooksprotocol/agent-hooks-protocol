"""Coverage-preserving lifecycle and observation group partitions."""
from collections import Counter
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import lifecycle_matrix as lifecycle


class LifecyclePartitionTests(unittest.TestCase):
    def test_round_robin_is_disjoint_exhaustive_and_stable(self):
        original = [(c, s, t, auth) for c in lifecycle.LANGUAGES
                    for s in lifecycle.LANGUAGES for t in ('http', 'stdio')
                    for auth in lifecycle.MODES if t == 'http' or auth == 'none']
        self.assertEqual(len(original), 96)
        for count in (1, 3, 4, 7, 100):
            partitions = []
            for index in range(count):
                expected, assigned = lifecycle.partition_groups(shard_index=index, shard_count=count)
                self.assertEqual(expected, original)
                self.assertEqual(assigned, original[index::count])
                partitions.extend(assigned)
            self.assertEqual(Counter(partitions), Counter(original))

    def test_filters_apply_before_partition_without_invalid_stdio_auth(self):
        expected, assigned = lifecycle.partition_groups(client='python', server='rust',
            shard_index=1, shard_count=2)
        self.assertEqual(len(expected), 6)
        self.assertEqual(assigned, expected[1::2])
        self.assertEqual(lifecycle.partition_groups(transport='stdio', auth='bearer'), ([], []))
        for index, count in [(-1, 4), (4, 4), (0, 0)]:
            with self.assertRaises(ValueError):
                lifecycle.partition_groups(shard_index=index, shard_count=count)

    def results(self, groups, scenarios):
        rows = []
        for group in groups:
            selected, excluded = lifecycle.transport_scenarios(scenarios, group[2])
            rows.append({'group': lifecycle.group_key(group), 'passed': True,
                         'scenarios': len(selected), 'inapplicable': excluded, 'errors': []})
        return rows

    def test_inventory_preserves_every_scenario_and_transport_exclusion(self):
        for file, count in [('lifecycle-scenarios.json', 4), ('observation-chain-scenarios.json', 3)]:
            scenarios = lifecycle.load(lifecycle.HERE/file)['scenarios']
            for index in range(count):
                expected, groups = lifecycle.partition_groups(shard_index=index, shard_count=count)
                rows = self.results(groups, scenarios)
                coverage = lifecycle.coverage_inventory(expected, groups, scenarios, rows)
                self.assertTrue(coverage['complete'])
                self.assertEqual(len(coverage['expected_inventory']), 96)
                for entry in coverage['expected_inventory']:
                    selected, excluded = lifecycle.transport_scenarios(scenarios, entry['group'].split('/')[1])
                    self.assertEqual(entry['scenario_ids'], [s['id'] for s in selected])
                    self.assertEqual(entry['inapplicable_scenario_ids'], [s['id'] for s in excluded])
                    self.assertEqual(Counter(entry['scenario_ids']+entry['inapplicable_scenario_ids']),
                                     Counter(s['id'] for s in scenarios))
                for bad in (rows[:-1], rows + [rows[0]],
                            [{**rows[0], 'passed': False}] + rows[1:],
                            [{**rows[0], 'scenarios': 0}] + rows[1:]):
                    self.assertFalse(lifecycle.coverage_inventory(expected, groups, scenarios, bad)['complete'])

    def test_cli_metadata_and_default_observation_fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)/'report.json'
            scenarios = lifecycle.load(lifecycle.HERE/'observation-chain-scenarios.json')['scenarios']
            def run(client, server, transport, adapters, scenario_file, timeout, mode, issuer):
                self.assertEqual(scenario_file, lifecycle.HERE/'observation-chain-scenarios.json')
                return self.results([(client, server, transport, mode)], scenarios)[0]
            with patch.object(lifecycle, 'discover', return_value={}), patch.object(lifecycle, 'Issuer') as issuer, patch.object(lifecycle, 'run_group', side_effect=run), redirect_stdout(io.StringIO()):
                code = lifecycle.main(['--suite', 'observation', '--shard-index', '2', '--shard-count', '3', '--output', str(output)])
            report = json.loads(output.read_text())
            self.assertEqual(code, 0)
            self.assertEqual(report['suite'], 'observation')
            self.assertEqual((report['shard_index'], report['shard_count']), (2, 3))
            self.assertEqual(report['groups'], 32)
            self.assertEqual(len(report['coverage']['expected_group_keys']), 96)
            self.assertEqual(len(report['scenario_sha256']), 64)


if __name__ == '__main__':
    unittest.main()
