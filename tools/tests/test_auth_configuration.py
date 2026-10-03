"""Configuration acceptance only; these tests do not execute auth discovery."""
import copy
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import check_conformance as checker


class AuthenticationConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = checker.Snapshot.resolve(ROOT)
        self.store = checker.SchemaStore(self.snapshot)
        self.validator = checker.SubsetValidator(self.store)
        text = (ROOT / 'spec/draft/base/registration.md').read_text()
        self.config = json.loads(re.findall(r'```json\n(.*?)\n```', text, re.S)[-1])

    def errors(self, value):
        path = self.snapshot.schema_dir / 'registration.schema.json'
        return self.validator.validate(value, self.store.load(path), path)

    def with_bindings(self, event=None, upload=None):
        config = copy.deepcopy(self.config)
        backend = config['hooks'][0]
        if event is not None:
            backend['authentication'] = event
        if upload is not None:
            backend['subscriptions'][0]['upload']['auth'] = upload
        return config

    def test_omitted_bindings_are_valid_and_not_materialized(self):
        before = copy.deepcopy(self.config)
        self.assertFalse(self.errors(self.config))
        self.assertEqual(before, self.config)
        backend = self.config['hooks'][0]
        self.assertNotIn('authentication', backend)
        self.assertNotIn('auth', backend['subscriptions'][0]['upload'])

    def test_upload_and_event_bindings_are_independent(self):
        bearer = {'type': 'bearer', 'tokenRef': 'secrets/event'}
        for config in (self.with_bindings(event=bearer),
                       self.with_bindings(upload=bearer),
                       self.with_bindings(event=bearer, upload={'type': 'bearer', 'tokenEnv': 'UPLOAD_TOKEN'})):
            with self.subTest(config=config):
                self.assertFalse(self.errors(config))

    def test_shared_explicit_binding_shapes(self):
        bindings = [
            {'type': 'bearer', 'tokenEnv': 'TOKEN'},
            {'type': 'bearer', 'tokenRef': 'secrets/token'},
            {'type': 'oauth', 'issuer': 'https://issuer.example',
             'resource': 'https://hooks.example/events', 'clientId': 'client',
             'flow': 'authorization_code_pkce', 'scopes': ['hooks']},
        ]
        for binding in bindings:
            with self.subTest(binding=binding):
                self.assertFalse(self.errors(self.with_bindings(event=binding, upload=binding)))

    def test_invalid_bindings_rejected_at_either_endpoint(self):
        invalid = [
            {}, {'type': 'anonymous'}, {'type': 'bearer'},
            {'type': 'bearer', 'tokenEnv': 'TOKEN', 'tokenRef': 'secret'},
            {'type': 'bearer', 'tokenEnv': 'not an env name'},
            {'type': 'bearer', 'tokenRef': ''},
            {'type': 'oauth', 'issuer': 'http://issuer.example',
             'resource': 'resource', 'clientId': 'client', 'flow': 'client_credentials'},
        ]
        for binding in invalid:
            with self.subTest(binding=binding):
                self.assertTrue(self.errors(self.with_bindings(event=binding)))
                self.assertTrue(self.errors(self.with_bindings(upload=binding)))

    def test_recognized_bindings_accept_unknown_fields(self):
        binding = {'type': 'bearer', 'tokenRef': 'identity', 'futureField': 'ignored'}
        self.assertFalse(self.errors(self.with_bindings(event=binding, upload=binding)))

    def test_explicit_http_binding_remains_invalid_on_stdio(self):
        config = self.with_bindings(event={'type': 'bearer', 'tokenEnv': 'TOKEN'})
        config['hooks'][0]['transport'] = {'type': 'stdio', 'command': 'hook'}
        self.assertTrue(self.errors(config))


if __name__ == '__main__':
    unittest.main()
