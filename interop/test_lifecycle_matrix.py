"""Negative verifier tests: a success-shaped report alone cannot pass."""
from copy import deepcopy
import unittest
from lifecycle_matrix import HERE, load, verify

def evidence(scenarios):
    entries=[]
    for s in scenarios:
        slots={}; counters={}; uploaded=iter(s['expected']['uploadStatuses']); settled=set(); staged=set(); cancelled=set()
        for st in s['steps']:
            op=st['op']; id=s['requests'][st['key']]['id'] if 'key' in st else None
            if op=='send':
                slots[st['slot']]=id; entries.append({'kind':'received','id':id,'message':deepcopy(s['requests'][st['key']])})
            elif op=='receive':
                received_id=slots[st['slot']]; entries.append({'kind':'replied','id':received_id})
                if received_id not in settled and received_id not in cancelled and received_id not in staged:
                    staged.add(received_id);entries.append({'kind':'acquired','id':received_id,'scenario':s['id']})
            elif op=='cancel':
                cancelled.add(id); entries.append({'kind':'cancelled','id':id,'scenario':s['id']})
            elif op=='accept' and id in s['expected']['published'] and id not in settled:
                # Only publish after this ID's reply, not merely any reply.
                replied=[x for x in entries if x['kind']=='replied' and x['id']==id]
                if replied:
                    entries.append({'kind':'accepted','id':id,'scenario':s['id']}); settled.add(id)
            elif op=='emit':
                entries.extend([{'kind':'emitted','id':st['response']['id']},{'kind':'discarded','id':st['response']['id'],'scenario':s['id']}])
            elif op=='upload':entries.append({'kind':'upload',**{k:st[k] for k in ('ref','size','sha256')},'status':next(uploaded)})
            elif op=='observe':
                event=deepcopy(s['requests'][st['key']]['params']['event']); event['tool']['input']=deepcopy(s['expected']['states'].get(id,{}).get('input',event['tool']['input']))
                if 'items' in st:event['items']=deepcopy(st['items'])
                message={'jsonrpc':'2.0','method':'hooks/observe','params':{'protocolVersion':'draft','event':event}}
                entries.append({'kind':'observed','eventId':event['id'],'event':event,'message':message})
    report={'language':'python','results':[{'id':s['id'],'actual':deepcopy(s['expected'])} for s in scenarios]}
    return report,{'entries':entries}

