"""Offline compatibility regression tests for the versioned MCP contract."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import check_conformance as checker
import mcp_elicitation

class McpElicitationTests(unittest.TestCase):
    def setUp(self):
        self.path = ROOT / 'schema/draft/mcp-elicitation.schema.json'
        self.schema = json.loads(self.path.read_text())
        self.validator = checker.SubsetValidator(checker.SchemaStore(checker.Snapshot.resolve(ROOT)))

    def errors(self, value, kind='request'):
        return self.validator.validate(value, {'$ref': '#/$defs/' + kind}, self.path)

    def test_pin_and_deterministic_extraction(self):
        subprocess.run([sys.executable, str(ROOT/'tools/mcp_elicitation.py')], check=True, capture_output=True)
        source = json.loads((mcp_elicitation.PIN/'schema.json').read_text())
        self.assertEqual(self.schema, mcp_elicitation.extract(source))
        ts = (mcp_elicitation.PIN/'schema.ts').read_text()
        for declaration in ['mode?: "form";', 'elicitationId: string;', 'action: "accept" | "decline" | "cancel";', 'content?: { [key: string]: string | number | boolean | string[] };']:
            self.assertIn(declaration, ts)

    def test_vendored_examples_against_extracted_contract(self):
        for kind in ('request', 'result'):
            with self.subTest(kind=kind):
                value = json.loads((mcp_elicitation.PIN / 'fixtures' / (kind + '.json')).read_text())
                self.assertEqual([], self.errors(value, kind))

    def test_all_published_form_variants(self):
        fields = [
            {'type':'string','format':'email','minLength':1,'default':'x@y.test'},
            {'type':'integer','minimum':1,'maximum':9,'default':2},
            {'type':'number','default':1.5}, {'type':'boolean','default':True},
            {'type':'string','enum':['a','b']},
            {'type':'string','oneOf':[{'const':'a','title':'A'}]},
            {'type':'string','enum':['a'],'enumNames':['A']},
            {'type':'array','items':{'type':'string','enum':['a']},'minItems':1,'maxItems':2,'default':['a']},
            {'type':'array','items':{'anyOf':[{'const':'a','title':'A'}]},'default':['a']},
        ]
        for field in fields:
            with self.subTest(field=field):
                request={'message':'Choose','requestedSchema':{'type':'object','properties':{'value':field},'required':['value']}}
                self.assertEqual([], self.errors(request))
                request['mode']='form'
                self.assertEqual([], self.errors(request))

    def test_rejects_nonpublished_forms(self):
        for field in [{'type':'object','properties':{}}, {'type':'array','items':{'type':'number'}}, {'type':'string','pattern':'.*'}, {'type':'string','enum':[1]}, {'type':'string','format':'password'}]:
            with self.subTest(field=field):
                self.assertTrue(self.errors({'message':'Choose','requestedSchema':{'type':'object','properties':{'x':field}}}))
        self.assertTrue(self.errors({'mode':'form','message':'x','requestedSchema':True}))
        self.assertTrue(self.errors({'mode':'form','requestedSchema':{'type':'object','properties':{}}}))

    def test_url_fields_and_modes(self):
        value={'mode':'url','message':'Connect','url':'https://example.test/connect','elicitationId':'opaque'}
        self.assertEqual([], self.errors(value))
        for field in value:
            missing=dict(value);del missing[field]
            self.assertTrue(self.errors(missing), field)
        for mode in ['browser','FORM',None]:
            self.assertTrue(self.errors(dict(value,mode=mode)))

    def test_structured_results_not_items(self):
        for action in ['accept','decline','cancel']:
            self.assertEqual([], self.errors({'action':action},'result'))
        self.assertEqual([],self.errors({'action':'accept','content':{'text':'yes','number':1.5,'flag':False,'choices':['a']}},'result'))
        for value in [{'action':'submitted'},{'action':'accept','content':[]},{'action':'accept','content':{'nested':{}}},{'action':'accept','content':{'bad':[1]}}]:
            self.assertTrue(self.errors(value,'result'))

    def test_selection_aware_event_views(self):
        path=ROOT/'schema/draft/observe-notification.schema.json'
        for kind in ['request', 'result']:
            fixture=json.loads((ROOT/f'fixtures/draft/http/catalogue-user.elicitation.{kind}.valid.json').read_text())
            elicitation=fixture['params']['event']['elicitation']
            descriptor=elicitation[kind]
            self.assertEqual([], self.validator.validate(fixture, {'$ref':path.name}, path))
            descriptor.pop('body')
            descriptor['selection']='metadata'
            self.assertEqual([], self.validator.validate(fixture, {'$ref':path.name}, path))
            descriptor['selection']='omit'
            self.assertEqual([], self.validator.validate(fixture, {'$ref':path.name}, path))
            descriptor['selection']='body'
            self.assertTrue(self.validator.validate(fixture, {'$ref':path.name}, path))
            descriptor['gap']={'reason':'permission_withheld'}
            self.assertEqual([], self.validator.validate(fixture, {'$ref':path.name}, path))
            del elicitation[kind]
            self.assertEqual([], self.validator.validate(fixture, {'$ref':path.name}, path))
            elicitation[kind]={'message':'inline MCP is forbidden'}
            self.assertTrue(self.validator.validate(fixture, {'$ref':path.name}, path))

    def test_upstream_extension_compatibility_boundary(self):
        value={'message':'Choose', '_meta':{'vendor.custom':'kept'}, 'vendor.extension':{'arbitrary':True}, 'requestedSchema':{'type':'object','properties':{}}}
        self.assertEqual([], self.errors(value))
        value['requestedSchema']['vendor.extension']='not in the published form vocabulary'
        self.assertTrue(self.errors(value))
        self.assertEqual([], self.errors({'action':'accept', 'vendor.extension':{'arbitrary':True}}, 'result'))

    def test_compaction_modify_targets_match_boundary(self):
        path=ROOT/'schema/draft/capabilities.schema.json'
        for phase,target,wrong in [('before','instructions','summary'),('after','summary','instructions')]:
            schema={'$ref':'#/$defs/context.compact.'+phase}
            caps={'effects':['modify'], 'modify':{target:{'replace':True,'merge':False}}}
            self.assertEqual([], self.validator.validate(caps,schema,path))
            caps['modify']={wrong:{'replace':True,'merge':False}}
            self.assertTrue(self.validator.validate(caps,schema,path))
        self.assertEqual([],self.validator.validate({'effects':['return']},{'$ref':'#/$defs/context.compact.before'},path))
        self.assertTrue(self.validator.validate({'effects':['return']},{'$ref':'#/$defs/context.compact.after'},path))

    def test_exact_uploaded_body_fixtures(self):
        for kind in ['request', 'result']:
            raw=(mcp_elicitation.PIN/'fixtures'/f'{kind}.json').read_bytes()
            payload=json.loads(raw)
            event=json.loads((ROOT/f'fixtures/draft/http/catalogue-user.elicitation.{kind}.valid.json').read_text())['params']['event']
            reference=event['elicitation'][kind]['body']
            self.assertEqual(len(raw), reference['size'])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), reference['sha256'])
            self.assertEqual([], self.errors(payload, kind))
            if kind == 'request':
                self.assertNotIn('mode', payload)  # Normalized metadata does not mutate MCP bytes.
                self.assertEqual(payload.get('mode', 'form'), event['elicitation']['mode'])
            else:
                self.assertEqual(payload['action'], event['elicitation']['action'])

    def test_schema_keyword_property_names_are_not_schema_nodes(self):
        schema={'properties': {'required': {'type': 'array'}, 'oneOf': {'type': 'string'}, 'enum': {'type': 'object'}}}
        nodes=list(checker.iter_schema_nodes(schema))
        self.assertEqual(4, len(nodes))
        self.assertNotIn(schema['properties'], nodes)

    def test_envelope_references_and_no_disposition(self):
        for kind in ['request','result']:
            fixture=json.loads((ROOT/f'fixtures/draft/http/catalogue-user.elicitation.{kind}.valid.json').read_text())
            path=ROOT/'schema/draft/observe-notification.schema.json'
            self.assertEqual([],self.validator.validate(fixture,{'$ref':'observe-notification.schema.json'},path))
            self.assertNotIn('disposition', path.read_text())
        self.assertFalse((ROOT/'schema/draft/observation-disposition.schema.json').exists())

if __name__ == '__main__':
    unittest.main()
