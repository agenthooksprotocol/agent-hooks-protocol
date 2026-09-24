"""Offline guard tests execute all four SDK helpers, not a Python reimplementation."""
import base64, copy, json, os, subprocess, sys, unittest
from unittest.mock import patch
import elicitation_matrix
from elicitation_matrix import ROOT, SCHEMA, LANGUAGES, commands, cases, envelopes, encode, plan_cases, atomic_cases, json_equal, run_pair, reference


def guard_cases():
    fixture=cases()[0];rows=[];wanted=[]
    def add(effect,granted,accept,domain=True):
        name='guard-'+str(len(rows))
        (request,rr,rb),(result,sr,sb)=envelopes(name,fixture['request'],fixture['result'])
        target=result if effect['type']=='modify' else request
        if granted:
            target['params']['capabilities']['effects']=[effect['type']]
            if effect['type']=='modify':target['params']['capabilities']['modify']={'content':{'replace':domain,'merge':not domain}}
        rows.append({'request':request,'result':result,'uploads':[{'ref':rr['ref'],'bytes':encode(rb)},{'ref':sr['ref'],'bytes':encode(sb)}],'effect':effect})
        wanted.append(accept)
    for effect in ({'type':'return','value':fixture['result']},{'type':'deny','reason':'Policy'},{'type':'modify','target':'content','operation':'replace','value':fixture['result']['content']}):
        add(effect,True,True);add(effect,False,False)
    add({'type':'modify','target':'content','operation':'replace','value':{}},True,False,False)
    add({'type':'modify','target':'response','operation':'replace','value':{}},True,False)
    add({'type':'deny'},True,False)
    add({'type':'return'},True,False)
    add({'type':'modify','target':'content','operation':'erase','value':{}},True,False)
    # Unknown effect types and cross-boundary capabilities never grant authority.
    add({'type':'ask'},True,False)
    replay=copy.deepcopy(rows[0]);replay['request']['params']['capabilities']['effects']=[]
    replay['result']['params']['capabilities']['effects']=['return'];rows.append(replay);wanted.append(False)
    return rows,wanted


def capability_cases():
    rows=[];expected=[]
    for caps in (None,{}, {'form':{}},{'url':{}},{'form':{},'url':{}}):
        for origin in ('ahp','mcp'):
            for mode in ('form','url'):
                row={'op':'capability','origin':origin,'mode':mode}
                if caps is not None:row['capabilities']=caps
                normalized={'form':{}} if origin=='mcp' and caps=={} else (caps or {})
                rows.append(row);expected.append(normalized if mode in normalized else None)
    for caps in ({'form':True},{'url':[]},{'unexpected':{}}):
        rows.append({'op':'capability','mode':'form','capabilities':caps});expected.append(None)
    return rows,expected