class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.scenarios=[s for s in load(HERE/'lifecycle-scenarios.json')['scenarios'] if 'chain' not in s]; self.report,self.receipts=evidence(self.scenarios)
    def check(self):return verify(self.scenarios,self.report,self.receipts,'python')
    def test_ask_fixture_valid_but_reason_extension_rejected(self):
        from observation_wire import ObservationValidator
        validator = ObservationValidator()
        scenario = next(s for s in self.scenarios if s['id'] == 'ask-boundary-observed')
        response = deepcopy(scenario['responses']['a'])
        self.assertEqual(validator.schema_errors(response, 'intercept-response'), [])
        response['result']['effects'][0]['reason'] = 'confirm'
        self.assertTrue(validator.schema_errors(response, 'intercept-response'))

    def test_unauthorized_upload_uses_explicit_credentials(self):
        for name in ('immutable-upload-and-subscription-views', 'upload-integrity-rejection'):
            scenario = next(s for s in self.scenarios if s['id'] == name)
            uploads = [step for step in scenario['steps'] if step['op'] == 'upload']
            denied = uploads[-1]
            self.assertEqual(scenario['expected']['uploadStatuses'][-1], 401)
            self.assertEqual(denied['upload']['auth'], {
                'type': 'bearer', 'tokenEnv': 'AHP_INTEROP_UNAUTHORIZED_UPLOAD_TOKEN'})

    def test_valid_control_proof(self):self.assertEqual(self.check(),[])
    def test_failed_stdio_delivery_cannot_pass(self):
        # A sender report cannot replace the absent receiver delivery record.
        self.receipts['entries'] = [e for e in self.receipts['entries'] if e['kind'] != 'observed']
        self.assertTrue(self.check())

    def test_missing_receipts(self):self.receipts={}; self.assertTrue(self.check())
    def test_missing_result(self):self.report['results'].pop(); self.assertTrue(self.check())
    def test_duplicate_result(self):self.report['results'].append(deepcopy(self.report['results'][0])); self.assertTrue(self.check())
    def test_wrong_language(self):self.report['language']='oracle'; self.assertTrue(self.check())
    def test_nonzero_exit(self):self.assertTrue(verify(self.scenarios,self.report,self.receipts,'python',1))
    def test_stale_acceptance(self):self.report['results'][0]['actual']['published']=['cancel-before-reply:a']; self.assertTrue(self.check())
    def test_no_receiver_execution(self):self.receipts['entries']=[]; self.assertTrue(self.check())
    def test_duplicate_receiver_receipt(self):self.receipts['entries'].append(deepcopy(self.receipts['entries'][0])); self.assertTrue(self.check())
    def test_cancel_race_false_pass(self):
        es=self.receipts['entries']; a=next(i for i,e in enumerate(es) if e['kind']=='cancelled'); b=next(i for i,e in enumerate(es) if e['kind']=='replied'); es[a],es[b]=es[b],es[a]; self.assertTrue(self.check())
    def test_observation_identity_changed(self):next(e for e in self.receipts['entries'] if e['kind']=='observed')['eventId']='new-event'; self.assertTrue(self.check())
    def test_observation_unsettled_input(self):next(e for e in self.receipts['entries'] if e['kind']=='observed')['event']['tool']['input']={'value':'original'}; self.assertTrue(self.check())
    def test_upload_before_dispatch_required(self):
        es=self.receipts['entries']; uploads=[e for e in es if e['kind']=='upload']; self.receipts['entries']=[e for e in es if e['kind']!='upload']+uploads; self.assertTrue(self.check())
    def test_content_hash_mismatch(self):next(e for e in self.receipts['entries'] if e['kind']=='upload')['sha256']='0'*64; self.assertTrue(self.check())
    def test_content_permission_bypass(self):next(e for e in self.receipts['entries'] if e['kind']=='upload' and e['status']==401)['status']=201; self.assertTrue(self.check())
    def test_receiver_assigned_upload_refs_are_used_on_wire(self):
        refs={e['ref']:'receiver:'+e['ref'] for e in self.receipts['entries'] if e['kind']=='upload' and e['status']==201}
        def rewrite(value):
            if isinstance(value,list):
                for v in value:rewrite(v)
            if isinstance(value,dict):
                if value.get('ref') in refs:value['ref']=refs[value['ref']]
                for v in value.values():rewrite(v)
        rewrite(self.receipts)
        self.assertEqual([],self.check())
    def test_missing_receiver_assigned_reference_fails(self):
        next(e for e in self.receipts['entries'] if e['kind']=='upload' and e['status']==201).pop('ref')
        self.assertTrue(self.check())
    def test_changed_bytes_cannot_reuse_receiver_reference(self):
        uploads=[e for e in self.receipts['entries'] if e['kind']=='upload' and e['status']==201]
        uploads[2]['ref']=uploads[0]['ref']
        self.assertTrue(self.check())
    def test_success_without_created_receipt(self):next(e for e in self.receipts['entries'] if e['kind']=='upload' and e['status']==201)['status']=204; self.assertTrue(self.check())
    def test_boolean_not_integer(self):self.report['results'][2]['actual']['states']['late-old-while-next-pending:b']['executed']=1; self.assertTrue(self.check())
    def test_unsupported_is_not_pass(self):self.report['results'][0]['status']='unsupported'; self.assertTrue(self.check())
    def test_acquisition_after_cancellation_is_not_after_reply_race(self):
        es=self.receipts['entries']; ident='cancel-after-reply-before-acceptance:a'; a=next(i for i,e in enumerate(es) if e['kind']=='acquired' and e['id']==ident); c=next(i for i,e in enumerate(es) if e['kind']=='cancelled' and e['id']==ident); es[a],es[c]=es[c],es[a]; self.assertTrue(self.check())
    def test_missing_acquired(self):self.receipts['entries']=[e for e in self.receipts['entries'] if e['kind']!='acquired']; self.assertTrue(self.check())
    def test_missing_wire_message(self):next(e for e in self.receipts['entries'] if e['kind']=='observed').pop('message'); self.assertTrue(self.check())
    def test_swapped_payload_ownership(self):
        r=next(r for r in self.report['results'] if r['id']=='two-staged-reverse-acceptance'); states=r['actual']['states']; a,b=states.keys(); states[a],states[b]=states[b],states[a]; self.assertTrue(self.check())
    def test_duplicate_replaces_private_stage(self):
        r=next(r for r in self.report['results'] if r['id']=='first-staged-response-wins'); next(iter(r['actual']['states'].values()))['input']={'value':'MUST-NOT-REPLACE'}; self.assertTrue(self.check())
    def test_missing_stray_reader_rendezvous(self):self.receipts['entries']=[e for e in self.receipts['entries'] if e['kind']!='discarded']; self.assertTrue(self.check())
    def test_interrupted_wire_requires_prior_cancellation(self):
        es=self.receipts['entries']; ident='interrupt-after-publication:a'; cancelled=next(e for e in es if e['kind']=='cancelled' and e['id']==ident); es.remove(cancelled); observed=next(i for i,e in enumerate(es) if e['kind']=='observed' and e['eventId']==ident); es.insert(observed+1,cancelled); self.assertTrue(self.check())
    def test_legacy_control_oracle_rejected(self):
        self.receipts['entries'].append({'kind':'view','decision':'allow'}); self.assertTrue(self.check())
    def test_obsolete_wire_disposition(self):
        next(e for e in self.receipts['entries'] if e['kind']=='observed')['message']['params'].__setitem__('disposition', {'status':'normal'}); self.assertTrue(self.check())
    def test_observation_rpc_id_forbidden(self):
        next(e for e in self.receipts['entries'] if e['kind']=='observed')['message']['id']='invalid'; self.assertTrue(self.check())
    def test_changed_wire_source(self):
        next(e for e in self.receipts['entries'] if e['kind']=='observed')['message']['params']['event']['source']='urn:other'; self.assertTrue(self.check())
    def test_request_and_boundary_ids_are_independent(self):
        for scenario in self.scenarios:
            for request in scenario['requests'].values():
                request['params']['event']['id']='boundary:'+request['id']
            for observation in scenario['expected']['observations']:
                observation['eventId']='boundary:'+observation['eventId']
        self.report,self.receipts=evidence(self.scenarios)
        self.assertEqual(self.check(),[])
    def test_missing_received_wire_message(self):
        next(e for e in self.receipts['entries'] if e['kind']=='received').pop('message'); self.assertTrue(self.check())
    def test_intercept_content_upload_must_precede_wire_receipt(self):
        entries=self.receipts['entries']; receipt=next(e for e in entries if e['kind']=='upload' and e['ref']=='intercept-body')
        entries.remove(receipt); entries.append(receipt); self.assertTrue(self.check())
    def test_denial_plus_stop_has_no_silent_precedence(self):
        scenario=next(s for s in self.scenarios if s['id']=='denied-boundary-observed')
        scenario['expected']['states'][scenario['requests']['a']['id']]['flow']='stop'
        self.report,self.receipts=evidence(self.scenarios)
        self.assertEqual([],self.check())
    def test_interruption_overrides_combined_denial_and_stop(self):
        scenario=next(s for s in self.scenarios if s['id']=='interrupt-after-publication')
        scenario['expected']['states'][scenario['requests']['a']['id']].update(decision='deny',flow='stop')
        self.report,self.receipts=evidence(self.scenarios)
        self.assertEqual(self.check(),[])
    def test_unknown_receipt(self):self.receipts['entries'].append({'kind':'echo'}); self.assertTrue(self.check())

