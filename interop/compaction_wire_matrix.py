#!/usr/bin/env python3
"""Canonical AHP HTTP/stdio 4x4 matrix. Only hooks/intercept is on event wire.

Each native sender runs its public compaction runtime. Callback replies arrive
from a different SDK's canonically validating receiver and drive subsequent wire
events and final downstream application. HTTP uploads precede BOTH transports.
"""
import argparse, copy, hashlib, json, os, secrets, selectors, signal, subprocess, tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from compaction_matrix import LANGUAGES, commands as transport_commands

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent.parent
SCHEMA=ROOT/'agent-hooks-protocol/schema/draft'


def commands():
    return {'python':[str(ROOT/'python-sdk/.venv/bin/python'),str(ROOT/'python-sdk/interop/compaction_wire.py')],
            'typescript':['node',str(ROOT/'typescript-sdk/interop/compaction-wire.mjs')],
            'go':['/tmp/ahp-compaction-wire-go'],
            'rust':[str(ROOT/'rust-sdk/target/debug/compaction_wire')]}


def modify(target,value):return {'type':'modify','target':target,'operation':'replace','value':value}
def effects(*values):return {'kind':'effects','effects':list(values)}
def append(target,suffix):return {'kind':'append','target':target,'suffix':suffix}


def cases():
    definitions=[]
    def add(name,before,after,final,instructions='base',generated=True,applied=True,failures=0,seen=()):
        definitions.append((name,before,after,{'final':final,'instructions':instructions,'generated':generated,'applied':applied,'failures':failures,'seen':list(seen)}))
    watch=effects();msg={'type':'message','text':'leaked'}
    add('chain',[append('instructions',':one'),append('instructions',':two'),watch],[append('summary',':safe'),watch],
        'summary:base:one:two:safe','base:one:two',seen=['base','base:one','base:one:two','summary:base:one:two','summary:base:one:two:safe'])
    add('supplied',[effects({'type':'return','value':'cached'}),watch],[append('summary',':safe'),watch],
        'cached:safe',generated=False,seen=['base','base','cached','cached:safe'])
    add('before-atomic',[effects({'type':'return','value':'cached'}),(effects(modify('instructions','leaked'),msg,modify('summary','wrong')),'fail-open'),watch],[append('summary',':safe'),watch],
        'cached:safe',generated=False,failures=1,seen=['base','base','base','cached','cached:safe'])
    add('after-atomic',[watch],[append('summary',':prefix'),(effects(modify('summary','leaked'),msg,modify('instructions','wrong')),'fail-open'),watch],
        'summary:base:prefix',failures=1,seen=['base','summary:base','summary:base:prefix','summary:base:prefix'])
    add('after-closed',[watch],[effects(modify('summary','leaked'),modify('instructions','wrong')),watch],
        'summary:base',applied=False,failures=1,seen=['base','summary:base'])
    add('before-closed',[effects(modify('summary','wrong')),watch],[watch],
        None,generated=False,applied=False,failures=1,seen=['base'])
    add('before-open',[(effects(modify('summary','wrong')),'fail-open'),watch],[watch],
        'summary:base',failures=1,seen=['base','base','summary:base'])
    add('after-open',[watch],[(effects(modify('instructions','wrong')),'fail-open'),watch],
        'summary:base',failures=1,seen=['base','summary:base','summary:base'])
    config={};rows=[];expected={}
    for name,before,after,wanted in definitions:
        row={'name':name}
        for boundary,actions in [('before',before),('after',after)]:
            row[boundary]=[]
            for i,action in enumerate(actions):
                policy='fail-closed'
                if isinstance(action,tuple):action,policy=action
                sub=f'{name}-{boundary}-{i}'
                config[sub]=action
                row[boundary].append({'supplier':sub,'failurePolicy':policy})
        rows.append(row);expected[name]=wanted
    return rows,config,expected


