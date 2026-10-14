"""Shared synthetic hosts emit originating harness identity on every event."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from elicitation_matrix import cases, envelopes
from observation_wire import ObservationValidator

HERE = Path(__file__).resolve().parent
HARNESS = {'name': 'example-harness', 'version': '1.0.0'}


def events(value):
    if isinstance(value, dict):
        if {'id', 'source', 'type'} <= value.keys():
            yield value
        for child in value.values():
            yield from events(child)
    elif isinstance(value, list):
        for child in value:
            yield from events(child)


class HarnessEventTests(unittest.TestCase):
    def test_shared_scenarios_carry_originating_harness(self):
        for name in ('scenarios.json', 'catalogue-scenarios.json',
                     'lifecycle-scenarios.json', 'observation-chain-scenarios.json'):
            with self.subTest(document=name):
                emitted = list(events(json.loads((HERE / name).read_text())))
                self.assertTrue(emitted)
                for event in emitted:
                    self.assertEqual(event['harness'], HARNESS)

    def test_elicitation_boundaries_carry_originating_harness(self):
        fixture = cases()[0]
        for envelope, _, _ in envelopes('harness-test', fixture['request'], fixture['result']):
            self.assertEqual(envelope['params']['event']['harness'], HARNESS)

    def test_validation_rejects_missing_harness_without_repair(self):
        request = json.loads((HERE / 'scenarios.json').read_text())['scenarios'][0]['request']
        validator = ObservationValidator()
        self.assertFalse(validator.schema_errors(request, 'intercept-request'))
        del request['params']['event']['harness']
        before = deepcopy(request)
        self.assertTrue(validator.schema_errors(request, 'intercept-request'))
        self.assertEqual(request, before)
        note = {'jsonrpc': '2.0', 'method': 'hooks/observe', 'params': {
            'protocolVersion': 'draft', 'event': request['params']['event']}}
        self.assertTrue(validator.errors(note))
        self.assertNotIn('harness', note['params']['event'])
