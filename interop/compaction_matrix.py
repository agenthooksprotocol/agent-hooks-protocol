#!/usr/bin/env python3
"""Actual 4x4 SDK sender/receiver matrix over HTTP and NDJSON subprocess pipes.

The non-normative compaction/run method configures a synthetic host. Each receiver
invokes its public SDK compaction runtime; only this driver knows expected state.
No LLM, proxy evaluator, expected result, or success switch goes to the receiver.
"""
import argparse
import json
import os
import secrets
import selectors
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
LANGUAGES = ('typescript', 'python', 'go', 'rust')


def commands():
    return {'typescript': ['node', str(ROOT/'typescript-sdk/interop/compaction.mjs')],
            'python': [str(ROOT/'python-sdk/.venv/bin/python'), str(ROOT/'python-sdk/interop/compaction.py')],
            'go': ['/tmp/ahp-compaction-go'],
            'rust': [str(ROOT/'rust-sdk/target/debug/compaction')]}


def modify(target, value, operation='replace'):
    return {'type': 'modify', 'target': target, 'operation': operation, 'value': value}


def hook(supplier, *effects, policy='fail-closed', throws=False):
    return {'supplier': supplier, 'effects': list(effects), 'failurePolicy': policy, 'throw': throws}


def cases():
    rows = []
    def add(name, before=(), after=(), *, instructions='base', final='summary:base', generated=True,
            applied=True, failures=0, messages=(), observe=False, supplier=None, seen=None, absent=()):
        rows.append({'request': {'jsonrpc': '2.0', 'id': name, 'method': 'compaction/run', 'params': {
            'instructions': 'base', 'itemId': 'logical-summary', 'before': list(before), 'after': list(after), 'observeOnly': observe}},
            'expected': {'instructions': instructions, 'final': final, 'generated': generated, 'applied': applied,
                         'failures': failures, 'messages': list(messages), 'supplier': supplier, 'seen': seen, 'absent': absent}})
    add('unchanged')
    add('before-chain', [hook('edit', modify('instructions','new')), hook('watch')], instructions='new', final='summary:new', seen=[('base',None),('new',None)])
    add('after-chain', after=[hook('redact',modify('summary','clean')),hook('watch')], final='clean', seen=[('base','summary:base'),('base','clean')])
    add('both-boundaries', [hook('edit',modify('instructions','new'))], [hook('redact',modify('summary','clean')),hook('watch')], instructions='new', final='clean', seen=[('base',None),('new','summary:new'),('new','clean')])
    add('supplied-still-after', [hook('cache',{'type':'return','value':'cached'})], [hook('redact',modify('summary','clean')),hook('watch')], generated=False, final='clean', supplier='cache', seen=[('base',None),('base','cached'),('base','clean')])
    add('later-input-invalidates-return', [hook('cache',{'type':'return','value':'stale'}),hook('edit',modify('instructions','new'))], instructions='new',final='summary:new',absent=['stale'])
    add('same-input-keeps-return', [hook('cache',{'type':'return','value':'cached'}),hook('same',modify('instructions','base'))],generated=False,final='cached',supplier='cache')
    add('return-bound-after-edits', [hook('cache',{'type':'return','value':'fresh'},modify('instructions','new'))],instructions='new',generated=False,final='fresh',supplier='cache')
    add('last-return-wins', [hook('one',{'type':'return','value':'old'}),hook('two',{'type':'return','value':'fresh'})],generated=False,final='fresh',supplier='two',absent=['old'])
    add('deny-beats-return', [hook('cache',{'type':'return','value':'cached'}),hook('deny',{'type':'deny','reason':'Host policy'})],generated=False,applied=False,final=None,seen=[('base',None),('base',None)])
    for boundary, wrong in [('before','summary'),('after','instructions')]:
        for policy in ('fail-open','fail-closed'):
            hooks = [hook('invalid', modify(wrong,'leaked'), policy=policy), hook('watch')]
            add(boundary+'-wrong-target-'+policy, before=hooks if boundary=='before' else (), after=hooks if boundary=='after' else (),
                generated=not (boundary=='before' and policy=='fail-closed'), applied=policy=='fail-open',
                final=None if boundary=='before' and policy=='fail-closed' else 'summary:base', failures=1,absent=['leaked'])
    add('before-compound-rollback', [hook('prefix',{'type':'message','text':'kept'}),hook('invalid',modify('instructions','leaked'),{'type':'message','text':'leaked'},modify('summary','bad'),policy='fail-open'),hook('watch')], failures=1,messages=['kept'],absent=['leaked','bad'],seen=[('base',None),('base',None),('base',None)])
    add('after-compound-rollback', after=[hook('prefix',modify('summary','kept')),hook('invalid',modify('summary','leaked'),{'type':'message','text':'leaked'},modify('instructions','bad'),policy='fail-open'),hook('watch')],final='kept',failures=1,absent=['leaked'],seen=[('base','summary:base'),('base','kept'),('base','kept')])
    add('candidate-invalidation-rollback', [hook('cache',{'type':'return','value':'cached'}),hook('invalid',modify('instructions','leaked'),modify('summary','bad'),policy='fail-open')],generated=False,final='cached',supplier='cache',failures=1,absent=['leaked','bad'])
    add('after-fail-closed-prevents-application', after=[hook('invalid',modify('summary','leaked'),modify('instructions','bad')),hook('unreachable')],applied=False,failures=1,absent=['leaked'],seen=[('base','summary:base')])
    add('before-merge-invalid', [hook('invalid',modify('instructions',{},'merge'),policy='fail-open')],failures=1)
    add('after-merge-invalid', after=[hook('invalid',modify('summary',{},'merge'),policy='fail-open')],failures=1)
    add('nontext-invalid', after=[hook('invalid',modify('summary',42),policy='fail-open')],failures=1)
    add('after-return-invalid', after=[hook('invalid',{'type':'return','value':'bad'},policy='fail-open')],failures=1,absent=['bad'])
    add('observe-only-no-modifications', after=[hook('invalid',modify('summary','leaked')),hook('watch')],observe=True,failures=0,absent=['leaked'])
    add('callback-failure-open', [hook('broken',policy='fail-open',throws=True),hook('watch')],failures=1)
    add('callback-failure-closed', [hook('cache',{'type':'return','value':'cached'}),hook('broken',throws=True)],generated=False,applied=False,final=None,failures=1)
    add('unicode-empty', [hook('edit',modify('instructions','世界 😀'))],[hook('clear',modify('summary','')),hook('watch')],instructions='世界 😀',final='',seen=[('base',None),('世界 😀','summary:世界 😀'),('世界 😀','')])
    add('deny-missing-reason', [hook('invalid',{'type':'deny'},policy='fail-open')],failures=1)
    add('deny-empty-reason', [hook('invalid',{'type':'deny','reason':''},policy='fail-open')],failures=1)
    bad=hook('invalid',policy='fail-open');bad['effects']={'type':'deny'}
    add('malformed-response-array',[bad],failures=1)
    return rows


