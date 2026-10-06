"""Regression checks for shared draft registration and control contracts."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import check_conformance as checker


class ContractConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = checker.Snapshot.resolve(ROOT)
        self.store = checker.SchemaStore(self.snapshot)
        self.validator = checker.SubsetValidator(self.store)

    def errors(self, value, name, definition=None):
        path = self.snapshot.schema_dir / (name + '.schema.json')
        schema = self.store.load(path)
        if definition:
            schema = schema['$defs'][definition]
        return self.validator.validate(value, schema, path)

    def test_registration_uses_shared_flat_selection(self):
        for mode in ['intercept', 'observe']:
            subscription = {'events': ['tool.before'], 'mode': mode,
                            'content': {'default': 'omit', 'images': 'body',
                                        'future-category': 'metadata'}}
            if mode == 'intercept':
                subscription.update(timeoutMs=100, failurePolicy='fail-closed')
            definition = mode + 'Subscription'
            self.assertFalse(self.errors(subscription, 'registration', definition))
            subscription['content'] = {'default': 'omit', 'categories': {'images': 'body'}}
            self.assertTrue(self.errors(subscription, 'registration', definition))

    def test_upload_accepts_independent_explicit_auth_bindings(self):
        upload = {'endpoint': 'https://upload.example/path?scope=test',
                  'timeoutMs': 100, 'maxBytes': 1000}
        self.assertFalse(self.errors(upload, 'content-upload'))
        upload['auth'] = {'type': 'bearer', 'tokenEnv': 'UPLOAD_TOKEN'}
        self.assertFalse(self.errors(upload, 'content-upload'))
        for auth in [{'type': 'bearer', 'tokenRef': 'secret'},
                     {'type': 'oauth', 'resource': 'upload', 'issuer': 'https://issuer.example',
                      'clientId': 'test', 'flow': 'client_credentials'}]:
            self.assertFalse(self.errors({**upload, 'auth': auth}, 'content-upload'))

    def test_upload_remote_requires_https(self):
        upload = {'timeoutMs': 100, 'maxBytes': 1000}
        for endpoint in ['https://upload.example/path', 'http://localhost:8080/upload',
                         'http://127.0.0.1/upload', 'http://[::1]:8080/upload']:
            self.assertFalse(self.errors({**upload, 'endpoint': endpoint}, 'content-upload'))
        for endpoint in ['http://upload.example/path', 'http://localhost.evil.example/',
                         'http://127.0.0.1.evil.example/', 'file:///tmp/upload']:
            self.assertTrue(self.errors({**upload, 'endpoint': endpoint}, 'content-upload'))

    def test_shared_continuation_advertisement_requires_counts(self):
        capabilities = {'effects': ['flow'], 'flow': {'operations': ['continue']}}
        self.assertTrue(self.errors(capabilities, 'capabilities'))
        capabilities['flow'].update(remainingContinuations=0, continuationCount=2)
        self.assertFalse(self.errors(capabilities, 'capabilities'))
        for field in ['remainingContinuations', 'continuationCount']:
            flow = {**capabilities['flow'], field: -1}
            self.assertTrue(self.errors({**capabilities, 'flow': flow}, 'capabilities'))
        self.assertFalse(self.errors({'effects': ['flow'], 'flow': {'operations': ['stop']}},
                                     'capabilities'))

    def test_continue_instruction_is_optional_but_nonempty_when_present(self):
        effect = {'type': 'flow', 'operation': 'continue'}
        self.assertFalse(self.errors(effect, 'effect'))
        self.assertFalse(self.errors({**effect, 'instruction': 'Keep going'}, 'effect'))
        self.assertTrue(self.errors({**effect, 'instruction': ''}, 'effect'))

    def test_capability_control_object_accepts_unknown_fields(self):
        capabilities = {'effects': ['deny']}
        self.assertFalse(self.errors(capabilities, 'capabilities'))
        self.assertFalse(self.errors({**capabilities, 'flwo': {'operations': ['stop']}},
                                    'capabilities'))


    def test_configuration_accepts_unknown_fields(self):
        upload = {'endpoint': 'https://upload.example/path',
                  'timeoutMs': 100, 'maxBytes': 1000,
                  'futureOption': {'enabled': True},
                  'auth': {'type': 'bearer', 'tokenEnv': 'UPLOAD_TOKEN',
                           'futureOption': True}}
        self.assertFalse(self.errors(upload, 'content-upload'))
        registration = json.loads((ROOT / 'fixtures/draft/registration/portable.valid.json').read_text())
        registration['futureOption'] = True
        registration['hooks'][0]['futureOption'] = True
        self.assertFalse(self.errors(registration, 'registration'))

    def test_effects_reject_unknown_fields_and_types(self):
        effects = [
            {'type': 'allow'},
            {'type': 'ask'},
            {'type': 'deny', 'reason': 'Policy'},
            {'type': 'modify', 'target': 'input', 'operation': 'merge', 'value': {}},
            {'type': 'message', 'text': 'Notice'},
            {'type': 'return', 'value': None},
            {'type': 'flow', 'operation': 'stop', 'reason': 'Done'},
            {'type': 'flow', 'operation': 'continue'},
            {'type': 'inject', 'target': 'context', 'operation': 'append',
             'deliverAt': 'now', 'value': {}},
        ]
        for effect in effects:
            with self.subTest(effect=effect):
                self.assertFalse(self.errors(effect, 'effect'))
                self.assertTrue(self.errors({**effect, 'futureOption': True}, 'effect'))
        self.assertTrue(self.errors({'type': 'future-effect'}, 'effect'))
        self.assertTrue(self.errors({'effects': ['future-effect']}, 'capabilities'))


class OperationOwnershipContractTests(unittest.TestCase):
    """Guard normative SDK ownership wording; not runtime delivery evidence."""

    def text(self, name):
        return (ROOT / 'spec' / 'draft' / name).read_text()

    def test_observation_is_owned_without_becoming_a_decision(self):
        composition = self.text('base/composition.md')
        self.assertNotIn('Observer delivery MUST NOT delay the interceptor chain or tool execution', composition)
        self.assertIn('MUST NOT leave detached observer deliveries', composition)
        self.assertIn('MUST obtain the interception decision', composition)
        self.assertIn('MUST NOT initiate further best-effort observations', composition)
        failure = self.text('base/failure.md')
        self.assertNotIn('Observe subscriptions are always non-blocking', failure)
        self.assertIn('failure never changes the settled decision', failure)

    def test_outer_budget_does_not_reset_or_own_borrowed_resources(self):
        failure = self.text('base/failure.md')
        self.assertIn('MUST NOT reset or extend the enclosing deadline', failure)
        self.assertIn('remain caller-owned unless ownership was explicitly transferred', failure)
        self.assertIn('Bounded safety cleanup may finish after the processing deadline', failure)

    def test_lazy_content_and_external_auth_keep_security_boundaries(self):
        content = self.text('content-upload.md')
        self.assertIn('Constructing an owned source MUST NOT read or upload', content)
        self.assertIn('MUST be checked against actual bytes', content)
        auth = self.text('capability-auth.md')
        self.assertIn('Delegation does not relax the authentication requirements', auth)
        self.assertIn('MUST NOT appear in diagnostics or credential-attempt identifiers', auth)


if __name__ == '__main__':
    unittest.main()
