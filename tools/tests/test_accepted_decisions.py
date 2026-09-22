"""Schema-only regressions for accepted identity/configuration/upload decisions."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import check_conformance as checker


class AcceptedDecisionTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = checker.Snapshot.resolve(ROOT)
        self.store = checker.SchemaStore(self.snapshot)
        self.validator = checker.SubsetValidator(self.store)

    def errors(self, value, name, pointer=()):
        path = self.snapshot.schema_dir / (name + '.schema.json')
        schema = self.store.load(path)
        for key in pointer:
            schema = schema[key]
        return self.validator.validate(value, schema, path)

    def event(self):
        return {'id': 'event', 'source': 'urn:test', 'type': 'tool.before',
                'time': '2026-09-15T12:00:00Z', 'call': {'id': 'call'},
                'tool': {'name': 'test', 'kind': 'custom', 'input': {}, 'origin': 'native'},
                'path': 'native'}

    def test_delivery_without_subscription_identity(self):
        params = {'protocolVersion': 'draft', 'event': self.event()}
        observe = {'jsonrpc': '2.0', 'method': 'hooks/observe', 'params': params}
        self.assertFalse(self.errors(observe, 'observe-notification'))
        intercept = copy.deepcopy(observe)
        intercept.update(id='event', method='hooks/intercept')
        intercept['params']['capabilities'] = {'effects': ['deny']}
        self.assertFalse(self.errors(intercept, 'intercept-request'))
        observe['id'] = 'event'
        self.assertTrue(self.errors(observe, 'observe-notification'))

    def test_no_schema_defines_wire_subscription_identity(self):
        for path in self.snapshot.schema_dir.glob('*.schema.json'):
            self.assertNotIn('"subscriptionId"', path.read_text(), path.name)

    def test_supplied_execution_needs_no_subscription(self):
        self.assertFalse(self.errors({'status': 'skipped', 'reason': 'supplied_result'},
                                     'execution-event', ('$defs', 'execution')))

    def test_registration_ignores_unknown_but_validates_known(self):
        auth = {'type': 'bearer', 'tokenEnv': 'TOKEN', 'future': {}}
        self.assertFalse(self.errors(auth, 'registration', ('$defs', 'authentication')))
        auth['tokenEnv'] = 42
        self.assertTrue(self.errors(auth, 'registration', ('$defs', 'authentication')))
        subscription = {'id': 'local', 'events': ['tool.before'], 'mode': 'intercept',
                        'timeoutMs': 100, 'failurePolicy': 'fail-open',
                        'content': {'default': 'metadata'}, 'future': {},
                        'filters': {'future': True}}
        self.assertFalse(self.errors(subscription, 'registration', ('$defs', 'interceptSubscription')))
        subscription['timeoutMs'] = 0
        self.assertTrue(self.errors(subscription, 'registration', ('$defs', 'interceptSubscription')))

    def test_upload_configuration_is_forward_compatible(self):
        upload = {'endpoint': 'https://upload.example/bytes', 'timeoutMs': 100,
                  'maxBytes': 0, 'future': True,
                  'auth': {'type': 'bearer', 'tokenEnv': 'TOKEN', 'future': {}}}
        self.assertFalse(self.errors(upload, 'content-upload'))
        upload['maxBytes'] = -1
        self.assertTrue(self.errors(upload, 'content-upload'))

    def test_capabilities_ignore_fields_not_invalid_operations(self):
        value = {'effects': ['modify'], 'future': {},
                 'modify': {'input': {'replace': True, 'merge': False, 'future': True}}}
        self.assertFalse(self.errors(value, 'capabilities'))
        value['modify']['input']['replace'] = 'yes'
        self.assertTrue(self.errors(value, 'capabilities'))
        self.assertTrue(self.errors({'effects': ['flow'], 'flow': {'operations': ['unknown']}},
                                    'capabilities'))

    def test_control_state_remains_open(self):
        pointer = ('allOf', 1, 'properties', 'params', 'properties', 'state')
        value = {'permission': 'none', 'future': {}, 'candidate': {'value': {}, 'future': True}}
        self.assertFalse(self.errors(value, 'intercept-request', pointer))
        value['flow'] = 'unknown'
        self.assertTrue(self.errors(value, 'intercept-request', pointer))

    def test_canonical_upload_descriptor(self):
        value = {'ref': 'receiver-ref', 'size': 0, 'sha256': 'a' * 64}
        self.assertFalse(self.errors(value, 'content-reference'))
        for field, invalid in [('ref', ''), ('size', -1), ('sha256', 'bad')]:
            with self.subTest(field=field):
                broken = dict(value, **{field: invalid})
                self.assertTrue(self.errors(broken, 'content-reference'))
                broken = dict(value)
                del broken[field]
                self.assertTrue(self.errors(broken, 'content-reference'))

    def test_unknown_effect_or_operation_rejects_whole_response(self):
        value = {'jsonrpc': '2.0', 'id': 'event', 'result': {
            'protocolVersion': 'draft', 'effects': [{'type': 'deny', 'reason': 'policy'}]}}
        self.assertFalse(self.errors(value, 'intercept-response'))
        for invalid in [{'type': 'unknown'}, {'type': 'flow', 'operation': 'unknown'}]:
            broken = copy.deepcopy(value)
            broken['result']['effects'].append(invalid)
            self.assertTrue(self.errors(broken, 'intercept-response'))


if __name__ == '__main__':
    unittest.main()