def check(outputs,receipts,expected):
    assert len(outputs)==len(expected),'case count'
    wire_count=0
    for out in outputs:
        name=out['name'];want=expected[name];result=out['result'];trace=out['trace']
        for key in ('instructions','generated','applied'):
            assert result[key]==want[key],(name,key,result[key],want[key])
        assert len(result['failures'])==want['failures'],(name,'failure policy')
        assert result['messages']==[],(name,'staged message leak')
        actual=result['bodies'][result['summary']['ref']] if result['summary'] is not None else None
        assert actual==want['final'],(name,'summary',actual,want['final'])
        assert out['downstream']==([want['final']] if want['applied'] else []),(name,'downstream application')
        assert 'leaked' not in result['bodies'].values(),(name,'staged content leak')
        assert len(trace)==len(want['seen']),(name,'missing wire hooks',len(trace),len(want['seen']))
        refs={};item_ids={};serial=[]
        for entry in trace:
            wire_count+=1;request=entry['request'];reply=entry['response'];event=request['params']['event'];boundary=event['type'].split('.')[-1]
            assert request['method']=='hooks/intercept' and request['id']==event['id']==name+':'+boundary
            assert reply['jsonrpc']=='2.0' and reply['id']==request['id'] and reply['result']['protocolVersion']=='draft'
            matches=[r for r in receipts if r['subscription']==entry['subscription']]
            assert len(matches)==1,(name,'receiver receipt count',entry['subscription'])
            receipt=matches[0]
            assert receipt['request']==request and receipt['response']==reply,(name,'receiver wire evidence')
            target='instructions' if boundary=='before' else 'summary';item=event[target];body=receipt['bodies'][item['id']]
            serial.append(body)
            assert item['selection']=='body' and item['mediaType']=='text/plain'
            raw=body.encode();ref=item['body'];assert ref['size']==len(raw) and ref['sha256']==hashlib.sha256(raw).hexdigest()
            assert ref['ref']=='urn:sha256:'+ref['sha256']
            assert item['id']==name+':'+target,(name,'logical item ID')
            if target in refs and body!=refs[target][0]:assert ref['ref']!=refs[target][1],(name,'changed bytes same ref')
            refs[target]=(body,ref['ref']);item_ids[target]=item['id']
            assert request['params']['capabilities']['modify']=={target:{'replace':True,'merge':False}}
            if boundary=='after':
                assert event['parentEventId']==name+':before'
                assert event['removed']==[{'id':name+':context'}]
                if not want['generated']:
                    assert event['execution']=={'status':'skipped','reason':'supplied_result','subscriptionId':name+'-before-0'}
                else:assert event['execution']=={'status':'executed'}
        assert serial==want['seen'],(name,'serial wire bytes',serial,want['seen'])
    assert len(receipts)==wire_count,'unmatched receiver receipts'
    return wire_count


def probe(sender,receiver,transport,endpoint,token,receiver_command,outputs,env):
    # Native sender transmits invalid canonical requests directly. They must not
    # be rejected only by a sender-side validator or accepted as generic JSON.
    first=outputs[0]['trace'][0];valid=first['request'];sub=first['subscription'];requests=[]
    bad=copy.deepcopy(valid);bad['params']['event'].pop('trigger');requests.append(bad)
    bad=copy.deepcopy(valid);bad['params']['capabilities']['modify']={'summary':{'replace':True,'merge':False}};requests.append(bad)
    bad=copy.deepcopy(valid);bad['params']['event']['instructions']['body']['ref']='urn:not-uploaded';requests.append(bad)
    bad=copy.deepcopy(valid);bad['params']['event']['instructions']['body']['sha256']='0'*64;requests.append(bad)
    plan={'transport':transport,'requests':requests,'endpoint':endpoint+'/hooks/intercept/'+sub,'token':token,
          'command':receiver_command+['stdio',sub]}
    # Original native transport clients append a trailing stdio argument; the
    # receiver consumes its fixed startup config and subscription arguments only.
    out=subprocess.run(transport_commands()[sender]+['client'],input=json.dumps(plan),capture_output=True,text=True,env=env,timeout=60)
    if out.returncode:raise RuntimeError('negative sender: '+out.stderr[-1000:])
    replies=json.loads(out.stdout)
    assert len(replies)==4
    for reply in replies:assert reply.get('error',{}).get('code')==-32602,('receiver accepted invalid request',reply)
    return len(replies)


