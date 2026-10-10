"""Canonical wire values and host-context edit semantics (no upload required)."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import check_conformance as checker


def text(value='hello', id='p1'):
    return {'id': id, 'kind': 'text', 'mediaType': 'text/plain',
            'selection': 'body', 'text': value}


def message(parts=None, id='m1'):
    return {'id': id, 'role': 'user', 'parts': [text()] if parts is None else parts}


def edit(value, target='prompt', operation='replace'):
    return {'type': 'modify', 'target': target, 'operation': operation, 'value': value}


class InlineEditTests(unittest.TestCase):
    def setUp(self):
        targets = checker.EDIT_TARGETS
        self.context = checker.EditContext(
            frozenset((t, op) for t in targets for op in ('replace', 'merge')),
            {t: 'body' for t in targets}, frozenset(targets), frozenset(targets))
        self.current = {'prompt': [message()], 'instructions': [text()],
                        'input': {'nested': {'a': 1}, 'keep': True},
                        'workspace': {}, 'output': [message()]}

    def apply(self, effects, current=None, context=None):
        return checker.apply_modify_response(
            self.current if current is None else current, effects,
            self.context if context is None else context)

    def test_append_preserves_order_and_duplicates(self):
        for target, value in [('prompt', [message()]), ('instructions', [text()])]:
            with self.subTest(target=target):
                result = self.apply([edit(value, target, 'merge')])
                self.assertEqual(value + value, result[target])
                self.assertEqual(value, self.current[target])

    def test_replace_substitutes_instead_of_appending(self):
        replacement = [message([text('new')], id='m2')]
        result = self.apply([edit(replacement)])
        self.assertEqual(replacement, result['prompt'])
        result['prompt'][0]['parts'][0]['text'] = 'detached'
        self.assertEqual('new', replacement[0]['parts'][0]['text'])
        self.assertEqual([], self.apply([edit([])])['prompt'])

    def test_object_merge_is_shallow_and_preserves_null(self):
        result = self.apply([edit({'nested': {'b': 2}, 'keep': None}, 'input', 'merge')])
        self.assertEqual({'nested': {'b': 2}, 'keep': None}, result['input'])
        result = self.apply([edit({}, 'input')])
        self.assertEqual({}, result['input'])

    def test_invalid_target_types(self):
        for target, value in [('prompt', 'hello'), ('request', {}), ('response', [text()]),
                              ('content', [text()]), ('instructions', [message()]),
                              ('summary', 'hello'), ('input', []), ('workspace', None)]:
            with self.subTest(target=target):
                with self.assertRaises(checker.CheckFailure):
                    self.apply([edit(value, target)])
        with self.assertRaises(checker.CheckFailure):
            self.apply([edit({}, 'workspace')], current={'workspace': []})

    def test_contextual_content_fixtures(self):
        manifest = json.loads((ROOT / 'fixtures/draft/manifest.json').read_text())
        for case in manifest['cases']:
            if 'editContext' not in case:
                continue
            with self.subTest(case=case['id']):
                host = case['editContext']
                response = json.loads((ROOT / case['path']).read_text())
                effects = response['result']['effects']
                self.assertEqual(case['expectedValid'], not checker.draft_value_errors(response, {
                    '$ref': 'intercept-response.schema.json'}))
                context = replace(self.context, event=host['event'],
                                  elicitation_mode=host['mode'], elicitation_action=host['action'])
                before = copy.deepcopy(host['current'])
                if case['contextualExpectedValid']:
                    result = self.apply(effects, current=host['current'], context=context)
                    target = effects[0]['target']
                    expected = effects[0]['value'] if effects[0]['operation'] == 'replace' else (
                        before[target] | effects[0]['value'] if isinstance(before[target], dict)
                        else before[target] + effects[0]['value'])
                    self.assertEqual(expected, result[target])
                else:
                    with self.assertRaises(checker.CheckFailure):
                        self.apply(effects, current=host['current'], context=context)
                self.assertEqual(before, host['current'])

    def test_elicitation_content_replace_merge_and_atomic_failure(self):
        context = replace(self.context, event='user.elicitation.result',
                          elicitation_mode='form', elicitation_action='accept')
        current = {'content': {'keep': True, 'choices': ['old']}}
        self.assertEqual({'choices': ['new']}, self.apply(
            [edit({'choices': ['new']}, 'content')], current=current, context=context)['content'])
        self.assertEqual({'keep': True, 'choices': ['new', 'new']}, self.apply(
            [edit({'choices': ['new', 'new']}, 'content', 'merge')],
            current=current, context=context)['content'])
        self.assertEqual({}, self.apply([edit({}, 'content')],
                                      current=current, context=context)['content'])
        for value in [None, {'keep': None}, {'bad': [1]}, {'nested': {}}]:
            with self.subTest(value=value), self.assertRaises(checker.CheckFailure):
                self.apply([edit({'keep': False}, 'content'), edit(value, 'content')],
                           current=current, context=context)
        self.assertEqual({'content': {'keep': True, 'choices': ['old']}}, current)
        for mode, action in [('', ''), ('url', 'accept'), ('form', 'cancel'), ('form', 'decline')]:
            with self.assertRaises(checker.CheckFailure):
                self.apply([edit({}, 'content')], current=current,
                           context=replace(context, elicitation_mode=mode, elicitation_action=action))

    def test_ordinary_content_and_request_types_and_elicitation_request_bounds(self):
        for event, target in [('user.message.outbound', 'content'),
                              ('model.request.before', 'request')]:
            context = replace(self.context, event=event)
            for operation in ['replace', 'merge']:
                with self.subTest(event=event, operation=operation):
                    with self.assertRaises(checker.CheckFailure):
                        self.apply([edit({}, target, operation)],
                                   current={target: [message()]}, context=context)
                    self.assertEqual([message()], self.apply([edit([message()], target)],
                        current={target: [message()]}, context=context)[target])
        for value in [{}, [message()]]:
            with self.assertRaises(checker.CheckFailure):
                self.apply([edit(value, 'request')], current={'request': [message()]},
                           context=replace(self.context, event='user.elicitation.request'))
        # A structured current value does not itself grant specialized boundary authority.
        with self.assertRaises(checker.CheckFailure):
            self.apply([edit({}, 'content')], current={'content': {}},
                       context=replace(self.context, event='user.message.outbound'))

    def test_output_canonical_replace_and_append(self):
        context = replace(self.context, event='tool.after')
        self.assertEqual([], self.apply([edit([], 'output')], context=context)['output'])
        self.assertEqual([message(), message()], self.apply(
            [edit([message()], 'output', 'merge')], context=context)['output'])
        for value in [None, True, 12, 's', [1, 2], {}, [text()]]:
            for operation in ['replace', 'merge']:
                with self.subTest(value=value, operation=operation), self.assertRaises(checker.CheckFailure):
                    self.apply([edit(value, 'output', operation)], context=context)
        for current in [None, {}, [text()]]:
            with self.assertRaises(checker.CheckFailure):
                self.apply([edit([message()], 'output')], current={'output': current}, context=context)

    def test_attachment_preservation_removal_and_unauthorized_swap(self):
        attachment = {'id': 'a1', 'kind': 'attachment', 'mediaType': 'image/png',
                      'selection': 'body', 'body': {'ref': 'opaque'}}
        current = {'prompt': [message([text(), attachment])]}
        context = replace(self.context, authorized_attachments=(attachment,))
        replacement = [message([text('edited'), attachment])]
        self.assertEqual(replacement, self.apply([edit(replacement)], current, context)['prompt'])
        self.assertEqual([], self.apply([edit([])], current, context)['prompt'])
        self.assertEqual('opaque', current['prompt'][0]['parts'][1]['body']['ref'])
        with self.assertRaises(checker.CheckFailure):
            self.apply([edit(replacement)], current)  # A ref is not authority.
        for change in [{'body': {'ref': 'other'}}, {'text': 'bytes'}, {'mediaType': 'image/jpeg'}]:
            changed = {**attachment, **change}
            with self.subTest(change=change), self.assertRaises(checker.CheckFailure):
                self.apply([edit([message([changed])])], current,
                           replace(context, authorized_attachments=(attachment, changed)))

    def test_privacy_and_exact_capabilities(self):
        contexts = [replace(self.context, capabilities=frozenset({('prompt', 'merge')})),
                    replace(self.context, capabilities=frozenset({('response', 'replace')})),
                    replace(self.context, readable=frozenset()),
                    replace(self.context, writable=frozenset())]
        contexts += [replace(self.context, selection={'prompt': mode})
                     for mode in ('metadata', 'omit', 'gap')]
        for context in contexts:
            with self.subTest(context=context), self.assertRaises(checker.CheckFailure):
                self.apply([edit([message()])], context=context)
        for mode in ('metadata', 'omit', 'gap'):
            part = text()
            if mode == 'gap':
                part['gap'] = {'reason': 'missing'}
            else:
                part['selection'] = mode
            self.assertTrue(checker.canonical_part_errors(part))
            del part['text']
            self.assertEqual([], checker.canonical_part_errors(part))
            with self.assertRaises(checker.CheckFailure):
                self.apply([edit([message()])], current={'prompt': [message([part])]})

    def test_new_identity_cannot_replace_hidden_text(self):
        for mode in ('metadata', 'omit', 'gap'):
            hidden = text()
            del hidden['text']
            if mode == 'gap':
                hidden['gap'] = {'reason': 'unavailable'}
            else:
                hidden['selection'] = mode
            for target, previous, incoming in [
                ('prompt', [message([hidden])], [message([text(id='new')], id='new-message')]),
                ('instructions', [hidden], [text(id='new')]),
            ]:
                current = {target: previous}
                for replacement in (incoming, []):
                    with self.subTest(mode=mode, target=target, replacement=replacement):
                        with self.assertRaises(checker.CheckFailure):
                            self.apply([edit(replacement, target)], current=current)
                appended = self.apply([edit(incoming, target, 'merge')], current=current)
                self.assertEqual(previous + incoming, appended[target])

    def test_atomic_failure_and_serial_chaining(self):
        original = copy.deepcopy(self.current)
        with self.assertRaises(checker.CheckFailure):
            self.apply([edit([message([text('first')])]), edit('invalid')])
        self.assertEqual(original, self.current)
        accepted = self.apply([edit([message([text('first')])])])
        before = copy.deepcopy(accepted)
        with self.assertRaises(checker.CheckFailure):
            self.apply([edit([message()], operation='merge'), edit('bad')], current=accepted)
        self.assertEqual(before, accepted)
        chained = self.apply([edit([message()], operation='merge')], current=accepted)
        self.assertEqual('first', chained['prompt'][0]['parts'][0]['text'])
        self.assertEqual(2, len(chained['prompt']))
        serial = self.apply([edit([], operation='replace'), edit([message()], operation='merge')])
        self.assertEqual([message()], serial['prompt'])
        with self.assertRaises(checker.CheckFailure):
            self.apply([edit([]), {'type': 'deny', 'reason': 'not a modify executor'}])

    def test_content_unions_reuse_distinct_canonical_definitions(self):
        path = ROOT / 'schema/draft/content-item.schema.json'
        schema = json.loads(path.read_text())
        names = [kind + view + 'Part' for kind in ('text', 'attachment')
                 for view in ('Body', 'Gap', 'Metadata', 'Omitted')]
        self.assertEqual([{'$ref': '#/$defs/' + name} for name in names], schema['oneOf'])
        self.assertEqual(schema['oneOf'][:4], schema['$defs']['textPart']['oneOf'])
        previous_text_constraint = {'allOf': [
            {'$ref': 'content-item.schema.json'},
            {'properties': {'kind': {'const': 'text'}}}]}
        validator = checker.SubsetValidator(checker.SchemaStore(checker.Snapshot.resolve(ROOT)))
        for kind in ('text', 'attachment'):
            for view in ('body', 'gap', 'metadata', 'omit'):
                part = {'id': 'part', 'kind': kind,
                        'mediaType': 'text/plain' if kind == 'text' else 'image/png',
                        'selection': 'body' if view == 'gap' else view}
                if view == 'body':
                    part.update({'text': 'hello'} if kind == 'text' else {'body': {'ref': 'opaque'}})
                elif view == 'gap':
                    part['gap'] = {'reason': 'unavailable'}
                self.assertEqual([], validator.validate(part, schema, path))
                for candidate in (part, {**part, 'unknown': True}, {**part, 'kind': 'json'}):
                    with self.subTest(kind=kind, view=view, candidate=candidate):
                        old_valid = not validator.validate(candidate, previous_text_constraint, path)
                        new_valid = not validator.validate(candidate, schema['$defs']['textPart'], path)
                        self.assertEqual(old_valid, new_valid)

    def test_canonical_schema_rejects_noncanonical_parts(self):
        attachment = {'id': 'a1', 'kind': 'attachment', 'mediaType': 'image/png',
                      'selection': 'body', 'body': {'ref': 'opaque'}}
        variants = [{**text(), 'kind': 'json'}, {**text(), 'role': 'user'},
                    {**text(), 'parentItemId': 'm1'}, {**text(), 'unknown': True},
                    {**attachment, 'mediaType': 'text/plain'},
                    {**attachment, 'mediaType': 'application/json'},
                    {'id': 'p', 'kind': 'text', 'mediaType': 'text/plain',
                     'selection': 'metadata', 'size': -1}]
        for part in variants:
            with self.subTest(part=part):
                self.assertTrue(checker.canonical_part_errors(part))
                with self.assertRaises(checker.CheckFailure):
                    self.apply([edit([message([part])])])



class InlineElicitationTests(unittest.TestCase):
    def event(self, payload, kind='request', mode='form', action='accept'):
        return {'type': 'user.elicitation.' + kind, 'elicitation': {
            'server': 'mcp', 'mode': mode, 'action': action,
            kind: text(json.dumps(payload))}}

    def errors(self, event):
        return checker.semantic_errors(event, ROOT / 'schema/draft/interaction-event.schema.json')

    def test_parse_validate_and_compare_mode_action(self):
        request = {'message': 'Choose', 'requestedSchema': {'type': 'object', 'properties': {}}}
        self.assertEqual([], self.errors(self.event(request)))
        self.assertTrue(self.errors(self.event(request, mode='url')))
        self.assertTrue(self.errors(self.event({'message': 'missing schema'})))
        self.assertEqual([], self.errors(self.event({'action': 'accept'}, kind='result')))
        self.assertTrue(self.errors(self.event({'action': 'decline'}, kind='result')))
        self.assertTrue(self.errors(self.event({'action': 'accept', 'content': {}},
                                              kind='result', mode='url')))
        event = self.event(request)
        event['elicitation']['request']['text'] = '{invalid'
        self.assertTrue(self.errors(event))
        event['elicitation']['request']['text'] = '{"a":1,"a":2}'
        self.assertTrue(self.errors(event))

    def test_frozen_snapshot_semantics_are_not_reinterpreted(self):
        event = self.event({})
        event['elicitation']['request']['text'] = '{invalid'
        path = ROOT / 'schema/2026-01-01/interaction-event.schema.json'
        self.assertEqual([], checker.semantic_errors(event, path))

    def test_metadata_omit_gap_not_parsed(self):
        for mode in ('metadata', 'omit', 'gap'):
            event = self.event({})
            part = event['elicitation']['request']
            del part['text']
            if mode == 'gap':
                part['gap'] = {'reason': 'unavailable'}
            else:
                part['selection'] = mode
            self.assertEqual([], self.errors(event))


if __name__ == '__main__':
    unittest.main()
