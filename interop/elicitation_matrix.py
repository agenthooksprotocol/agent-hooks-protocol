#!/usr/bin/env python3
"""Real four-SDK HTTP upload/intercept matrix. Offline, no LLM or semantic controls.

Each SDK client transmits bytes over loopback HTTP to each SDK receiver. Receivers
validate canonical envelopes and MCP uploaded bodies themselves. Only this oracle
knows expected results. No proxy or central semantic evaluator is on the wire.
"""
import argparse, base64, copy, hashlib, json, os, secrets, selectors, subprocess, tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from adapter_builds import go_command

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent.parent
SCHEMA=ROOT/'agent-hooks-protocol/schema/draft'
LANGUAGES=('typescript','python','go','rust')

def json_equal(actual, expected):
    """Compare JSON values without treating booleans as numbers."""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(json_equal(actual[k], expected[k]) for k in actual)
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(json_equal(a, e) for a, e in zip(actual, expected))
    return actual == expected


def stderr_tail(stream):
    stream.seek(0, os.SEEK_END)
    stream.seek(max(0, stream.tell() - 1500))
    return stream.read(1500).decode('utf-8', errors='replace')


def commands():
    return {
      'typescript':['node',str(ROOT/'typescript-sdk/interop/elicitation.mjs')],
      'python':[str(ROOT/'python-sdk/.venv/bin/python'),str(ROOT/'python-sdk/interop/elicitation.py')],
      'go':go_command('elicitation'),
      'rust':[str(ROOT/'rust-sdk/target/debug/elicitation')],
    }

