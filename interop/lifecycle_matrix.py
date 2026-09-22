#!/usr/bin/env python3
"""Cross-language lifecycle controller: scripts/proofs only, never applies effects."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import json
import hashlib
import ssl
from urllib.parse import urlencode
from content_upload import encoded, NoRedirect
import os
import signal
import urllib.request
import urllib.error
from pathlib import Path
import subprocess
import tempfile
import time
from auth import Issuer, configuration
from matrix import MODES
from observation_wire import ObservationValidator
from matrix import load, write, control, ready, stop, equal, kill_group

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
LANGUAGES = ('python', 'go', 'rust', 'typescript')
UPLOAD_TOKEN = 'TEST-ONLY-independent-upload-token'
UPLOAD_ENV = 'AHP_INTEROP_UPLOAD_TOKEN'

def verify(scenarios, report, receipts, language, exit_code=0):
    """Fail closed on reports AND independent receiver rendezvous evidence."""
    chains=[s for s in scenarios if 'chain' in s]
    if chains and isinstance(report,dict) and isinstance(receipts,dict) and isinstance(receipts.get('entries'),list) and isinstance(report.get('results'),list) and all(isinstance(r,dict) for r in report['results']) and all(isinstance(e,dict) for e in receipts['entries']):
        from observation_chain import verify_chains
        ids={s['requests']['a']['id'] for s in chains}
        names={s['id'] for s in chains}
        is_chain=lambda e:e.get('id',e.get('eventId')) in ids
        chain_report={**report,'results':[r for r in report['results'] if r.get('id') in names]}
        chain_receipts={'entries':[e for e in receipts['entries'] if is_chain(e)]}
        errors=verify_chains(chains,chain_report,chain_receipts,language,exit_code)
        rest=[s for s in scenarios if 'chain' not in s]
        rest_report={**report,'results':[r for r in report['results'] if r.get('id') not in names]}
        rest_receipts={'entries':[e for e in receipts['entries'] if not is_chain(e)]}
        return errors+verify(rest,rest_report,rest_receipts,language,exit_code)
    errors = []
    if exit_code != 0:
        errors.append(f'client exit {exit_code}')
    if not isinstance(report, dict) or report.get('language') != language:
        return errors + ['wrong report language/type']
    results = report.get('results')
    if not isinstance(results, list) or any(not isinstance(r, dict) for r in results):
        return errors + ['malformed results']
    if Counter(r.get('id') for r in results) != Counter(s['id'] for s in scenarios):
        errors.append('result IDs must match scenarios exactly once')
    if any(r.get('status', 'passed') != 'passed' for r in results):
        errors.append('unsupported/inapplicable/failed result is not a pass')
    by_id = {r.get('id'): r for r in results}
    for s in scenarios:
        if not equal(by_id.get(s['id'], {}).get('actual'), s['expected']):
            errors.append(s['id'] + ': local application result mismatch')
    entries = receipts.get('entries') if isinstance(receipts, dict) else None
    if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
        return errors + ['missing/malformed receiver receipts']
    allowed = {'received', 'replied', 'acquired', 'cancelled', 'accepted', 'upload', 'observed', 'emitted', 'discarded'}
    if any(e.get('kind') not in allowed for e in entries):
        errors.append('unknown receipt kind')
    def positions(kind, id):
        return [i for i,e in enumerate(entries) if e.get('kind') == kind and e.get('id') == id]
    sends = Counter(s['requests'][st['key']]['id'] for s in scenarios for st in s['steps'] if st['op'] == 'send')
    for kind in ('received', 'replied'):
        if Counter(e.get('id') for e in entries if e.get('kind') == kind) != sends:
            errors.append(kind + ': exact attempt multiplicity mismatch')
    for id, count in sends.items():
        received, replied = positions('received', id), positions('replied', id)
        if len(received) == len(replied) == count and any(a >= b for a,b in zip(received,replied)):
            errors.append(id + ': reply before receipt')
    for kind, field in [('accepted', 'published'), ('cancelled', 'cancelled')]:
        expected = Counter((s['id'], id) for s in scenarios for id in s['expected'][field])
        found = Counter((e.get('scenario'), e.get('id')) for e in entries if e.get('kind') == kind)
        if found != expected:
            errors.append(kind + ': milestone mismatch')
    acquired_expected=[]; emitted_expected=[]; discarded_expected=[]
    for scenario in scenarios:
        slots={}; terminal=set(); staged=set(); interrupted=set()
        for step in scenario['steps']:
            op=step['op']; id=scenario['requests'][step['key']]['id'] if 'key' in step else None
            if op=='send':slots[step['slot']]=id
            elif op=='receive':
                response_id=slots[step['slot']]
                if response_id not in terminal and response_id not in staged:
                    staged.add(response_id); acquired_expected.append({'kind':'acquired','id':response_id,'scenario':scenario['id']})
            elif op=='cancel':terminal.add(id); interrupted.add(id)
            elif op in ('accept','failOpen') and id in scenario['expected']['published'] and (id in staged or op=='failOpen'):terminal.add(id)
            elif op=='emit':
                stray=step['response']['id']; emitted_expected.append({'kind':'emitted','id':stray}); discarded_expected.append({'kind':'discarded','id':stray,'scenario':scenario['id']})
    for kind,wanted in [('acquired',acquired_expected),('emitted',emitted_expected),('discarded',discarded_expected)]:
        if not equal([e for e in entries if e.get('kind')==kind],wanted):errors.append(kind+': control proof mismatch')
    for e in discarded_expected:
        emitted=positions('emitted',e['id']); discarded=positions('discarded',e['id'])
        if not emitted or not discarded or emitted[0]>=discarded[0]:errors.append('stray discard lacks reader rendezvous')
    uploads = [e for e in entries if e.get('kind') == 'upload']
    upload_steps = [(st,status) for s in scenarios for st,status in zip([x for x in s['steps'] if x['op']=='upload'],s['expected']['uploadStatuses'])]
    if len(uploads) != len(upload_steps): errors.append('upload multiplicity mismatch')
    for receipt,(st,status) in zip(uploads,upload_steps):
        wanted = {k:st[k] for k in ('subscription','ref','size','sha256')} | {'kind':'upload','status':status}
        if not equal(receipt,wanted): errors.append('upload integrity/authorization evidence mismatch')
    expected_observations=[]
    cancelled_observations=set()
    validator=ObservationValidator()
    for s in scenarios:
        cancelled=set()
        for st in s['steps']:
            if st['op']=='cancel': cancelled.add(s['requests'][st['key']]['id'])
            if st['op']!='observe': continue
            request=s['requests'][st['key']]; id=request['id']
            if id in cancelled: cancelled_observations.add((id,st['subscription']))
            event=deepcopy(request['params']['event'])
            event['tool']['input']=deepcopy(s['expected']['states'].get(id,{}).get('input',event['tool']['input']))
            if 'items' in st: event['items']=deepcopy(st['items'])
            message={'jsonrpc':'2.0','method':'hooks/observe','params':{'protocolVersion':'draft','event':event,'subscriptionId':st['subscription']}}
            expected_observations.append({'kind':'observed','eventId':event['id'],'subscription':st['subscription'],'event':event,'message':message})
    boundary_requests={(request['params']['event']['source'],request['params']['event']['id']):request['id'] for s in scenarios for request in s['requests'].values()}
    observed=[e for e in entries if e.get('kind')=='observed']
    if not equal(observed,expected_observations): errors.append('settled observation payload/identity/subscription mismatch')
    requests={r['id']:r for s in scenarios for r in s['requests'].values()}
    for i,receipt in enumerate(entries):
        if receipt.get('kind')!='received':continue
        message=receipt.get('message')
        if not equal(message,requests.get(receipt.get('id'))):
            errors.append('received canonical request mismatch')
        errors.extend(validator.schema_errors(message,'intercept-request'))
        if isinstance(message,dict):
            params=message.get('params',{})
            for item in params.get('event',{}).get('items',[]):
                body=item.get('body')
                if body is not None and not any(u.get('kind')=='upload' and u.get('status')==204 and u.get('subscription')==params.get('subscriptionId') and all(u.get(k)==body.get(k) for k in ('ref','size','sha256')) for u in entries[:i]):
                    errors.append('intercept dispatched before authorized exact upload readiness')
    for receipt in observed:
        errors.extend(validator.errors(receipt.get('message')))
    for i,e in enumerate(entries):
        if e.get('kind') == 'acquired':
            if not any(j<i for j in positions('replied',e.get('id'))): errors.append('acquisition before response')
        if e.get('kind') == 'accepted':
            if not any(j<i for j in positions('acquired',e.get('id'))): errors.append('acceptance before acquired response')
        if e.get('kind') != 'observed': continue
        id=boundary_requests.get((e.get('event',{}).get('source'),e.get('eventId')))
        # JSON-RPC request identity is not the logical boundary identity.
        if not any(j<i for kind in ('accepted','cancelled') for j in positions(kind,id)):
            errors.append('observation before settlement')
        if (id,e.get('subscription')) in cancelled_observations and not any(j<i for j in positions('cancelled',id)):
            errors.append('observation dispatched before planned cancellation')
        for item in e.get('event',{}).get('items',[]):
            body=item.get('body')
            if body is not None and not any(u.get('kind')=='upload' and u.get('status')==204 and u.get('subscription')==e.get('subscription') and all(u.get(k)==body.get(k) for k in ('ref','size','sha256')) for u in entries[:i]):
                errors.append('body dispatched before authorized exact upload readiness')
    for s in scenarios:
        # Check the race partial order from actual receipt/reply/mark records,
        # never from elapsed time or a client-provided passed boolean.
        slots={}; consumed=set()
        for st in s['steps']:
            op=st['op']
            if op=='send':slots[st['slot']]=st['key']
            if op=='receive':consumed.add(slots[st['slot']])
            if op!='cancel':continue
            id=s['requests'][st['key']]['id']; c=positions('cancelled',id); r=positions('replied',id); received=positions('received',id)
            if len(c)!=1 or not r or not received: continue
            if received[0]>=c[0]:errors.append('cancel not rendezvoused after receipt')
            after=st['key'] in consumed
            acquired=positions('acquired',id)
            if after and (not acquired or acquired[0]>=c[0]):errors.append('cancel-after-reply lacks acquisition proof')
            if (after and r[0]>=c[0]) or (not after and c[0]>=r[0]):errors.append('cancel/reply barrier order violated')
        for step_index,step in enumerate(s['steps']):
            if step['op']!='emit':continue
            discarded=positions('discarded',step['response']['id'])
            following=next((st for st in s['steps'][step_index+1:] if st['op']=='release'),None)
            if following:
                live=s['requests'][following['key']]['id']; received=positions('received',live); replied=positions('replied',live)
                emitted=positions('emitted',step['response']['id'])
                if not discarded or not emitted or not received or not replied or not received[0]<emitted[0]<discarded[0]<replied[0]:errors.append('stray frame did not rendezvous while live request pending')
        if s['id']=='late-old-while-next-pending':
            a=s['requests']['a']['id']; b=s['requests']['b']['id']
            ar=positions('replied',a); br=positions('received',b); breply=positions('replied',b)
            if not ar or not br or not breply or not br[0]<ar[0]<breply[0]:errors.append('old reply did not race next pending request')
    return errors

def receiver_probes(endpoint, upload_endpoint, base_request, event_auth=None):
    """Raw byte upload and real event receiver probes; never semantic controls."""
    event_auth = event_auth or {'mode':'none'}
    event_headers = {'Content-Type':'application/json'}
    context = None
    mode = event_auth['mode']
    if mode == 'mtls':
        context = ssl.create_default_context(cafile=event_auth['caFile'])
        context.verify_flags &= ~getattr(ssl, 'VERIFY_X509_STRICT', 0)
        context.load_cert_chain(event_auth['certFile'], event_auth['keyFile'])
    if mode == 'bearer': event_headers['Authorization']='Bearer '+event_auth['token']
    if mode == 'workload': event_headers['Authorization']='Bearer '+event_auth['assertion']
    if mode == 'oauth':
        request=urllib.request.Request(event_auth['tokenEndpoint'],data=urlencode({'grant_type':'client_credentials','client_id':event_auth['clientId'],'client_secret':event_auth['clientSecret'],'audience':event_auth['audience']}).encode(),headers={'Content-Type':'application/x-www-form-urlencoded'})
        with urllib.request.build_opener(NoRedirect).open(request,timeout=5) as response:
            event_headers['Authorization']='Bearer '+json.load(response)['access_token']
    def post(url, payload, headers, tls=None):
        req=urllib.request.Request(url,data=payload,headers=headers,method='POST')
        handlers=[NoRedirect]
        if tls is not None: handlers.append(urllib.request.HTTPSHandler(context=tls))
        try:
            with urllib.request.build_opener(*handlers).open(req,timeout=5) as response:
                response.read(1048577); return response.status
        except urllib.error.HTTPError as error:
            error.read(1048577); return error.code
    raw=bytes(range(256))+b'\x00\xff\xfe'; ref='receiver-probe-ref'
    def metadata(data):return {'ref':ref,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    body=metadata(raw)
    def upload(data, subscription='body', token=UPLOAD_TOKEN):
        headers={'Content-Type':'application/octet-stream','AHP-Subscription':encoded(subscription),'AHP-Content-Ref':encoded(ref),'AHP-Content-SHA256':metadata(data)['sha256']}
        if token is not None:headers['Authorization']='Bearer '+token
        return post(upload_endpoint,data,headers)
    original=deepcopy(base_request['params']['event']); original['id']='receiver-probe-event'
    def notification(subscription='body', descriptor=None):
        event=deepcopy(original)
        event['items']=[{'id':'receiver-probe-item','kind':'text','mediaType':'application/octet-stream','selection':'body','body':descriptor or body}]
        return {'jsonrpc':'2.0','method':'hooks/observe','params':{'protocolVersion':'draft','event':event,'subscriptionId':subscription}}
    def observe(subscription='body',descriptor=None):
        return post(endpoint+'/observe',json.dumps(notification(subscription,descriptor)).encode(),event_headers,context)
    results=[(upload(raw),204,'raw upload readiness'),
             (upload(raw),204,'identical immutable retry'),
             (upload(b'changed bytes'),409,'same ref changed bytes'),
             (upload(raw,token=None),401,'missing independent upload credential'),
             (upload(raw,token='wrong'),401,'wrong independent upload credential'),
             (upload(raw,subscription='metadata'),403,'upload subscription authorization'),
             (observe(),200,'original binary ref readable'),
             (observe('metadata'),409,'cross subscription ref rejected'),
             (observe(descriptor=dict(body,ref='never-uploaded')),409,'missing readiness rejected'),
             (observe(descriptor=dict(body,size=body['size']+1)),409,'wrong descriptor size rejected'),
             (observe(descriptor=dict(body,sha256='0'*64)),409,'wrong descriptor hash rejected'),
             (observe(),200,'negative reads did not mutate bytes')]
    return [label+f': expected {expected}, got {got}' for got,expected,label in results if got!=expected],len(results)

def discover(root):
    result={}
    for lang in LANGUAGES:
        cwd=root/(lang+'-sdk'); manifest=load(cwd/'interop/adapter.json')
        for key in ('lifecycleClient','lifecycleServer'):
            if not isinstance(manifest.get(key),list) or not manifest[key] or not all(isinstance(x,str) for x in manifest[key]):
                raise ValueError(f'{lang}: missing actual {key} command')
        result[lang]=(cwd,manifest)
    return result

def cleanup(processes, child_file, readiness_file):
    """Best-effort discovery; unconditional stop/reap of every known process.

    Return diagnostics instead of letting malformed metadata hide cleanup or
    replace the structured group result. Callers must fail the group on errors.
    """
    groups=set(); errors=[]; direct={p.pid for p in processes}
    for path,register_group in ((Path(child_file),True),(Path(readiness_file),False)):
        try:
            if not path.exists():continue
            pid=load(path)['pid']
            if type(pid) is not int or pid<=1:raise ValueError('invalid cleanup pid')
            if register_group:groups.add(pid)
            try:groups.add(os.getpgid(pid))
            except ProcessLookupError:pass
        except Exception as error:errors.append(f'cleanup metadata {path.name}: {type(error).__name__}')
    try:
        snapshot=subprocess.run(['ps','-axo','pid=,ppid=,pgid='],capture_output=True,text=True,check=True,timeout=5)
        rows=[tuple(map(int,line.split())) for line in snapshot.stdout.splitlines() if line.split()]
        owned=set(direct)
        while True:
            descendants={pid for pid,parent,_ in rows if parent in owned}
            if descendants<=owned:break
            owned.update(descendants)
        groups.update(group for pid,_,group in rows if pid in owned)
    except Exception as error:errors.append('cleanup discovery: '+type(error).__name__)
    if os.getpgrp() in groups:
        groups.remove(os.getpgrp());errors.append('cleanup refused controller process group')
    try:
        for group in groups-direct:
            try:kill_group(group,signal.SIGKILL)
            except Exception as error:errors.append('cleanup child group: '+type(error).__name__)
    finally:
        # Discovery, metadata, or another stop failure NEVER skips this loop.
        for process in reversed(processes):
            try:stop(process)
            except Exception as error:
                errors.append('cleanup direct process: '+type(error).__name__)
                try:
                    process.kill();process.wait(timeout=3)
                except Exception as fallback:errors.append('cleanup direct fallback: '+type(fallback).__name__)
        for group in groups:
            try:kill_group(group,signal.SIGKILL)
            except Exception as error:errors.append('cleanup final group: '+type(error).__name__)
    return errors

def run_group(client_lang,server_lang,transport,adapters,scenario_file,timeout,mode="none",issuer=None,suite="lifecycle",verifier=None,upload_override=None,receiver_checks=True):
    label=f'{client_lang}->{server_lang}/{transport}/{mode}'
    if transport=='stdio' and mode!='none':
        raise ValueError('stdio uses process trust, not HTTP authentication')
    client_cwd,client=adapters[client_lang]; server_cwd,server=adapters[server_lang]
    processes=[]
    row={'group':label,'passed':False,'scenarios':0,'errors':[],'diagnostics':''}
    with tempfile.TemporaryDirectory(prefix='ahp-lifecycle-') as directory:
        d=Path(directory); readiness=d/'ready.json'; server_config=d/'server.json'; report_path=d/'report.json'
        selected=[s for s in load(scenario_file)['scenarios'] if transport in s.get('transports',['http','stdio'])]
        scenario_file=d/'scenarios.json'; write(scenario_file,{'version':1,'scenarios':selected})
        write(server_config,{'suite':suite,'transport':transport,'readinessFile':str(readiness),'scenarioFile':str(scenario_file),'auth':configuration(mode,HERE/'fixtures','server',issuer),'uploadAuth':{'token':UPLOAD_TOKEN,'subscriptions':['body']}})
        cfg={'suite':suite,'transport':transport,'scenarioFile':str(scenario_file),'reportFile':str(report_path),'childPidFile':str(d/'child.json'),'auth':configuration(mode,HERE/'fixtures','client',issuer),'upload':{'auth':{'type':'bearer','tokenEnv':UPLOAD_ENV},'timeoutMs':5000,'maxBytes':1048576}}
        environment=os.environ.copy(); environment[UPLOAD_ENV]=UPLOAD_TOKEN
        try:
            with (d/'server.log').open('w+') as server_log, (d/'client.log').open('w+') as client_log:
                if transport=='http':
                    p=subprocess.Popen(server['lifecycleServer']+['--config',str(server_config)],cwd=server_cwd,stdout=server_log,stderr=server_log,start_new_session=True); processes.append(p)
                    address=ready(readiness,p,timeout)
                    cfg.update(endpoint=address['endpoint'],controlEndpoint=address['controlEndpoint'])
                    cfg['upload']['endpoint']=address['uploadEndpoint']
                else:
                    cfg.update(serverCommand=server['lifecycleServer'],serverCwd=str(server_cwd),serverConfig=str(server_config))
                if upload_override is not None:cfg['upload']=deepcopy(upload_override)
                client_config=d/'client.json'; write(client_config,cfg)
                p=subprocess.Popen(client['lifecycleClient']+['--config',str(client_config)],cwd=client_cwd,stdout=client_log,stderr=client_log,start_new_session=True,env=environment); processes.append(p)
                code=p.wait(timeout=timeout)
                report=load(report_path)
                receipts=control(address['controlEndpoint'],'/receipts') if transport=='http' else report.get('receipts')
                errors=(verifier or verify)(load(scenario_file)['scenarios'],report,receipts,client_lang,code)
                probes=0
                if transport=='http' and suite=='lifecycle' and receiver_checks and not errors:
                    probe_errors,probes=receiver_probes(address['endpoint'],address['uploadEndpoint'],load(scenario_file)['scenarios'][0]['requests']['a'],cfg['auth'])
                    errors.extend(probe_errors)
                if errors:
                    client_log.seek(0); detail=client_log.read()[-1800:]
                else: detail=''
                row = {'group':label,'passed':not errors,'scenarios':len(load(scenario_file)['scenarios']),'receiverProbes':probes,'errors':errors,'diagnostics':detail}
        except Exception as error:
            logs=''
            for file in (d/'client.log',d/'server.log'):
                if file.exists():logs+=file.read_text(errors='replace')[-1800:]
            row = {'group':label,'passed':False,'scenarios':0,'errors':[str(error)],'diagnostics':logs}
        finally:
            cleanup_errors=cleanup(processes,d/'child.json',readiness)
            if cleanup_errors:
                row['errors'].extend(cleanup_errors);row['passed']=False
        return row

def main():
    p=argparse.ArgumentParser(); p.add_argument('--root',type=Path,default=ROOT); p.add_argument('--scenarios',type=Path,default=HERE/'lifecycle-scenarios.json'); p.add_argument('--client',choices=LANGUAGES); p.add_argument('--server',choices=LANGUAGES); p.add_argument('--transport',choices=('http','stdio')); p.add_argument('--auth',choices=MODES); p.add_argument('--workers',type=int,default=4); p.add_argument('--timeout',type=int,default=90); p.add_argument('--output',type=Path,default=HERE/'lifecycle-matrix-results.json'); args=p.parse_args()
    adapters=discover(args.root.resolve()); groups=[(c,s,t,mode) for c in ([args.client] if args.client else LANGUAGES) for s in ([args.server] if args.server else LANGUAGES) for t in ([args.transport] if args.transport else ('http','stdio')) for mode in ([args.auth] if args.auth else MODES) if t=='http' or mode=='none']
    if not groups:p.error('no applicable groups: stdio uses --auth none')
    results=[]
    with Issuer() as issuer, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(run_group,c,s,t,adapters,args.scenarios.resolve(),args.timeout,mode,issuer) for c,s,t,mode in groups]
        for f in as_completed(futures):
            result=f.result(); results.append(result); print(('PASS ' if result['passed'] else 'FAIL ')+result['group']+(' '+ '; '.join(result['errors'][:3]) if result['errors'] else ''),flush=True)
    summary={'scenarioCount':len(load(args.scenarios)['scenarios']),'groups':len(results),'passed':sum(r['passed'] for r in results),'scenarioExecutions':sum(r['scenarios'] for r in results if r['passed']),'receiverProbes':sum(r.get('receiverProbes',0) for r in results if r['passed']),'auth':'HTTP all selected modes; stdio process trust; independent bearer upload auth','results':sorted(results,key=lambda r:r['group'])}; write(args.output,summary)
    print(f"{summary['passed']}/{summary['groups']} groups; {summary['scenarioExecutions']} lifecycle scenario executions")
    return 0 if summary['passed']==summary['groups'] else 1
if __name__=='__main__':raise SystemExit(main())
