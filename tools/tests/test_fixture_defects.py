"""Negative fixtures must become valid by repairing only their named defect."""
import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import check_conformance as checker


# A path and replacement (or deletion) identify the entire repair. Baseline
# protocol fields must already be present, never supplied by the test.
DELETE = object()
REPAIRS = {
    'content-negative-size': ('params.event.items.0.body.size', 3),
    'content-bad-hash': ('params.event.items.0.body.sha256', 'a' * 64),
    'model-item-role-missing': ('params.event.items.0.role', 'assistant'),
    'intercept-request-id-mismatch': ('id', 'evt_http_inner'),
    'model-response-input-target': ('params.capabilities.modify', {'response': {'replace': True, 'merge': False}}),
    'tool-before-continue-capability': ('params.capabilities.flow.operations', ['stop']),
    'metadata-body': ('params.event.items.0.body', DELETE),
    'observe-session-start-tool': ('params.event.tool', DELETE),
    'observe-with-id': ('id', DELETE),
    'task-id-missing': ('params.event.task.id', 'task1'),
    'observe-unknown-event': ('params.event.type', 'tool.before'),
    'observe-tool-after-error': ('params.event.error', DELETE),
    'observe-session-end-outcome': ('params.event.outcome', 'completed'),
    'intercept-request-effects-missing': ('params.capabilities.effects', []),
    'task-return-capability': ('params.capabilities.effects', []),
    'parent-empty': ('params.event.parentEventId', 'proposal1'),
    'observe-tool-error-output': ('params.event.tool.output', DELETE),
    'observe-tool-after-input-missing': ('params.event.tool.input', {'path': 'README.md'}),
    'observe-tool-error-message-missing': ('params.event.error.message', 'File not found'),
    'content-inline': ('params.event.items.0.text', DELETE),
}


class FixtureDefectTests(unittest.TestCase):
    def test_multiline_request_needs_only_framing_repair(self):
        snapshot = checker.Snapshot.resolve(ROOT)
        store = checker.SchemaStore(snapshot)
        validator = checker.SubsetValidator(store)
        case = next(c for c in json.loads(snapshot.fixture_manifest_path.read_text())['cases']
                    if c['id'] == 'stdio.intercept-request-multiline.invalid')
        path = ROOT / case['path']
        self.assertTrue(checker.parse_fixture(case, path)[1])
        value = json.loads(path.read_text())
        with tempfile.TemporaryDirectory() as directory:
            repaired = Path(directory) / 'request.jsonl'
            repaired.write_text(json.dumps(value, separators=(',', ':')) + '\n')
            parsed, errors = checker.parse_fixture(case, repaired)
            self.assertEqual([], errors)
            self.assertEqual(value, parsed)
        for name in [case['schema'], 'schema/draft/schema.json']:
            schema_path = ROOT / name
            self.assertEqual([], validator.validate(value, store.load(schema_path), schema_path)
                             + checker.semantic_errors(value, schema_path))

    def test_event_documentation_wire_examples_validate(self):
        snapshot = checker.Snapshot.resolve(ROOT)
        store = checker.SchemaStore(snapshot)
        validator = checker.SubsetValidator(store)
        schema_path = snapshot.schema_dir / 'schema.json'
        examples = re.findall(r'```json\n(.*?)\n```',
                              (ROOT / 'spec/draft/base/events.md').read_text(),
                              re.DOTALL)
        messages = [json.loads(example) for example in examples]
        messages = [value for value in messages if 'jsonrpc' in value]
        self.assertEqual(4, len(messages))
        for value in messages:
            with self.subTest(message=value.get('method', value.get('result'))):
                self.assertEqual([], validator.validate(value, store.load(schema_path), schema_path)
                                 + checker.semantic_errors(value, schema_path))

    def test_named_defect_is_sufficient_to_explain_rejection(self):
        snapshot = checker.Snapshot.resolve(ROOT)
        store = checker.SchemaStore(snapshot)
        validator = checker.SubsetValidator(store)
        cases = json.loads(snapshot.fixture_manifest_path.read_text())['cases']
        for name, (pointer, replacement) in REPAIRS.items():
            with self.subTest(fixture=name):
                path = 'fixtures/draft/http/' + name + '.invalid.json'
                case = next(c for c in cases if c['path'] == path)
                original = json.loads((ROOT / path).read_text())
                repaired = copy.deepcopy(original)
                target = repaired
                parts = pointer.split('.')
                for part in parts[:-1]:
                    target = target[int(part)] if isinstance(target, list) else target[part]
                if replacement is DELETE:
                    del target[parts[-1]]
                else:
                    target[parts[-1]] = copy.deepcopy(replacement)
                # Test both the fixture's declared schema and the aggregate wire
                # schema; semantic request-ID equality is checked separately.
                for schema_name in [case['schema'], 'schema/draft/schema.json']:
                    schema_path = ROOT / schema_name
                    schema = store.load(schema_path)
                    errors = lambda value: (validator.validate(value, schema, schema_path)
                                            + checker.semantic_errors(value, schema_path))
                    self.assertTrue(errors(original), schema_name)
                    self.assertEqual([], errors(repaired), schema_name)


if __name__ == '__main__':
    unittest.main()