def encode(b):return base64.b64encode(b).decode()
def bytes_json(v):return (json.dumps(v,ensure_ascii=False,indent=1)+'\n').encode()
def identity(s):return base64.urlsafe_b64encode(s.encode()).rstrip(b'=').decode()
def reference(name,raw):return {'ref':name,'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def upload(ref,raw):
    return {'path':'/upload','bytes':encode(raw),'localRef':ref['ref'],
        'headers':{'Content-Type':'application/octet-stream','AHP-Content-SHA256':ref['sha256']}}
def intercept(envelope):return {'path':'/hooks/intercept','headers':{'Content-Type':'application/json'},'bytes':encode(bytes_json(envelope))}

def envelopes(name,request,result,selection="body"):
    mode=request.get('mode','form'); mode=mode if mode in ('form','url') else 'form'
    def make(stage,payload):
        raw=bytes_json(payload);ref=reference(name+':'+stage,raw)
        item={'id':name+':'+stage+':item','kind':'elicitation.'+stage,'mediaType':'application/json','selection':selection}
        if selection=='body':item['body']=ref
        meta={'server':'asserted-not-authenticated','mode':mode}
        if selection!='omit':meta[stage]=item
        if stage=='result':meta['action']=payload.get('action','accept') if payload.get('action') in ('accept','decline','cancel') else 'accept'
        event={'id':name+':'+stage,'source':'urn:spoofed:human','time':'2026-09-15T12:00:00Z',
            'type':'user.elicitation.'+stage,'session':{'id':name},'elicitation':meta}
        if stage=='result':event['parentEventId']=name+':request'
        envelope={'jsonrpc':'2.0','id':event['id'],'method':'hooks/intercept','params':{'protocolVersion':'draft','event':event,'capabilities':{'effects':[]}}}
        return envelope,ref,raw
    return make('request',request),make('result',result)

def cases():
    form={'mode':'form','message':'Choose café ☕','requestedSchema':{'type':'object','properties':{
        'answer':{'type':'string'},'tags':{'type':'array','items':{'type':'string','enum':['a','b']}}},'required':['answer']},
        '_meta':{'trace':'unchanged'}}
    accepted={'action':'accept','content':{'answer':'café','tags':['a','b'],'fraction':1.25},'_meta':{'keep':{'nested':['x',1]}}}
    url={'mode':'url','message':'Authorize externally','url':'https://example.test/authorize?request=42','elicitationId':'external-42','_meta':{'flow':'unchanged'}}
    rows=[]
    def add(name,req,res,request_status=200,result_status=200,mutation=None):
        rows.append({'selection':'body','id':name,'request':copy.deepcopy(req),'result':copy.deepcopy(res),'requestStatus':request_status,'resultStatus':result_status,'mutation':mutation})
    add('form-accept-selected-complete-bytes',form,accepted)
    implicit=copy.deepcopy(form);implicit.pop('mode');add('form-default-mode',implicit,accepted)
    for mode,req in [('form',form),('url',url)]:
        for action in ('decline','cancel'):add(mode+'-'+action,req,{'action':action})
    add('url-accept-not-completion',url,{'action':'accept','_meta':{'keep':True}})
    for field in ('message','requestedSchema'):
        req=copy.deepcopy(form);req.pop(field);add('form-missing-'+field,req,accepted,400)
    for field in ('message','url','elicitationId'):
        req=copy.deepcopy(url);req.pop(field);add('url-missing-'+field,req,{'action':'accept'},400)
    req=copy.deepcopy(form);req['schema']=req.pop('requestedSchema');add('renamed-requestedSchema',req,accepted,400)
    req=copy.deepcopy(form);req['mode']='arbitrary';add('invalid-mode',req,accepted,400)
    req=copy.deepcopy(form);req['requestedSchema']['properties']['nested']={'type':'object'};add('nonprimitive-form-schema',req,accepted,400)
    for name,content in [('array',[]),('nested',{'nested':{}}),('null',{'answer':None}),('numeric-array',{'tags':[1]})]:
        add('bad-content-'+name,form,{'action':'accept','content':content},result_status=400)
    add('bad-action',form,{'action':'approve'},result_status=400)
    for action in ('decline','cancel'):add(action+'-with-content',form,{'action':action,'content':{}},result_status=400)
    add('url-content-forbidden',url,{'action':'accept','content':{}},result_status=400)
    add('mode-metadata-mismatch',form,accepted,request_status=400,mutation='request-mode')
    add('action-metadata-mismatch',form,accepted,result_status=400,mutation='result-action')
    add('server-metadata-mismatch',form,accepted,result_status=400,mutation='result-server')
    add('request-not-preuploaded',form,accepted,request_status=400,mutation='missing-upload')
    add('request-digest-mismatch',form,accepted,request_status=400,mutation='bad-digest')
    for mode,req in [('form',form),('url',url)]:
        for selected in ('metadata','omit'):
            private_req=copy.deepcopy(req);private_req['message']='SECRET-SELECTED-ELICITATION-PROMPT'
            private_res={'action':'accept','_meta':{'private':'SECRET-SELECTED-ELICITATION-ANSWER'}}
            if mode=='form':private_res['content']={'answer':'SECRET-SELECTED-ELICITATION-ANSWER'}
            add(mode+'-'+selected+'-no-upload',private_req,private_res)
            rows[-1]['selection']=selected
    add('selected-request-gap-fails-closed',form,accepted,request_status=400,mutation='request-gap')
    add('selected-result-gap-fails-closed',form,accepted,result_status=400,mutation='result-gap')
    for name,answer in [('missing-required',{'action':'accept','content':{}}),('absent-required-content',{'action':'accept'}),('wrong-field-type',{'action':'accept','content':{'answer':4}}),('invalid-enum-selection',{'action':'accept','content':{'answer':'yes','tags':['unlisted']}})]:
        add('submitted-form-'+name,form,answer,result_status=400)
    numeric=copy.deepcopy(form);numeric['requestedSchema']['properties']['amount']={'type':'number','minimum':1,'maximum':10}
    add('submitted-number-out-of-bounds',numeric,{'action':'accept','content':{'answer':'yes','amount':11}},result_status=400)
    # Defaults are upstream annotations: preserve structurally permitted values,
    # do not reject them merely for disagreeing with a property's declared type.
    defaults=copy.deepcopy(form);defaults['requestedSchema']['properties']['answer']['default']='unchanged default'
    add('submitted-form-default-not-applied',defaults,{'action':'accept','content':{'answer':'explicit'}})
    defaults['requestedSchema']['properties']['amount']={'type':'integer','default':1.25}
    defaults['requestedSchema']['properties']['tags']['default']=['not-in-enum']
    add('upstream-default-annotations-preserved',defaults,{'action':'accept','content':{'answer':'explicit','amount':2,'tags':['a']}})
    for selected in ('body','metadata','omit'):
        for mutation in ('missing-parent','wrong-parent','wrong-source','wrong-session','missing-session'):
            add('correlation-'+selected+'-'+mutation,form,accepted,result_status=400,mutation=mutation)
            rows[-1]['selection']=selected
    return rows

def atomic_cases():
    fixture=cases()[0];rows=[];expected=[]
    def add(name,phase,effects,grants,answer=None,selection='body',operations=None):
        (request,rr,rb),(result,sr,sb)=envelopes('atomic-'+name,fixture['request'],fixture['result'],selection)
        boundary=request if phase=='request' else result
        boundary['params']['capabilities']={'effects':grants}
        if 'modify' in grants:boundary['params']['capabilities']['modify']={'content':operations or {'replace':True,'merge':True}}
        rows.append({'op':'apply','request':request,'result':None if phase=='request' else result,
                     'uploads':[] if selection!='body' else [{'ref':rr['ref'],'bytes':encode(rb)},{'ref':sr['ref'],'bytes':encode(sb)}],'effects':effects})
        expected.append(answer)
    supplied={'action':'accept','content':{'answer':'returned by hook','tags':['b']},'_meta':{'preserved':True}}
    add('before-return','request',[{'type':'return','value':supplied}],['return'],supplied)
    add('before-deny','request',[{'type':'deny','reason':'Policy'}],['deny'],{'action':'decline'})
    replaced=copy.deepcopy(fixture['result']);replaced['content']={'answer':'modified','tags':['b']}
    add('result-replace','result',[{'type':'modify','target':'content','operation':'replace','value':replaced['content']}],['modify'],replaced)
    merged=copy.deepcopy(fixture['result']);merged['content']['answer']='merged'
    add('result-merge','result',[{'type':'modify','target':'content','operation':'merge','value':{'answer':'merged'}}],['modify'],merged)
    replacement={'type':'modify','target':'content','operation':'replace','value':{'answer':'staged','tags':['a']}}
    add('invalid-second-effect-rollback','result',[replacement,{'type':'modify','target':'content','operation':'merge','value':{'answer':9}}],['modify'])
    add('ungranted-second-effect-rollback','result',[replacement,{'type':'deny','reason':'Not granted'}],['modify'])
    add('invalid-returned-form','request',[{'type':'return','value':{'action':'accept','content':{}}}],['return'])
    add('ungranted-return','request',[{'type':'return','value':supplied}],[])
    add('ungranted-deny','request',[{'type':'deny','reason':'Policy'}],[])
    add('conflicting-terminal-effects','request',[{'type':'return','value':supplied},{'type':'deny','reason':'Policy'}],['return','deny'])
    add('operation-not-granted','result',[replacement],['modify'],operations={'replace':False,'merge':True})
    add('metadata-cannot-execute','request',[{'type':'return','value':supplied}],['return'],selection='metadata')
    add('omit-cannot-execute','request',[{'type':'deny','reason':'Policy'}],['deny'],selection='omit')
    url=next(row for row in cases() if row['id']=='url-accept-not-completion')
    for name,effect,answer in [
        ('url-return',{'type':'return','value':{'action':'accept'}},{'action':'accept'}),
        ('url-return-content-rejected',{'type':'return','value':{'action':'accept','content':{}}},None),
        ('url-deny',{'type':'deny','reason':'Policy'},{'action':'decline'}),
    ]:
        (request,rr,rb),_=envelopes('atomic-'+name,url['request'],url['result'])
        request['params']['capabilities']['effects']=[effect['type']]
        rows.append({'op':'apply','request':request,'result':None,'uploads':[{'ref':rr['ref'],'bytes':encode(rb)}],'effects':[effect]})
        expected.append(answer)
    return rows,expected


def plan_cases(rows):
    steps=[];statuses=[];wanted=[]
    for row in rows:
        (request,rref,rraw),(result,sref,sraw)=envelopes(row['id'],row['request'],row['result'],row['selection'])
        mutation=row['mutation']
        if mutation=='request-mode':request['params']['event']['elicitation']['mode']='url'
        if mutation=='result-action':result['params']['event']['elicitation']['action']='cancel'
        if mutation=='result-server':result['params']['event']['elicitation']['server']='another-server'
        result_event=result['params']['event']
        if mutation=='missing-parent':result_event.pop('parentEventId')
        if mutation=='wrong-parent':result_event['parentEventId']='unrelated-request'
        if mutation=='wrong-source':result_event['source']='urn:unrelated:source'
        if mutation=='wrong-session':result_event['session']['id']='unrelated-session'
        if mutation=='missing-session':result_event.pop('session')
        selected=row['selection']
        if selected=='body' and mutation not in ('missing-upload','request-gap'):steps.append(upload(rref,rraw));statuses.append(201)
        if selected=='body' and mutation!='result-gap':steps.append(upload(sref,sraw));statuses.append(201)
        for stage,envelope in [('request',request),('result',result)]:
            if mutation==stage+'-gap':
                item=envelope['params']['event']['elicitation'][stage];item.pop('body');item['gap']={'reason':'Unavailable selected body'}
        if mutation=='bad-digest':request['params']['event']['elicitation']['request']['body']['sha256']='0'*64
        steps.append(intercept(request));statuses.append(row['requestStatus'])
        if row['requestStatus']==200:
            wanted.append({'message':request,'bytes':encode(rraw) if selected=='body' else '', 'summary':{'request':row['request']} if selected=='body' else {'selection':selected}})
            steps.append(intercept(result));statuses.append(row['resultStatus'])
            if row['resultStatus']==200:
                summary={'request':row['request'],'result':row['result']} if selected=='body' else {'selection':{'request':selected,'result':selected},'bodyValidation':'not-selected'}
                summary.update(provenance={'kind':'mcp','authenticatedSource':'AUTHENTICATED'},externalCompletion=False)
                wanted.append({'message':result,'bytes':encode(sraw) if selected=='body' else '', 'summary':summary})
    # Independent uploads allocate immutable receiver refs; credentials authorize scope.
    r=reference('immutable',b'one');steps.append(upload(r,b'one'));statuses.append(201)
    changed=reference('immutable',b'two');steps.append(upload(changed,b'two'));statuses.append(201)
    wrong=upload(reference('unauthorized',b'x'),b'x');wrong['headers']['Authorization']='Bearer unauthorized-upload'
    steps.append(wrong);statuses.append(401)
    return steps,statuses,wanted

def replace_refs(value, refs):
    """Rebind local placeholders without repairing intentional bad metadata."""
    if isinstance(value, list):return [replace_refs(item, refs) for item in value]
    if not isinstance(value, dict):return value
    result={key:replace_refs(item, refs) for key,item in value.items()}
    if set(value)=={'ref','size','sha256'} and value['ref'] in refs:
        result['ref']=refs[value['ref']]['ref']
    return result


def transmit_steps(command, endpoint, token, steps, statuses, env, upload_token=None):
    """Use the native sender, verifying each upload before any dependent event."""
    if len(steps)!=len(statuses):raise AssertionError("step status count")
    results=[];refs={};immutable={};sent=[]
    for original,expected_status in zip(steps,statuses):
        step={key:value for key,value in original.items() if key!='localRef'}
        if step['path']=='/hooks/intercept':
            message=replace_refs(json.loads(base64.b64decode(step['bytes'])),refs)
            step['bytes']=encode(bytes_json(message))
        plan={'endpoint':endpoint,'token':token,'uploadToken':upload_token,'steps':[step]}
        out=subprocess.run(command+['client'],input=json.dumps(plan),capture_output=True,text=True,env=env,timeout=30)
        if out.returncode:raise RuntimeError(out.stderr[-1500:])
        replies=json.loads(out.stdout)
        if len(replies)!=1:raise AssertionError('sender response count')
        result=replies[0]
        if result['status']!=expected_status:
            raise AssertionError(f"{step['path']} expected {expected_status}, got {result['status']}")
        if step['path']=='/upload' and expected_status==201:
            descriptor=json.loads(result['body']);raw=base64.b64decode(step['bytes'])
            if not isinstance(descriptor,dict) or set(descriptor)!={'ref','size','sha256'}:
                raise AssertionError('invalid upload descriptor')
            if not isinstance(descriptor['ref'],str) or not descriptor['ref'] or type(descriptor['size']) is not int or descriptor['size']!=len(raw) or descriptor['sha256']!=hashlib.sha256(raw).hexdigest():
                raise AssertionError('upload descriptor integrity')
            if descriptor['ref'] in immutable and immutable[descriptor['ref']]!=raw:
                raise AssertionError('receiver ref mutated')
            immutable[descriptor['ref']]=raw;refs[original['localRef']]=descriptor
        sent.append(step);results.append(result)
    return results,refs,sent


def concurrent_plan():
    """Two outstanding requests share a session; reverse-order replies stay bound.

    A duplicate live ID and a wrong-session reply must not overwrite or consume
    either pending request. The second form deliberately has a different schema.
    """
    first=cases()[0];second_request=copy.deepcopy(first['request'])
    second_request['requestedSchema']={'type':'object','properties':{'count':{'type':'integer'}},'required':['count']}
    second_result={'action':'accept','content':{'count':7}}
    pairs=[envelopes('concurrent-a',first['request'],first['result']),envelopes('concurrent-b',second_request,second_result)]
    steps=[];statuses=[];wanted=[]
    for pair in pairs:
        for _,ref,raw in pair:steps.append(upload(ref,raw));statuses.append(201)
    for request, _ in pairs:
        message,_,raw=request;message['params']['event']['session']['id']='shared-concurrent-session'
        steps.append(intercept(message));statuses.append(200)
        wanted.append({'message':message,'bytes':encode(raw),'summary':{'request':json.loads(raw)}})
    steps.append(intercept(pairs[0][0][0]));statuses.append(400)
    for _,result in pairs:result[0]['params']['event']['session']['id']='shared-concurrent-session'
    wrong=copy.deepcopy(pairs[0][1][0]);wrong['params']['event']['session']['id']='wrong-concurrent-session'
    steps.append(intercept(wrong));statuses.append(400)
    for request,result in reversed(pairs):
        message,_,raw=result;steps.append(intercept(message));statuses.append(200)
        wanted.append({'message':message,'bytes':encode(raw),'summary':{'request':json.loads(request[2]),'result':json.loads(raw),'provenance':{'kind':'mcp','authenticatedSource':'AUTHENTICATED'},'externalCompletion':False}})
    return steps,statuses,wanted


def run_pair(pair):
    sender,receiver=pair;cmd=commands();token=secrets.token_urlsafe(32);upload_token=secrets.token_urlsafe(32)
    env=os.environ.copy();env['AHP_ELICITATION_TOKEN']=token;env['AHP_ELICITATION_UPLOAD_TOKEN']=upload_token;env['PYTHONPATH']=str(ROOT/'python-sdk/src')
    proc=None;stderr=tempfile.TemporaryFile()
    try:
        proc=subprocess.Popen(cmd[receiver]+['server',str(SCHEMA),'authenticated:'+sender],stdout=subprocess.PIPE,stderr=stderr,text=True,env=env)
        selector=selectors.DefaultSelector();selector.register(proc.stdout,selectors.EVENT_READ)
        ready=selector.select(30);selector.close()
        if not ready:raise RuntimeError('receiver startup timed out')
        line=proc.stdout.readline()
        if not line:raise RuntimeError('receiver startup failed: '+stderr_tail(stderr))
        endpoint=json.loads(line)['endpoint']
        atomic,atomic_expected=atomic_cases()
        applied=subprocess.run(cmd[sender]+['check',str(SCHEMA),'authenticated:'+sender],input=json.dumps(atomic),capture_output=True,text=True,env=env,timeout=45)
        if applied.returncode:raise RuntimeError('sender atomic helper failed: '+applied.stderr[-1000:])
        applied_results=json.loads(applied.stdout)
        if len(applied_results)!=len(atomic):raise AssertionError('atomic result count')
        wire_rows=cases();atomic_publications=0
        for fixture,actual,expected in zip(atomic,applied_results,atomic_expected):
            if actual.get('inputUnchanged') is not True:raise AssertionError('atomic staging mutated input')
            if actual.get('accepted') is not (expected is not None):raise AssertionError('atomic staging acceptance mismatch')
            if expected is None:
                if 'summary' in actual:raise AssertionError('partial failed result published')
                continue
            summary=actual['summary']
            if not json_equal(summary['result'], expected) or summary['externalCompletion'] is not False:raise AssertionError('atomic effect application mismatch')
            if summary['provenance']!={'kind':'hook','authenticatedSource':'authenticated:'+sender,'effects':[e['type'] for e in fixture['effects']]}:raise AssertionError('atomic provenance mismatch')
            # Upload and deliver the SDK's actual staged output, not an expected
            # fixture substituted into a receiver. Expected values were checked
            # independently above. Both receiver and sender remain ordinary SDKs.
            wire_rows.append({'id':'published:'+fixture['request']['id'],'selection':'body','request':summary['request'],'result':summary['result'],'requestStatus':200,'resultStatus':200,'mutation':None})
            atomic_publications+=1
        steps,statuses,wanted=plan_cases(wire_rows)
        concurrent_steps,concurrent_statuses,concurrent_receipts=concurrent_plan()
        steps+=concurrent_steps;statuses+=concurrent_statuses;wanted+=concurrent_receipts
        # Wrong bearer credential is sent over the same SDK client stack.
        bad=subprocess.run(cmd[sender]+['client'],input=json.dumps({'endpoint':endpoint,'token':'wrong','steps':[{'path':'/receipts','bytes':''}]}),capture_output=True,text=True,env=env,timeout=30)
        if bad.returncode or json.loads(bad.stdout)[0]['status']!=401:raise AssertionError('unauthenticated access accepted')
        results,refs,steps=transmit_steps(cmd[sender],endpoint,token,steps+[{'path':'/receipts','bytes':''}],statuses+[200],env,upload_token)
        wanted=replace_refs(wanted,refs)
        receipts=json.loads(results[-1]['body'])
        for receipt in wanted:
            if 'provenance' in receipt['summary']:receipt['summary']['provenance']['authenticatedSource']='authenticated:'+sender
        if not json_equal(receipts, wanted):raise AssertionError('exact receiver receipts differ (body bytes, payload, metadata, identity or completion)')
        # Verify actual response correlation and canonical no-effect envelopes.
        for step,result in zip(steps,results):
            if step['path']=='/hooks/intercept' and result['status']==200:
                request=json.loads(base64.b64decode(step['bytes']));response=json.loads(result['body'])
                if response!={'jsonrpc':'2.0','id':request['id'],'result':{'protocolVersion':'draft','effects':[]}}:raise AssertionError('bad wire response')
        return {'sender':sender,'receiver':receiver,'status':'passed','scenarios':len(wire_rows)+1,'concurrentRequests':2,'atomicPublications':atomic_publications,'wireOperations':len(steps)+1,'acceptedReceipts':len(receipts)}
    except Exception as error:return {'sender':sender,'receiver':receiver,'status':'failed','error':str(error)}
    finally:
        if proc is not None:
            proc.terminate()
            try:proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:proc.kill();proc.communicate()
        stderr.close()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=HERE/'elicitation-matrix-results.json');parser.add_argument('--sender',choices=LANGUAGES);parser.add_argument('--receiver',choices=LANGUAGES);args=parser.parse_args()
    pairs=[(s,r) for s in LANGUAGES for r in LANGUAGES if (not args.sender or s==args.sender) and (not args.receiver or r==args.receiver)]
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(run_pair,pairs))
    report={'transport':'http','authentication':'bearer','offline':True,'results':results,'passed':sum(r['status']=='passed' for r in results),'total':len(results)}
    args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2));return 0 if report['passed']==report['total'] else 1
if __name__=='__main__':raise SystemExit(main())