def run_pair(pair):
    sender,receiver,transport=pair;rows,config,expected=cases();cmd=commands();proc=None
    env=os.environ.copy();env['PYTHONPATH']=str(ROOT/'python-sdk/src');env['AHP_COMPACTION_TOKEN']=secrets.token_urlsafe(24);env['AHP_COMPACTION_UPLOAD_TOKEN']=secrets.token_urlsafe(24)
    with tempfile.TemporaryDirectory(prefix='ahp-compaction-wire-') as directory:
        store=Path(directory)/'bodies';store.mkdir();config_path=Path(directory)/'subscriptions.json';config_path.write_text(json.dumps(config))
        receiver_command=cmd[receiver]+[str(SCHEMA),str(store),str(config_path)]
        try:
            proc=subprocess.Popen(receiver_command+['server'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
            selector=selectors.DefaultSelector();selector.register(proc.stdout,selectors.EVENT_READ);ready=selector.select(45);selector.close()
            if not ready:raise RuntimeError('receiver startup timeout')
            line=proc.stdout.readline()
            if not line:raise RuntimeError('receiver startup failed: '+proc.stderr.read()[-1500:])
            endpoint=json.loads(line)['endpoint']
            plan={'cases':rows,'schema':str(SCHEMA),'transport':transport,'endpoint':endpoint,'token':env['AHP_COMPACTION_TOKEN'],'uploadToken':env['AHP_COMPACTION_UPLOAD_TOKEN'],'receiverCommand':receiver_command}
            host=subprocess.Popen(cmd[sender]+['host'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env,start_new_session=True)
            try:
                stdout,stderr=host.communicate(json.dumps(plan),timeout=240)
            except subprocess.TimeoutExpired:
                os.killpg(host.pid,signal.SIGKILL);host.communicate()
                raise RuntimeError('host timed out; process group terminated')
            if host.returncode:raise RuntimeError('host: '+stderr[-2000:])
            outputs=json.loads(stdout)
            receipt_path=store/'receipts.jsonl';receipts=[json.loads(line) for line in receipt_path.read_text().splitlines()] if receipt_path.exists() else []
            count=check(outputs,receipts,expected)
            negatives=probe(sender,receiver,transport,endpoint,env['AHP_COMPACTION_TOKEN'],receiver_command,outputs,env)
            assert len(receipt_path.read_text().splitlines())==count,'rejected request recorded as accepted'
            return {'sender':sender,'receiver':receiver,'transport':transport,'status':'passed','scenarios':len(rows),'canonicalExchanges':count,'receiverRejections':negatives,'preuploadedBodies':len(list(store.iterdir()))-1}
        except Exception as error:return {'sender':sender,'receiver':receiver,'transport':transport,'status':'failed','error':str(error)}
        finally:
            if proc:
                proc.terminate()
                try:proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.communicate()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--sender',choices=LANGUAGES);parser.add_argument('--receiver',choices=LANGUAGES);parser.add_argument('--transport',choices=('http','stdio'));parser.add_argument('--output',type=Path,default=HERE/'compaction-wire-matrix-results.json');args=parser.parse_args()
    pairs=[(s,r,t) for t in ('http','stdio') for s in LANGUAGES for r in LANGUAGES if (not args.sender or args.sender==s) and (not args.receiver or args.receiver==r) and (not args.transport or args.transport==t)]
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(run_pair,pairs))
    report={'canonicalMethod':'hooks/intercept','contentUpload':'HTTP raw octets, confirmed before intercept','offline':True,'results':results,'passed':sum(r['status']=='passed' for r in results),'total':len(results)}
    args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'passed':report['passed'],'total':report['total'],'failures':[r for r in results if r['status']!='passed']}));return 0 if report['passed']==report['total'] else 1

if __name__=='__main__':raise SystemExit(main())
