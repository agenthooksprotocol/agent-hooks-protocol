from copy import deepcopy
import unittest
from lifecycle_matrix import HERE, load, verify


def evidence(scenarios):
    entries=[]
    for row in scenarios:
        ident=row['requests']['a']['id']
        interrupted=row['chain']['interrupt']
        for index, template in enumerate(row['chainProof']['requests']):
            request=deepcopy(template)
            if row['id']=='observation-chain-continue' and index>0:
                request['params']['state']['permission']='allow'
            entries.append({'kind':'received','id':ident,'message':deepcopy(request)})
            if not interrupted:entries.append({'kind':'replied','id':ident})
        if interrupted:entries.append({'kind':'cancelled','id':ident,'scenario':row['id']})
        entries.append({'kind':'chain-settled','id':ident,'scenario':row['id']})
        for note in row['chainProof']['observations']:
            if row['chain'].get('holdObservers'):entries.append({'kind':'observer-blocked','id':ident})
            entries.append({'kind':'observed','eventId':note['params']['event']['id'],'event':deepcopy(note['params']['event']),'message':deepcopy(note)})
        if interrupted:entries.append({'kind':'replied','id':ident})
    return {'language':'python','results':[{'id':s['id'],'actual':deepcopy(s['expected'])} for s in scenarios]}, {'entries':entries}


class ChainVerifierTests(unittest.TestCase):
    def setUp(self):
        self.scenarios=[s for s in load(HERE/'lifecycle-scenarios.json')['scenarios'] if 'chain' in s]
        self.report,self.receipts=evidence(self.scenarios)
    def check(self):return verify(self.scenarios,self.report,self.receipts,'python')
    def observation(self):return next(e for e in self.receipts['entries'] if e['kind']=='observed')
    def test_request_and_event_ids_are_independent(self):
        for row in self.scenarios:
            row['requests']['a']['id']='rpc:'+row['id']
            for request in row['chainProof']['requests']:
                request['id']=row['requests']['a']['id']
        self.report,self.receipts=evidence(self.scenarios)
        self.assertEqual([],self.check())
    def test_authored_wire_proof(self):self.assertEqual([],self.check())
    def test_success_shaped_report_without_wire_fails(self):self.receipts['entries']=[];self.assertTrue(self.check())
    def test_missing_automatic_downgrade(self):self.receipts['entries'].remove(self.observation());self.assertTrue(self.check())
    def test_called_interceptor_second_copy(self):
        note=deepcopy(self.observation());self.receipts['entries'].append(note);self.assertTrue(self.check())
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
        row=next(s for s in self.scenarios if s['id']=='observation-chain-deny-no-explicit');self.assertEqual(2,len(row['chainProof']['observations']));self.assertTrue(set(row['expected']['observations']).isdisjoint(row['expected']['called']))

    def explicit_omit(self):
        for receipt in self.receipts['entries']:
            message=receipt.get('message', {})
            event=message.get('params', {}).get('event', {})
            if event.get('items') == []:
                event['items']=[{'id':event['id']+':item','kind':'text',
                                 'mediaType':'text/plain','selection':'omit'}]
                if receipt['kind']=='observed':receipt['event']=deepcopy(event)

    def test_explicit_omit_descriptors(self):
        self.explicit_omit()
        self.assertEqual([],self.check())

    def test_explicit_omit_does_not_hide_extra_content(self):
        self.explicit_omit()
        receipt=next(e for e in self.receipts['entries'] if e.get('message',{}).get('params',{}).get('event',{}).get('items',[{}])[0].get('selection')=='omit')
        receipt['message']['params']['event']['items'][0]['body']={'text':'secret'}
        self.assertTrue(self.check())

    def test_explicit_omit_preserves_item_identity(self):
        self.explicit_omit()
        receipt=next(e for e in self.receipts['entries'] if e.get('message',{}).get('params',{}).get('event',{}).get('items',[{}])[0].get('selection')=='omit')
        receipt['message']['params']['event']['items'][0]['id']='invented'
        self.assertTrue(self.check())

    def test_later_requests_require_accepted_permission(self):
        requests=[e['message'] for e in self.receipts['entries'] if e['kind']=='received' and e['id']=='observation-chain-continue']
        self.assertEqual(['none','allow','allow'],[r['params']['state']['permission'] for r in requests])
        requests[1]['params']['state']['permission']='none'
        self.assertTrue(self.check())

    def test_external_marker_may_follow_observers(self):
        entries=self.receipts['entries']
        for row in self.scenarios:
            marker=next(e for e in entries if e['kind']=='chain-settled' and e['id']==row['id'])
            entries.remove(marker)
            last=max(i for i,e in enumerate(entries) if e.get('eventId')==row['id'])
            entries.insert(last+1,marker)
        self.assertEqual([],self.check())

    def test_observer_cannot_precede_final_reply(self):
        entries=self.receipts['entries'];note=self.observation();entries.remove(note)
        reply=next(i for i,e in enumerate(entries) if e['kind']=='replied')
        entries.insert(reply,note)
        self.assertTrue(self.check())

    def test_interrupted_observer_cannot_precede_cancellation(self):
        entries=self.receipts['entries']
        note=next(e for e in entries if e['kind']=='observed' and e['eventId']=='observation-chain-interrupt')
        entries.remove(note)
        cancel=next(i for i,e in enumerate(entries) if e['kind']=='cancelled')
        entries.insert(cancel,note)
        self.assertTrue(self.check())

    def test_interrupted_observer_may_arrive_after_discarded_reply(self):
        entries=self.receipts['entries']
        deliveries=[e for e in entries if e['kind'] in ('observed','observer-blocked') and e.get('eventId',e.get('id'))=='observation-chain-interrupt']
        for receipt in deliveries:
            entries.remove(receipt)
        entries.extend(deliveries)
        self.assertEqual([],self.check())

    def test_rejected_response_does_not_change_later_permission(self):
        requests=[e['message'] for e in self.receipts['entries'] if e['kind']=='received' and e['id']=='observation-chain-fail-open']
        requests[1]['params']['state']['permission']='allow'
        self.assertTrue(self.check())

    def test_missing_external_marker_is_not_success(self):
        marker=next(e for e in self.receipts['entries'] if e['kind']=='chain-settled')
        self.receipts['entries'].remove(marker)
        self.assertTrue(self.check())

    def test_verification_does_not_rewrite_evidence(self):
        self.explicit_omit()
        before=deepcopy((self.scenarios,self.report,self.receipts))
        self.assertEqual([],self.check())
        self.assertEqual(before,(self.scenarios,self.report,self.receipts))
