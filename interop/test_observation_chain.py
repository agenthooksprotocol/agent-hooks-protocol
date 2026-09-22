from copy import deepcopy
import unittest
from lifecycle_matrix import HERE, load, verify


def evidence(scenarios):
    entries=[]
    for row in scenarios:
        ident=row['requests']['a']['id']
        interrupted=row['chain']['interrupt']
        for request in row['chainProof']['requests']:
            entries.append({'kind':'received','id':ident,'message':deepcopy(request)})
            if not interrupted:entries.append({'kind':'replied','id':ident})
        if interrupted:entries.append({'kind':'cancelled','id':ident,'scenario':row['id']})
        entries.append({'kind':'chain-settled','id':ident,'scenario':row['id']})
        for note in row['chainProof']['observations']:
            if row['chain'].get('holdObservers'):entries.append({'kind':'observer-blocked','id':ident})
            entries.append({'kind':'observed','eventId':ident,'subscription':note['params']['subscriptionId'],'event':deepcopy(note['params']['event']),'message':deepcopy(note)})
        if interrupted:entries.append({'kind':'replied','id':ident})
    return {'language':'python','results':[{'id':s['id'],'actual':deepcopy(s['expected'])} for s in scenarios]}, {'entries':entries}


class ChainVerifierTests(unittest.TestCase):
    def setUp(self):
        self.scenarios=[s for s in load(HERE/'lifecycle-scenarios.json')['scenarios'] if 'chain' in s]
        self.report,self.receipts=evidence(self.scenarios)
    def check(self):return verify(self.scenarios,self.report,self.receipts,'python')
    def observation(self):return next(e for e in self.receipts['entries'] if e['kind']=='observed')
    def test_authored_wire_proof(self):self.assertEqual([],self.check())
    def test_success_shaped_report_without_wire_fails(self):self.receipts['entries']=[];self.assertTrue(self.check())
    def test_missing_automatic_downgrade(self):self.receipts['entries'].remove(self.observation());self.assertTrue(self.check())
    def test_called_interceptor_second_copy(self):
        note=deepcopy(self.observation());note['message']['params']['subscriptionId']=self.scenarios[0]['expected']['called'][0];self.receipts['entries'].append(note);self.assertTrue(self.check())
    def test_downgrade_cannot_decide(self):self.observation()['message']['method']='hooks/intercept';self.assertTrue(self.check())
    def test_no_disposition(self):self.observation()['message']['params']['disposition']={'status':'denied'};self.assertTrue(self.check())
    def test_permission_filter_required(self):self.observation()['message']['params']['event']['items']=deepcopy(self.scenarios[0]['requests']['a']['params']['event']['items']);self.assertTrue(self.check())
    def test_effective_content_required(self):self.observation()['message']['params']['event']['tool']['input']={'value':'original'};self.assertTrue(self.check())
    def test_event_identity_required(self):self.observation()['message']['params']['event']['id']='new-event';self.assertTrue(self.check())
    def test_interrupt_does_not_wait_for_response(self):
        entries=self.receipts['entries'];cancel=next(e for e in entries if e['kind']=='cancelled');entries.remove(cancel);reply=next(i for i,e in enumerate(entries) if e['kind']=='replied' and e['id']==cancel['id']);entries.insert(reply+1,cancel);self.assertTrue(self.check())
    def test_skipped_chain_is_not_success(self):
        self.report['results'][0]['status']='skipped';self.assertTrue(self.check())
    def test_receipt_cannot_substitute_wire_payload(self):
        self.observation()['event']['tool']['input']={'value':'invented'};self.assertTrue(self.check())
    def test_missing_blocked_observer_proof(self):
        self.receipts['entries']=[e for e in self.receipts['entries'] if e['kind']!='observer-blocked'];self.assertTrue(self.check())
    def test_serial_interceptors(self):
        entries=self.receipts['entries'];ident='observation-chain-continue';reply=next(e for e in entries if e['kind']=='replied' and e['id']==ident);entries.remove(reply);received=[i for i,e in enumerate(entries) if e['kind']=='received' and e['id']==ident];entries.insert(received[1]+1,reply);self.assertTrue(self.check())
    def test_no_explicit_observer_has_no_called_second_copy(self):
        row=next(s for s in self.scenarios if s['id']=='observation-chain-deny-no-explicit');self.assertEqual(2,len(row['chainProof']['observations']));self.assertTrue(all(n['params']['subscriptionId'] not in row['expected']['called'] for n in row['chainProof']['observations']))
