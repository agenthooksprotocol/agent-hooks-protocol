"""Known value contracts are schema-owned and selected by the request event."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import check_conformance as checker


class TypedContractTests(unittest.TestCase):
    def setUp(self):
        self.validator = checker.SubsetValidator(checker.SchemaStore(checker.Snapshot.resolve(ROOT)))

    def errors(self, value, document, definition=None):
        path = ROOT / 'schema/draft' / (document + '.schema.json')
        schema = json.loads(path.read_text()) if definition is None else {'$ref': '#/$defs/' + definition}
        return self.validator.validate(value, schema, path)

    def response(self, effects=None):
        result = {'protocolVersion': 'draft'}
        if effects is not None:
            result['effects'] = effects
        return {'jsonrpc': '2.0', 'id': 'event-1', 'result': result}

    def test_contextual_returns_do_not_accept_opaque_fallback(self):
        contracts = {
            'ModelRequestBeforeInterceptResponse': [],
            'ContextCompactBeforeInterceptResponse': [],
            'UserElicitationRequestInterceptResponse': {'action': 'accept', 'content': {'ok': True}},
            'ToolBeforeInterceptResponse': None,
        }
        for name, value in contracts.items():
            with self.subTest(name=name):
                self.assertEqual([], self.errors(self.response([{'type': 'return', 'value': value}]), 'intercept-response', name))
                if name != 'ToolBeforeInterceptResponse':
                    self.assertTrue(self.errors(self.response([{'type': 'return', 'value': {'opaque': {'nested': True}}}]), 'intercept-response', name))
        self.assertTrue(self.errors(self.response([{'type': 'return', 'value': []}]), 'intercept-response', 'UserMessageOutboundInterceptResponse'))

    def test_neutral_effect_presence_and_typed_no_effect_items(self):
        for document in ['intercept-response', 'intercept-no-effect-response']:
            for effects in [None, []]:
                self.assertEqual([], self.errors(self.response(effects), document))
        schema = json.loads((ROOT / 'schema/draft/intercept-no-effect-response.schema.json').read_text())
        self.assertEqual({'$ref': 'effect.schema.json'}, schema['allOf'][1]['properties']['result']['properties']['effects']['items'])

    def test_known_modification_values(self):
        contracts = [('ModifyMessagesEffect', 'output', []), ('ModifyTextEffect', 'summary', []), ('ModifyWorkspaceEffect', 'workspace', {'cwd': '/repo'}), ('ModifyFormEffect', 'content', {'selected': ['a', 'b'], 'count': 1.5})]
        for name, target, value in contracts:
            effect = {'type': 'modify', 'target': target, 'operation': 'merge', 'value': value}
            self.assertEqual([], self.errors(effect, 'effect', name))
            effect['value'] = {'invalid': {'nested': True}}
            self.assertTrue(self.errors(effect, 'effect', name))

    def test_pending_candidate_null_is_distinct_from_null_result(self):
        self.assertEqual([], self.errors({'permission': 'none', 'candidate': None}, 'intercept-request', 'MessagesState'))
        self.assertTrue(self.errors({'permission': 'none', 'candidate': {'value': None}}, 'intercept-request', 'MessagesState'))
        self.assertEqual([], self.errors({'permission': 'none', 'candidate': {'value': None, 'provenance': {'future': {'x': True}}}}, 'intercept-request', 'ToolState'))
        self.assertTrue(self.errors({'permission': 'none', 'candidate': {'provenance': {}}}, 'intercept-request', 'ToolState'))

    def test_pending_injections_are_full_scheduled_effects(self):
        state = {'permission': 'none', 'candidate': None, 'injections': [{'type': 'inject', 'target': 'context', 'operation': 'append', 'deliverAt': 'next_turn', 'value': []}]}
        self.assertEqual([], self.errors(state, 'intercept-request', 'ToolState'))
        invalid = copy.deepcopy(state)
        del invalid['injections'][0]['deliverAt']
        self.assertTrue(self.errors(invalid, 'intercept-request', 'ToolState'))

    def test_host_counters_have_no_invented_integer_maximum(self):
        for counters in [{}, {'turns': 0}, {'modelRequests': 2, 'toolCalls': 3}, {'inputTokens': 2**80, 'futureCounter': 2**100}]:
            self.assertEqual([], self.errors(counters, 'common', 'SessionCounters'))
        for counters in [{'turns': -1}, {'toolCalls': 1.5}, {'futureCounter': '1'}, {'outputTokens': True}]:
            self.assertTrue(self.errors(counters, 'common', 'SessionCounters'))

    def test_form_maps_reject_unknown_shapes(self):
        self.assertEqual([], self.errors({'text': 'a', 'number': 1.25, 'flag': True, 'choices': ['a']}, 'mcp-elicitation', 'FormAnswers'))
        for value in [{'x': None}, {'x': {}}, {'x': [1]}, {'x': [['a']]}]:
            self.assertTrue(self.errors(value, 'mcp-elicitation', 'FormAnswers'))


if __name__ == '__main__':
    unittest.main()
