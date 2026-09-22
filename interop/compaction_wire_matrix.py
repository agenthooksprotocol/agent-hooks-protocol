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


def require(condition, detail='wire validation failed'):
    if not condition:
        raise AssertionError(detail)


def wire_equal(actual, expected):
    """Compare JSON values without treating booleans as integers."""
    if isinstance(actual, bool) != isinstance(expected, bool):
        return False
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(wire_equal(actual[key], value) for key, value in expected.items())
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(wire_equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def check(outputs,receipts,expected):
    require(wire_equal(len(outputs), len(expected)), 'case count')
    wire_count=0;immutable={}
    for out in outputs:
        name=out['name'];want=expected[name];result=out['result'];trace=out['trace']
        for key in ('instructions','generated','applied'):
            require(wire_equal(result[key], want[key]), (name, key, result[key], want[key]))
        require(wire_equal(len(result['failures']), want['failures']), (name, 'failure policy'))
        require(wire_equal(result['messages'], []), (name, 'staged message leak'))
        actual=result['bodies'][result['summary']['ref']] if result['summary'] is not None else None
        require(wire_equal(actual, want['final']), (name, 'summary', actual, want['final']))
        require(wire_equal(out['downstream'], [want['final']] if want['applied'] else []), (name, 'downstream application'))
        require('leaked' not in result['bodies'].values(), (name, 'staged content leak'))
        require(wire_equal(len(trace), len(want['seen'])), (name, 'missing wire hooks', len(trace), len(want['seen'])))
        serial=[]
        for entry in trace:
            wire_count+=1;request=entry['request'];reply=entry['response'];event=request['params']['event'];boundary=event['type'].split('.')[-1]
            require(wire_equal(request['method'], 'hooks/intercept') and (wire_equal(request['id'], event['id']) and wire_equal(event['id'], name + ':' + boundary)))
            require(wire_equal(reply['jsonrpc'], '2.0') and wire_equal(reply['id'], request['id']) and wire_equal(reply['result']['protocolVersion'], 'draft'))
            matches=[r for r in receipts if wire_equal(r['subscription'], entry['subscription'])]
            require(wire_equal(len(matches), 1), (name, 'receiver receipt count', entry['subscription']))
            receipt=matches[0]
            require(wire_equal(receipt['request'], request) and wire_equal(receipt['response'], reply), (name, 'receiver wire evidence'))
            target='instructions' if boundary=='before' else 'summary';item=event[target];body=receipt['bodies'][item['id']]
            serial.append(body)
            require(wire_equal(item['selection'], 'body') and wire_equal(item['mediaType'], 'text/plain'))
            raw=body.encode();ref=item['body'];require(wire_equal(ref['size'], len(raw)) and wire_equal(ref['sha256'], hashlib.sha256(raw).hexdigest()))
            require(isinstance(ref['ref'], str) and bool(ref['ref']), 'invalid opaque ref')
            key=(entry['subscription'],ref['ref'])
            require(key not in immutable or immutable[key]==raw, 'receiver ref mutated')
            immutable[key]=raw
            require('subscriptionId' not in request['params'], 'wire subscription identity')
            require(wire_equal(item['id'], name + ':' + target), (name, 'logical item ID'))
            require(wire_equal(request['params']['capabilities']['modify'], {target: {'replace': True, 'merge': False}}))
            if boundary=='after':
                require(wire_equal(event['parentEventId'], name + ':before'))
                require(wire_equal(event['removed'], [{'id': name + ':context'}]))
                if not want['generated']:
                    require(wire_equal(event['execution'], {'status': 'skipped', 'reason': 'supplied_result'}))
                else:require(wire_equal(event['execution'], {'status': 'executed'}))
        require(wire_equal(serial, want['seen']), (name, 'serial wire bytes', serial, want['seen']))
    require(wire_equal(len(receipts), wire_count), 'unmatched receiver receipts')
    return wire_count


def probe(sender,receiver,transport,endpoint,token,receiver_command,outputs,env):
    # Native sender transmits invalid canonical requests directly. They must not
    # be rejected only by a sender-side validator or accepted as generic JSON.
    first=outputs[0]['trace'][0];valid=first['request'];sub=first['subscription'];requests=[]
    bad=copy.deepcopy(valid);bad['params']['event'].pop('trigger');requests.append(bad)
    bad=copy.deepcopy(valid);bad['params']['capabilities']['modify']={'summary':{'replace':True,'merge':False}};requests.append(bad)
    bad=copy.deepcopy(valid);bad['params']['event']['instructions']['body']['ref']='urn:not-uploaded';requests.append(bad)
    bad=copy.deepcopy(valid);bad['params']['event']['instructions']['body']['sha256']='0'*64;requests.append(bad)
    plan={'transport':transport,'requests':requests,'endpoint':endpoint+'/hooks/intercept','token':token,
          'command':receiver_command+['stdio',sub]}
    # Original native transport clients append a trailing stdio argument; the
    # receiver consumes its fixed startup config and subscription arguments only.
    out=subprocess.run(transport_commands()[sender]+['client'],input=json.dumps(plan),capture_output=True,text=True,env=env,timeout=60)
    if out.returncode:raise RuntimeError('negative sender: '+out.stderr[-1000:])
    replies=json.loads(out.stdout)
    require(wire_equal(len(replies), 4))
    for reply in replies:require(wire_equal(reply.get('error', {}).get('code'), -32602), ('receiver accepted invalid request', reply))
    return len(replies)


def run_pair(pair):
    sender,receiver,transport=pair;rows,config,expected=cases();cmd=commands();proc=None
    env=os.environ.copy();env['PYTHONPATH']=str(ROOT/'python-sdk/src')
    # Each credential selects exactly one receiver scope; scope labels stay local.
    credentials={scope:{'token':secrets.token_urlsafe(24),'uploadToken':secrets.token_urlsafe(24)} for scope in config}
    env['AHP_COMPACTION_TOKENS']=json.dumps({value['token']:scope for scope,value in credentials.items()})
    env['AHP_COMPACTION_UPLOAD_TOKENS']=json.dumps({value['uploadToken']:scope for scope,value in credentials.items()})
    with tempfile.TemporaryDirectory(prefix='ahp-compaction-wire-') as directory, tempfile.TemporaryFile() as receiver_stderr:
        store=Path(directory)/'bodies';store.mkdir();config_path=Path(directory)/'subscriptions.json';config_path.write_text(json.dumps(config))
        receiver_command=cmd[receiver]+[str(SCHEMA),str(store),str(config_path)]
        try:
            proc=subprocess.Popen(receiver_command+['server'],stdout=subprocess.PIPE,stderr=receiver_stderr,text=True,env=env)
            selector=selectors.DefaultSelector();selector.register(proc.stdout,selectors.EVENT_READ);ready=selector.select(45);selector.close()
            if not ready:raise RuntimeError('receiver startup timeout')
            line=proc.stdout.readline()
            if not line:
                end=receiver_stderr.seek(0,os.SEEK_END)
                receiver_stderr.seek(max(0,end-1500))
                raise RuntimeError('receiver startup failed: '+receiver_stderr.read(1500).decode('utf-8',errors='replace'))
            endpoint=json.loads(line)['endpoint']
            plan={'cases':rows,'schema':str(SCHEMA),'transport':transport,'endpoint':endpoint,'credentials':credentials,'receiverCommand':receiver_command}
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
            negatives=probe(sender,receiver,transport,endpoint,credentials[outputs[0]['trace'][0]['subscription']]['token'],receiver_command,outputs,env)
            require(wire_equal(len(receipt_path.read_text().splitlines()), count), 'rejected request recorded as accepted')
            return {'sender':sender,'receiver':receiver,'transport':transport,'status':'passed','scenarios':len(rows),'canonicalExchanges':count,'receiverRejections':negatives,'preuploadedBodies':len(list(store.iterdir()))-1,'referencedBodies':len({(entry['subscription'],entry['request']['params']['event']['instructions' if entry['request']['params']['event']['type'].endswith('.before') else 'summary']['body']['ref']) for out in outputs for entry in out['trace']})}
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