def check(row, response):
    request, expected = row['request'], row['expected']
    assert response.get('jsonrpc') == '2.0' and response.get('id') == request['id'], 'correlation'
    assert 'error' not in response, response.get('error')
    r = response['result']
    for key in ('instructions','generated','applied','messages'):
        assert r[key] == expected[key], (key,r[key],expected[key])
    assert len(r['failures']) == expected['failures'], 'failure count'
    def body(snapshot):
        item = snapshot['summary']
        if item is None: return None
        assert item['id'] == 'logical-summary', 'logical identity changed'
        value = snapshot['bodies'][item['ref']]
        assert item['ref'] == 'urn:ahp:compaction:utf8:' + value.encode().hex(), 'mutable or wrong ref'
        return value
    assert body(r) == expected['final'], ('final',body(r),expected['final'])
    if expected['supplier'] is not None:
        assert r['provenance'] == {'kind':'supplied','supplier':expected['supplier']}, 'supplier erased'
    elif expected['generated']:
        assert r['provenance'] == {'kind':'generated'}, 'generation provenance'
    else:
        assert r['provenance'] is None, 'fabricated execution'
    for forbidden in expected['absent']:
        assert forbidden not in r['bodies'].values(), 'staged bytes leaked'
    observed=[]
    for snapshot in r['seen']:
        observed.append((snapshot['instructions'],body(snapshot)))
        caps=snapshot['capabilities']
        if request['params']['observeOnly'] and snapshot['boundary']=='after':
            assert caps == {'effects':[],'modify':{}}, 'observation advertises control'
        else:
            target='instructions' if snapshot['boundary']=='before' else 'summary'
            assert caps['modify']=={target:{'replace':True,'merge':False}}, 'wrong boundary target advertisement'
        # Earlier immutable references retain their exact bytes after settlement.
        for ref,value in snapshot['bodies'].items():
            assert r['bodies'][ref]==value, 'prior content reference mutated'
    if expected['seen'] is not None:
        assert observed==expected['seen'], ('serial hook snapshots',observed,expected['seen'])


