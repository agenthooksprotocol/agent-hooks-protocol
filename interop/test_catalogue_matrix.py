"""Fail-closed shared verifier tests; synthetic evidence is never runtime coverage."""
from copy import deepcopy
from unittest import TestCase, main
from catalogue_matrix import verify
from generate_catalogue_scenarios import build, validate, EXECUTION, LINEAGE
from generate_scenarios import CAPS


def evidence(scenarios):
    manifest={'events':[{'event':kind,'modes':['observe']} for kind in EXECUTION+LINEAGE],
              'gaps':[{'path':'events.hook.failure','reason':'Not supported by the synthetic test host'}],
              'transports':['http','stdio'],'authentication':['bearer','oauth','mtls','workload'],
              'toolPaths':['native'],'contentCategories':[], 'limits':{},
              'managedPolicy':{'scopes':['user','project'],'disableable':True},
              'correlationIdentityFields':['event.id','event.source','call.id']}
    manifest['events'][0].update(modes=['observe','intercept'],capabilities=deepcopy(CAPS))
    request={'jsonrpc':'2.0','id':'discovery','method':'hooks/capabilities','params':{'protocolVersion':'draft'}}
    response={'jsonrpc':'2.0','id':'discovery','result':{'protocolVersion':'draft','manifest':manifest}}
    entries=[{'kind':'discovery','request':request,'response':response}]
    results=[]
    for scenario in scenarios:
        results.append({'id':scenario['id'],'status':'passed','actual':{k:deepcopy(v) for k,v in scenario['expected'].items() if k!='deliveries'}})
        for message,delivery in zip(scenario['expected']['sent'],scenario['expected']['deliveries']):
            message=deepcopy(message);params=message['params'];event=params['event']
            if delivery['accepted']:entries.append({'kind':'observed','eventId':event['id'],'subscription':params['subscriptionId'],'event':event,'message':message})
            else:entries.append({'kind':'rejected','eventId':event['id'],'message':message,'errorKind':delivery['errorKind']})
    return {'language':'python','discovery':deepcopy(response),'results':results},{'entries':entries}


class CatalogueVerifierTests(TestCase):
    def setUp(self):
        self.scenarios=validate(build())['scenarios'];self.report,self.receipts=evidence(self.scenarios)
    def check(self):return verify(self.scenarios,self.report,self.receipts,'python')
    def test_unrelated_catalogue_items_do_not_reuse_identity_across_roles(self):
        roles={}
        def walk(value,source):
            if isinstance(value,dict):
                if all(k in value for k in ('id','kind','mediaType')):
                    key=(source,value['id'])
                    self.assertTrue(key not in roles or roles[key]==value.get('role'))
                    roles[key]=value.get('role')
                for child in value.values():walk(child,source)
            elif isinstance(value,list):
                for child in value:walk(child,source)
        for scenario in self.scenarios:
            for step in scenario['steps']:
                if step['op']=='notify':
                    event=step['message']['params']['event'];walk(event,event['source'])

    def test_valid_evidence(self):self.assertEqual(self.check(),[])
    def test_no_receiver_evidence_cannot_pass(self):self.receipts={'entries':[]};self.assertTrue(self.check())
    def test_missing_scenario(self):self.report['results'].pop();self.assertTrue(self.check())
    def test_duplicate_scenario(self):self.report['results'].append(deepcopy(self.report['results'][0]));self.assertTrue(self.check())
    def test_nonzero_exit(self):self.assertTrue(verify(self.scenarios,self.report,self.receipts,'python',1))
    def test_registration_unsupported_is_not_pass(self):
        row=next(r for r in self.report['results'] if r['id']=='registration-unenforceable-ask');row['actual']['registrations']=[{'accepted':True}];self.assertTrue(self.check())
    def test_receipt_rejection_cannot_be_replaced_by_client_claim(self):
        self.receipts['entries']=[e for e in self.receipts['entries'] if e['kind']!='rejected'];self.assertTrue(self.check())
    def test_forged_discovery_mismatch(self):self.report['discovery']['result']['manifest']['events']=[];self.assertTrue(self.check())
    def test_missing_catalogue_advertisement(self):
        for value in (self.report['discovery'],self.receipts['entries'][0]['response']):value['result']['manifest']['events'].pop()
        self.assertTrue(self.check())
    def test_source_identity_preserved(self):
        self.receipts['entries'][1]['message']['params']['event']['source']='urn:changed';self.assertTrue(self.check())
    def test_report_boolean_not_number(self):
        row=next(r for r in self.report['results'] if r['actual']['registrations']);row['actual']['registrations'][0]['accepted']=1;self.assertTrue(self.check())
    def test_semantic_control_oracle_rejected(self):self.receipts['entries'].append({'kind':'view','accepted':True});self.assertTrue(self.check())


if __name__=='__main__':main()
