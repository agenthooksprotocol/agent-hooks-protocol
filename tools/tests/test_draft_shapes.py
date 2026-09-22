"""Focused contracts beyond the catalogue fixture matrix."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import check_conformance as checker


class DraftShapeTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = checker.Snapshot.resolve(ROOT)
        self.store = checker.SchemaStore(self.snapshot)
        self.validator = checker.SubsetValidator(self.store)

    def errors(self, value, name='schema'):
        path = self.snapshot.schema_dir / (name + '.json' if name == 'schema' else name + '.schema.json')
        return self.validator.validate(value, self.store.load(path), path)

    def request(self):
        return json.loads((ROOT / 'fixtures/draft/http/intercept-request.valid.json').read_text())

    def test_request_continuation_requires_nonnegative_accounting(self):
        request = self.request()
        request['params']['event'] = json.loads((ROOT / 'fixtures/draft/http/observe-tool-after.valid.json').read_text())['params']['event']
        request['id'] = request['params']['event']['id']
        caps = request['params']['capabilities']
        caps.update(effects=['flow'], flow={'operations': ['continue']})
        self.assertTrue(self.errors(request))
        caps['flow'].update(remainingContinuations=0, continuationCount=2)
        self.assertFalse(self.errors(request))  # runtime rejects continue at exhausted allowance
        caps['flow']['remainingContinuations'] = -1
        self.assertTrue(self.errors(request))

    def test_stop_is_independent_of_continuation(self):
        request = self.request()
        request['params']['capabilities'] = {'effects': ['flow'], 'flow': {'operations': ['stop']}}
        self.assertFalse(self.errors(request))

    def test_capability_family_details_are_required(self):
        for effect in ['flow', 'inject', 'modify']:
            self.assertTrue(self.errors({'effects': [effect]}, 'capabilities'))
        self.assertFalse(self.errors({'effects': []}, 'capabilities'))

    def test_observation_only_boundaries_cannot_be_intercepted(self):
        request = self.request()
        for event in ['task.change.after', 'turn.end', 'model.error']:
            request['params']['event'] = {'id': request['id'], 'source': 'urn:test',
                'time': '2026-09-15T12:00:00Z', 'type': event,
                'turn': {'id': 'turn'}, 'outcome': 'completed',
                'task': {'id': 'task', 'operation': 'update', 'change': {}}}
            self.assertTrue(self.errors(request))

    def test_one_completion_distinguishes_supplied_and_executed(self):
        value = json.loads((ROOT / 'fixtures/draft/http/observe-tool-error.valid.json').read_text())
        self.assertFalse(self.errors(value))
        event = value['params']['event']
        event['execution'] = {'status': 'skipped', 'reason': 'supplied_result', 'subscriptionId': 'cache'}
        self.assertFalse(self.errors(value))
        event['type'] = 'tool.error'
        self.assertTrue(self.errors(value))

    def test_content_selection_requires_default(self):
        self.assertTrue(self.errors({'text': 'body'}, 'content-selection'))
        self.assertFalse(self.errors({'default': 'metadata', 'text': 'body', 'audio': 'omit'}, 'content-selection'))

    def test_discovery_has_no_session_dependency(self):
        value = {'jsonrpc': '2.0', 'id': 'discovery', 'method': 'hooks/capabilities',
                 'params': {'protocolVersion': 'draft'}}
        self.assertFalse(self.errors(value))
        value['params']['session'] = {'id': 'invented'}
        self.assertTrue(self.errors(value))

    def test_invalid_member_rejects_complete_response(self):
        value = {'jsonrpc': '2.0', 'id': 'event', 'result': {'protocolVersion': 'draft',
                 'effects': [{'type': 'deny', 'reason': 'policy'}, {'type': 'flow', 'operation': 'retry'}]}}
        self.assertTrue(self.errors(value))
        value['result']['effects'].pop()
        self.assertFalse(self.errors(value))

    def test_all_catalogue_events_require_typed_payloads(self):
        """No wired event may regress to its old permissive envelope."""
        def required(schema, path):
            if '$ref' in schema:
                filename, _, pointer = schema['$ref'].partition('#')
                path = path.parent / filename if filename else path
                schema = self.store.load(path)
                for key in pointer.strip('/').split('/') if pointer else []:
                    schema = schema[int(key) if isinstance(schema, list) else key]
            result = set(schema.get('required', []))
            for branch in schema.get('allOf', []):
                result.update(required(branch, path))
            return result

        path = ROOT / 'schema/draft/catalogue-event.schema.json'
        catalogue = self.store.load(path)['$defs']
        for fixture in sorted((ROOT / 'fixtures/draft/http').glob('catalogue-*.valid.json')):
            value = json.loads(fixture.read_text())
            event = value['params']['event']
            with self.subTest(event=event['type']):
                self.assertFalse(self.errors(value))
                fields = required(catalogue[event['type']], path)
                self.assertTrue(fields - {'id', 'source', 'time', 'type'})
                for field in fields:
                    invalid = copy.deepcopy(value)
                    invalid['params']['event'].pop(field, None)
                    self.assertTrue(self.errors(invalid), (event['type'], field))

    def test_observation_has_subscription_identity_without_disposition(self):
        value = json.loads((ROOT / 'fixtures/draft/http/observe-tool-before.valid.json').read_text())
        self.assertNotIn('disposition', value['params'])
        self.assertFalse(self.errors(value))
        schema = self.store.load(ROOT / 'schema/draft/observe-notification.schema.json')
        for branch in schema['allOf']:
            params = branch.get('properties', {}).get('params', {})
            self.assertNotIn('disposition', params.get('properties', {}))
            self.assertNotIn('disposition', params.get('required', []))
        self.assertFalse((ROOT / 'schema/draft/observation-disposition.schema.json').exists())
        del value['params']['subscriptionId']
        self.assertTrue(self.errors(value))

    def test_binary_binding_configuration_and_descriptor(self):
        upload = {'endpoint': 'https://example.com/receive?scope=one', 'timeoutMs': 1, 'maxBytes': 0}
        self.assertFalse(self.errors(upload, 'content-upload'))
        for key, bad in [('endpoint', 'file:///tmp/body'), ('timeoutMs', 0), ('maxBytes', -1)]:
            self.assertTrue(self.errors({**upload, key: bad}, 'content-upload'))
        item = {'id': 'skill', 'kind': 'skill', 'mediaType': 'application/octet-stream',
                'selection': 'body', 'body': {'ref': 'binary', 'size': 0, 'sha256': '0' * 64}}
        self.assertFalse(self.errors(item, 'content-item'))
        self.assertTrue(self.errors({**item, 'body': {'text': 'inline'}}, 'content-item'))
        self.assertTrue(self.errors({**item, 'selection': 'metadata'}, 'content-item'))
        self.assertFalse(self.errors({'default': 'omit', 'future-category': 'metadata'}, 'content-selection'))

    def test_static_manifest_coverage_is_explicit(self):
        value = json.loads((ROOT / 'fixtures/draft/http/capabilities-response.valid.json').read_text())
        manifest = value['result']['manifest']
        for field in ['toolPaths', 'limits', 'managedPolicy', 'correlationIdentityFields']:
            invalid = copy.deepcopy(value)
            del invalid['result']['manifest'][field]
            self.assertTrue(self.errors(invalid))
        manifest['events'][0]['modes'] = ['intercept']  # task.change.after cannot gate
        self.assertTrue(self.errors(value))
        manifest['events'][0]['modes'] = ['observe']
        manifest['managedPolicy'] = {'scopes': ['managed'], 'disableable': True}
        self.assertTrue(self.errors(value))

    def test_attempt_usage_and_supplied_execution(self):
        value = json.loads((ROOT / 'fixtures/draft/http/catalogue-model.response.after.valid.json').read_text())
        event = value['params']['event']
        event['usage'] = {'kind': 'amount', 'scope': 'attempt', 'completeness': 'complete',
                          'provenance': 'provider', 'inputTokens': 0}
        self.assertFalse(self.errors(value))
        for key, invalid in [('kind', 'total'), ('scope', 'turn'), ('inputTokens', -1), ('provenance', 'mixed')]:
            altered = copy.deepcopy(value)
            altered['params']['event']['usage'][key] = invalid
            self.assertTrue(self.errors(altered), key)
        event['execution'] = {'status': 'skipped', 'reason': 'supplied_result', 'subscriptionId': 'cache'}
        self.assertTrue(self.errors(value))  # supplied results cannot fabricate provider usage
        event.pop('usage')
        self.assertFalse(self.errors(value))

    def test_workspace_changes_are_not_file_notifications(self):
        value = json.loads((ROOT / 'fixtures/draft/http/catalogue-workspace.change.after.valid.json').read_text())
        value['params']['event']['workspace']['change'] = {}
        self.assertTrue(self.errors(value))
        value = json.loads((ROOT / 'fixtures/draft/http/catalogue-file.changed.valid.json').read_text())
        value['params']['event']['changes'][0].pop('agentCaused')
        self.assertTrue(self.errors(value))

    def test_review_regression_fixtures(self):
        manifest = json.loads((ROOT / 'fixtures/draft/manifest.json').read_text())
        names = ('model-response-', 'synthesized-', 'timestamp-compact', 'source-malformed',
                 'hash-newline-', 'model-item-role-', 'model-child-', 'generic-file-',
                 'task-return-', 'tool-before-continue-')
        for case in manifest['cases']:
            if case['binding'] == 'http-json' and any(n in case['id'] for n in names):
                with self.subTest(case=case['id']):
                    value = json.loads((ROOT / case['path']).read_text())
                    self.assertEqual(case['expectedValid'], not self.errors(value))

    def test_subscription_requires_explicit_content_default(self):
        value = json.loads((ROOT / 'fixtures/draft/registration/portable.valid.json').read_text())
        for mode in ['observe', 'intercept']:
            invalid = copy.deepcopy(value)
            sub = next(s for h in invalid['hooks'] for s in h['subscriptions'] if s['mode'] == mode)
            del sub['content']
            self.assertTrue(self.errors(invalid, 'registration'))
            sub['content'] = {'text': 'body'}
            self.assertTrue(self.errors(invalid, 'registration'))
            sub['content'] = {'default': 'metadata'}
            self.assertFalse(self.errors(invalid, 'registration'))

    def test_uri_and_rfc3339_formats(self):
        valid_uris = ['urn:example:event', 'https://example.com/a%20b?x=1#fragment',
                      'file:///tmp/a', 'https://[::1]:3000/a', 'https://[v1.host]/',
                      'mailto:user@example.com', 'custom:opaque/path']
        invalid_uris = ['relative/path', 'https://bad host/%zz', 'https://example.com/%',
                        'https://[not-ip]/', 'https://example.com/\n', 'https://éxample.com/',
                        'https://example.com:bad/', 'https://example.com/[]']
        for uri in valid_uris:
            self.assertTrue(checker.valid_uri(uri), uri)
        for uri in invalid_uris:
            self.assertFalse(checker.valid_uri(uri), uri)
        valid_times = ['2026-09-15T12:00:00Z', '2026-09-15t12:00:00.123z',
                       '2024-02-29T23:59:59-00:00', '2026-09-15T12:00:00+05:30']
        invalid_times = ['20260915T120000+0000', '2026-09-15 12:00:00Z',
                         '2026-09-15T12:00:00', '2026-09-15T12:00:00+0000',
                         '2026-02-29T12:00:00Z', '2026-09-15T12:00:00+00:60',
                         '2026-09-15T12:00:00Z\n']
        for timestamp in valid_times:
            self.assertTrue(checker.valid_datetime(timestamp), timestamp)
        for timestamp in invalid_times:
            self.assertFalse(checker.valid_datetime(timestamp), timestamp)
        self.assertTrue(checker.type_matches(1.0, 'integer'))
        self.assertFalse(checker.type_matches(True, 'integer'))
        self.assertFalse(checker.type_matches(1.5, 'integer'))

    def test_request_and_manifest_share_capability_semantics(self):
        response = json.loads((ROOT / 'fixtures/draft/http/capabilities-response.valid.json').read_text())
        cases = [
            ('model.response.after', {'effects': ['modify'], 'modify': {'response': {'replace': True, 'merge': False}}}, True),
            ('model.response.after', {'effects': ['flow'], 'flow': {'operations': ['stop']}}, True),
            ('model.response.after', {'effects': ['modify'], 'modify': {'input': {'replace': True, 'merge': False}}}, False),
            ('task.change.before', {'effects': ['return']}, False),
            ('tool.before', {'effects': ['flow'], 'flow': {'operations': ['continue'], 'remainingContinuations': 1, 'continuationCount': 0}}, False),
            ('task.change.before', {'effects': ['message']}, True),
        ]
        for event, caps, valid in cases:
            with self.subTest(event=event, caps=caps):
                response['result']['manifest']['events'] = [{'event': event, 'modes': ['intercept'], 'capabilities': caps}]
                self.assertEqual(valid, not self.errors(response))

    def test_generic_items_and_owned_blocks_have_distinct_role_contracts(self):
        generic = {'id': 'file', 'kind': 'file', 'mediaType': 'text/plain', 'selection': 'metadata'}
        self.assertFalse(self.errors(generic, 'content-item'))
        path = ROOT / 'schema/draft/content-item.schema.json'
        schema = self.store.load(path)['$defs']['modelVisibleItem']
        self.assertTrue(self.validator.validate(generic, schema, path))
        child = {**generic, 'id': 'reasoning', 'kind': 'reasoning', 'parentItemId': 'assistant'}
        self.assertTrue(self.validator.validate(child, schema, path))
        child['role'] = 'assistant'
        self.assertFalse(self.validator.validate(child, schema, path))
        # Equal roles and an existing parent require runtime cross-item checks.

    def test_execution_literal_normalization_preserves_canonical_validity(self):
        path = ROOT / 'schema/draft/execution-event.schema.json'
        current = self.store.load(path)['$defs']['execution']
        old = copy.deepcopy(current)
        ordinary = copy.deepcopy(current['oneOf'][2])
        ordinary['properties']['reason'] = {'enum': ['policy', 'cancelled', 'timeout', 'other']}
        old['oneOf'] = old['oneOf'][:2] + [ordinary]
        for status in ['executed', 'skipped']:
            for reason in [None, 'supplied_result', 'policy', 'cancelled', 'timeout', 'other', 'future']:
                for supplier in ['absent', None, 'supplier']:
                    value = {'status': status}
                    if reason is not None:
                        value['reason'] = reason
                    if supplier != 'absent':
                        value['subscriptionId'] = supplier
                    with self.subTest(value=value):
                        self.assertEqual(bool(self.validator.validate(value, old, path)),
                                         bool(self.validator.validate(value, current, path)))