def run_pair(pair):
    sender,receiver,transport=pair
    cmd=commands(); rows=cases(); proc=None
    env=os.environ.copy();env['PYTHONPATH']=str(ROOT/'python-sdk/src');token=secrets.token_urlsafe(24);env['AHP_COMPACTION_TOKEN']=token
    try:
        plan={'transport':transport,'requests':[row['request'] for row in rows]}
        if transport=='stdio':plan['command']=cmd[receiver]
        else:
            proc=subprocess.Popen(cmd[receiver]+['server'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
            selector=selectors.DefaultSelector();selector.register(proc.stdout,selectors.EVENT_READ)
            ready=selector.select(30);selector.close()
            if not ready:raise RuntimeError('receiver startup timeout')
            line=proc.stdout.readline()
            if not line:raise RuntimeError('receiver startup failed: '+proc.stderr.read()[-1000:])
            plan.update(endpoint=json.loads(line)['endpoint'],token=token)
        out=subprocess.run(cmd[sender]+['client'],input=json.dumps(plan),capture_output=True,text=True,env=env,timeout=90)
        if out.returncode:raise RuntimeError(out.stderr[-1500:])
        replies=json.loads(out.stdout)
        assert len(replies)==len(rows), 'response count'
        for row,reply in zip(rows,replies):
            try:check(row,reply)
            except Exception as error:raise AssertionError(row['request']['id']+': '+str(error)) from error
        return {'sender':sender,'receiver':receiver,'transport':transport,'status':'passed','scenarios':len(rows)}
    except Exception as error:
        return {'sender':sender,'receiver':receiver,'transport':transport,'status':'failed','error':str(error)}
    finally:
        if proc:
            proc.terminate()
            try:proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:proc.kill();proc.communicate()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=HERE/'compaction-matrix-results.json')
    parser.add_argument('--sender',choices=LANGUAGES);parser.add_argument('--receiver',choices=LANGUAGES);parser.add_argument('--transport',choices=('http','stdio'))
    args=parser.parse_args()
    pairs=[(s,r,t) for t in ('http','stdio') for s in LANGUAGES for r in LANGUAGES if (not args.sender or args.sender==s) and (not args.receiver or args.receiver==r) and (not args.transport or args.transport==t)]
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(run_pair,pairs))
    report={'offline':True,'syntheticCompactor':True,'results':results,'passed':sum(r['status']=='passed' for r in results),'total':len(results)}
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'passed':report['passed'],'total':report['total'],'failures':[r for r in results if r['status']!='passed']}))
    return 0 if report['passed']==report['total'] else 1

if __name__=='__main__':raise SystemExit(main())