class ElicitationTests(unittest.TestCase):
    def assertJSONEqual(self, actual, expected):
        self.assertTrue(json_equal(actual, expected), (actual, expected))

    def test_json_boolean_number_distinction(self):
        for actual,expected in ((True,1),(False,0),({'content':[True]},{'content':[1]})):
            self.assertFalse(json_equal(actual,expected))
            self.assertFalse(json_equal(expected,actual))
        self.assertTrue(json_equal({'a':[1,True]}, {'a':[1.0,True]}))

    def test_sender_uses_separate_upload_credential(self):
        reply = subprocess.CompletedProcess([], 0, json.dumps([{'status':401,'body':'{}'}]), '')
        with patch.object(elicitation_matrix.subprocess, 'run', return_value=reply) as send:
            elicitation_matrix.transmit_steps(['native'], 'http://unused', 'event-token',
                [elicitation_matrix.upload(reference('local', b'body'), b'body')], [401], {},
                upload_token='upload-token')
        plan = json.loads(send.call_args.kwargs['input'])
        self.assertEqual(plan['token'], 'event-token')
        self.assertEqual(plan['uploadToken'], 'upload-token')

    def test_upload_status_contract(self):
        steps,statuses,_=plan_cases([])
        self.assertEqual([step['path'] for step in steps],['/upload']*3)
        self.assertEqual(statuses,[201,201,401])
        for step in steps:
            self.assertNotIn("AHP-Subscription",step["headers"])
            self.assertNotIn("AHP-Content-Ref",step["headers"])

    def test_upload_verification_precedes_event(self):
        descriptor=reference('receiver-assigned',b'body')
        for broken in ({**descriptor,'size':True},{**descriptor,'size':5},
                       {**descriptor,'sha256':'0'*64},{**descriptor,'ref':''},
                       {**descriptor,'extra':1}):
            with self.subTest(broken=broken):
                reply=subprocess.CompletedProcess([],0,json.dumps([{'status':201,'body':json.dumps(broken)}]),'')
                steps=[elicitation_matrix.upload(reference('local',b'body'),b'body'),
                       elicitation_matrix.intercept({'body':reference('local',b'body')})]
                with patch.object(elicitation_matrix.subprocess,'run',return_value=reply) as send:
                    with self.assertRaises(AssertionError):
                        elicitation_matrix.transmit_steps(['native'],'http://unused','token',steps,[201,200],{})
                    self.assertEqual(send.call_count,1)

    def test_canonical_ref_rebinding_and_immutable_reuploads(self):
        sent=[]
        descriptors=[reference('receiver-one',b'body'),reference('receiver-two',b'body')]
        def send(command,**kwargs):
            plan=json.loads(kwargs['input']);step=plan['steps'][0];sent.append(step)
            self.assertNotIn('localRef',step)
            response={'status':201,'body':json.dumps(descriptors[len(sent)-1])} if len(sent)<3 else {'status':200,'body':'{}'}
            return subprocess.CompletedProcess(command,0,json.dumps([response]),'')
        local=reference('local',b'body')
        steps=[elicitation_matrix.upload(local,b'body'),elicitation_matrix.upload(local,b'body'),elicitation_matrix.intercept({'body':local})]
        with patch.object(elicitation_matrix.subprocess,'run',side_effect=send):
            _,refs,_=elicitation_matrix.transmit_steps(['native'],'http://unused','token',steps,[201,201,200],{})
        self.assertEqual(json.loads(base64.b64decode(sent[-1]['bytes']))['body'],descriptors[-1])
        bad={**local,'sha256':'0'*64}
        self.assertEqual(elicitation_matrix.replace_refs(bad,refs),{**bad,'ref':'receiver-two'})

    def test_receiver_cannot_reassign_ref_to_changed_bytes(self):
        steps=[elicitation_matrix.upload(reference('local',raw),raw) for raw in (b'one',b'two')]
        replies=[subprocess.CompletedProcess([],0,json.dumps([{'status':201,'body':json.dumps(reference('same-ref',raw))}]),'') for raw in (b'one',b'two')]
        with patch.object(elicitation_matrix.subprocess,'run',side_effect=replies):
            with self.assertRaisesRegex(AssertionError,'receiver ref mutated'):
                elicitation_matrix.transmit_steps(['native'],'http://unused','token',steps,[201,201],{})

    def test_noisy_receiver_startup_is_bounded(self):
        command=[sys.executable,'-c',"import os,time; os.write(2,b'x'*200000+b'TAIL'); os.close(1); time.sleep(20)"]
        with patch.object(elicitation_matrix,'commands',return_value={'python':command}):
            result=run_pair(('python','python'))
        self.assertEqual(result['status'],'failed')
        self.assertTrue(result['error'].endswith('TAIL'))
        self.assertLess(len(result['error']),1600)

    def run_helpers(self, rows):
        env=os.environ.copy();env['AHP_ELICITATION_TOKEN']='offline-guard-only';env['PYTHONPATH']=str(ROOT/'python-sdk/src')
        for language in LANGUAGES:
            result=subprocess.run(commands()[language]+['check',str(SCHEMA),'authenticated:hook'],input=json.dumps(rows),text=True,capture_output=True,env=env,timeout=45)
            self.assertEqual(result.returncode,0,language+': '+result.stderr[-2000:])
            yield language,json.loads(result.stdout)

    def test_four_sdk_per_request_guards_and_provenance(self):
        rows,wanted=guard_cases()
        for language,values in self.run_helpers(rows):
            with self.subTest(language=language):
                self.assertJSONEqual([v['accepted'] for v in values],wanted)
                for row,value in zip(rows,values):
                    if value['accepted']:
                        self.assertJSONEqual(value['summary']['provenance'],{'kind':'hook','authenticatedSource':'authenticated:hook','effect':row['effect']['type']})
                        self.assertIs(value['summary']['externalCompletion'],False)
                        self.assertJSONEqual(value['summary']['result'],cases()[0]['result'])

    def test_four_sdk_atomic_application(self):
        rows,wanted=atomic_cases()
        for language,values in self.run_helpers(rows):
            with self.subTest(language=language):
                self.assertJSONEqual([v['accepted'] for v in values],[v is not None for v in wanted])
                for row,value,result in zip(rows,values,wanted):
                    self.assertIs(value['inputUnchanged'], True, row)
                    if result is None:self.assertNotIn('summary',value)
                    else:
                        self.assertJSONEqual(value['summary']['result'],result)
                        self.assertJSONEqual(value['summary']['provenance'],{'kind':'hook','authenticatedSource':'authenticated:hook','effects':[effect['type'] for effect in row['effects']]})
                        self.assertIs(value['summary']['externalCompletion'],False)

    def test_four_sdk_independent_mode_registration(self):
        rows,wanted=capability_cases()
        for language,values in self.run_helpers(rows):
            with self.subTest(language=language):
                self.assertJSONEqual([v['accepted'] for v in values],[v is not None for v in wanted])
                for actual,expected in zip(values,wanted):
                    if expected is not None:self.assertJSONEqual(actual['summary'],expected)

    def test_four_sdk_parent_source_session_correlation(self):
        rows=[]
        for selected in ('body','metadata','omit'):
            for mutation in ('missing-parent','wrong-parent','wrong-source','wrong-session','missing-session'):
                (request,rr,rb),(result,sr,sb)=envelopes('direct-'+selected+'-'+mutation,cases()[0]['request'],cases()[0]['result'],selected)
                event=result['params']['event']
                if mutation=='missing-parent':event.pop('parentEventId')
                if mutation=='wrong-parent':event['parentEventId']='unrelated'
                if mutation=='wrong-source':event['source']='urn:unrelated:source'
                if mutation=='wrong-session':event['session']['id']='unrelated'
                if mutation=='missing-session':event.pop('session')
                rows.append({'request':request,'result':result,'uploads':[] if selected!='body' else [{'ref':rr['ref'],'bytes':encode(rb)},{'ref':sr['ref'],'bytes':encode(sb)}]})
                applied=copy.deepcopy(rows[-1]);applied['op']='apply'
                applied['result']['params']['capabilities']={'effects':['modify'],'modify':{'content':{'replace':True,'merge':False}}}
                applied['effects']=[{'type':'modify','target':'content','operation':'replace','value':{'answer':'must not apply'}}]
                rows.append(applied)
        for language,values in self.run_helpers(rows):
            with self.subTest(language=language):
                self.assertTrue(all(result['accepted'] is False for result in values))
                self.assertTrue(all('summary' not in result for result in values))

    def test_selected_views_never_upload_or_leak(self):
        for row in cases():
            if row['selection']=='body':continue
            steps,_,receipts=plan_cases([row])
            # Final three upload steps are independent transport authorization
            # negatives; the scenario itself has only its two event deliveries.
            scenario=steps[:-3]
            self.assertEqual([step['path'] for step in scenario],['/hooks/intercept','/hooks/intercept'])
            for step in scenario:
                raw=base64.b64decode(step['bytes'])
                self.assertNotIn(b'SECRET-SELECTED',raw)
                meta=json.loads(raw)['params']['event']['elicitation']
                for stage in ('request','result'):
                    self.assertNotIn('body',meta.get(stage,{}))
            self.assertTrue(all(receipt['bytes']=='' for receipt in receipts))
            self.assertNotIn('SECRET-SELECTED',json.dumps(receipts))

    def test_wire_instructions_have_no_expected_semantics(self):
        steps,_,_=plan_cases(cases())
        for step in steps:
            self.assertLessEqual(set(step),{'path','bytes','headers','localRef'})
            self.assertNotIn('expected',step)

    def test_cases_cover_required_bindings(self):
        names={c['id'] for c in cases()}
        self.assertLessEqual({'form-accept-selected-complete-bytes','url-accept-not-completion','form-decline','form-cancel','url-decline','url-cancel','request-not-preuploaded','request-digest-mismatch','bad-action','url-content-forbidden','upstream-default-annotations-preserved'},names)

if __name__=='__main__':unittest.main()