class CleanupTests(unittest.TestCase):
    def test_registered_isolated_child_is_killed_and_reaped(self):
        import json, os, subprocess, sys, tempfile
        from pathlib import Path
        from lifecycle_matrix import cleanup
        with tempfile.TemporaryDirectory() as d:
            childfile=Path(d)/'child.json'
            code="""import json,signal,subprocess,sys,threading
child=subprocess.Popen([sys.executable,'-c','import threading; threading.Event().wait()'],start_new_session=True)
def end(*args):
 child.wait(timeout=5)
 raise SystemExit(0)
signal.signal(signal.SIGTERM,end)
open(sys.argv[1],'w').write(json.dumps({'pid':child.pid}))
print(child.pid,flush=True)
threading.Event().wait()
"""
            p=subprocess.Popen([sys.executable,'-c',code,str(childfile)],stdout=subprocess.PIPE,text=True,start_new_session=True)
            try:
                pid=int(p.stdout.readline()) # real startup rendezvous, not sleep
                cleanup([p],childfile,Path(d)/'absent-ready.json')
                self.assertIsNotNone(p.poll())
                with self.assertRaises(ProcessLookupError):os.kill(pid,0)
            finally:
                if p.poll() is None:cleanup([p],childfile,Path(d)/'absent-ready.json')
                p.stdout.close()

    def test_malformed_client_exit_still_cleans_registered_child(self):
        # Failure-shaped exit of the controller must not bypass the same cleanup.
        import os, subprocess, sys, tempfile
        from pathlib import Path
        from lifecycle_matrix import cleanup
        with tempfile.TemporaryDirectory() as d:
            childfile=Path(d)/'child.json'
            code="""import json,signal,subprocess,sys,threading
child=subprocess.Popen([sys.executable,'-c','import threading; threading.Event().wait()'],start_new_session=True)
def end(*args):
 child.wait(timeout=5)
 raise SystemExit(17)
signal.signal(signal.SIGTERM,end)
open(sys.argv[1],'w').write(json.dumps({'pid':child.pid}))
print('MALFORMED REPORT',flush=True)
threading.Event().wait()
"""
            p=subprocess.Popen([sys.executable,'-c',code,str(childfile)],stdout=subprocess.PIPE,text=True,start_new_session=True)
            try:
                self.assertEqual(p.stdout.readline().strip(),'MALFORMED REPORT')
                pid=load(childfile)['pid']
                cleanup([p],childfile,Path(d)/'absent-ready.json')
                self.assertEqual(p.returncode,17)
                with self.assertRaises(ProcessLookupError):os.kill(pid,0)
            finally:
                if p.poll() is None:cleanup([p],childfile,Path(d)/'absent-ready.json')
                p.stdout.close()

    def test_actual_runner_timeout_and_malformed_report_cleanup(self):
        import os, subprocess, sys, tempfile
        from pathlib import Path
        from lifecycle_matrix import run_group, stop
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory); audit=d/'audit.json'; client=d/'client.py'; server=d/'server.py'
            server.write_text('import threading; threading.Event().wait()')
            for mode in ('timeout','malformed'):
                with self.subTest(mode=mode):
                    external=None
                    try:
                        if mode=='malformed':
                            # Safe external reaper owns the live registered orphan;
                            # fake client MUST NOT kill it before malformed exit.
                            external=subprocess.Popen([sys.executable,str(server)],start_new_session=True)
                            code="""import json,sys
config=json.load(open(sys.argv[sys.argv.index('--config')+1]))
record={'pid':PID}
open(config['childPidFile'],'w').write(json.dumps(record))
open(AUDIT,'w').write(json.dumps(record))
open(config['reportFile'],'w').write('{not valid JSON')
""".replace('PID',str(external.pid))
                        else:
                            code="""import json,os,signal,subprocess,sys,threading
config=json.load(open(sys.argv[sys.argv.index('--config')+1]))
child=subprocess.Popen(config['serverCommand']+['--config',config['serverConfig']],start_new_session=True)
record={'pid':child.pid}
open(config['childPidFile'],'w').write(json.dumps(record))
open(AUDIT,'w').write(json.dumps(record))
def end(*args):
 child.wait(timeout=5)
 raise SystemExit(17)
signal.signal(signal.SIGTERM,end)
threading.Event().wait()
"""
                        client.write_text(code.replace('AUDIT',repr(str(audit))))
                        adapters={'python':(d,{'lifecycleClient':[sys.executable,str(client)],'lifecycleServer':[sys.executable,str(server)]})}
                        result=run_group('python','python','stdio',adapters,HERE/'lifecycle-scenarios.json',1)
                        self.assertFalse(result['passed']); self.assertTrue(result['errors'])
                        if external is not None:external.wait(timeout=3)
                        with self.assertRaises(ProcessLookupError):os.kill(load(audit)['pid'],0)
                    finally:
                        if external is not None:stop(external)

    def test_bad_cleanup_metadata_never_bypasses_known_processes(self):
        import subprocess, sys, tempfile
        from pathlib import Path
        from lifecycle_matrix import cleanup, stop
        for name,contents in [('ready.json','{malformed'),('child.json','{malformed'),('child.json','{}'),('child.json','{"pid":true}')]:
            with self.subTest(name=name,contents=contents), tempfile.TemporaryDirectory() as directory:
                d=Path(directory);(d/name).write_text(contents)
                p=subprocess.Popen([sys.executable,'-c','import threading; threading.Event().wait()'],start_new_session=True)
                try:
                    errors=cleanup([p],d/'child.json',d/'ready.json')
                    self.assertTrue(errors);self.assertIsNotNone(p.poll())
                finally:stop(p)

    def test_failed_process_discovery_still_reaps_known_processes(self):
        import subprocess, sys, tempfile
        from pathlib import Path
        from unittest.mock import patch
        from lifecycle_matrix import cleanup, stop
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory);p=subprocess.Popen([sys.executable,'-c','import threading; threading.Event().wait()'],start_new_session=True)
            try:
                with patch('lifecycle_matrix.subprocess.run',side_effect=OSError('injected ps failure')):
                    errors=cleanup([p],d/'child.json',d/'ready.json')
                self.assertTrue(errors);self.assertIsNotNone(p.poll())
            finally:stop(p)

    def test_stdio_success_report_with_failed_delivery_is_rejected(self):
        import sys,tempfile
        from pathlib import Path
        from lifecycle_matrix import run_group
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory); client=d/'client.py'
            client.write_text("""import json,sys
c=json.load(open(sys.argv[sys.argv.index('--config')+1]))
scenarios=json.load(open(c['scenarioFile']))['scenarios']
# Simulate a failed transport: no receiver has accepted a single frame.
report={'language':'python','results':[{'id':s['id'],'actual':s['expected']} for s in scenarios], 'receipts':{'entries':[]}}
open(c['reportFile'],'w').write(json.dumps(report))
""")
            adapters={'python':(d,{'lifecycleClient':[sys.executable,str(client)],'lifecycleServer':[sys.executable]})}
            result=run_group('python','python','stdio',adapters,HERE/'lifecycle-scenarios.json',3)
            self.assertFalse(result['passed'])
            self.assertTrue(any('mismatch' in e or 'multiplicity' in e for e in result['errors']))

    def test_cleanup_errors_invalidate_structured_group_result(self):
        import sys,tempfile
        from pathlib import Path
        from unittest.mock import patch
        from lifecycle_matrix import run_group
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory); client=d/'client.py'
            client.write_text("""import json,sys
c=json.load(open(sys.argv[sys.argv.index('--config')+1]))
open(c['childPidFile'],'w').write('{malformed')
open(c['reportFile'],'w').write(json.dumps({'language':'python','results':[],'receipts':{'entries':[]}}))
""")
            adapters={'python':(d,{'lifecycleClient':[sys.executable,str(client)],'lifecycleServer':[sys.executable]})}
            with patch('lifecycle_matrix.verify',return_value=[]):
                result=run_group('python','python','stdio',adapters,HERE/'lifecycle-scenarios.json',3)
            self.assertFalse(result['passed']);self.assertTrue(any('cleanup metadata' in e for e in result['errors']))


if __name__=='__main__':unittest.main()
