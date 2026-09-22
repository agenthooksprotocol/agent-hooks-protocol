#!/usr/bin/env python3
"""Central deterministic fixtures. No lifecycle evaluator lives in the runner."""
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
HERE = Path(__file__).resolve().parent
BASE = json.loads((HERE / 'scenarios.json').read_text())['scenarios'][0]['request']

def actual(**kw):
    return dict(published=[], cancelled=[], ignored=[], states={}, observations=[], uploadStatuses=[], **{}) | kw

def scenario(name, effects=None, keys=('a',)):
    requests, responses = {}, {}
    for k in keys:
        r = deepcopy(BASE)
        r['id'] = r['params']['event']['id'] = name + ':' + k
        r['params']['event']['tool']['input'] = {'value': 'original'}
        r['params']['capabilities'] = {'effects': ['allow','deny','ask','modify','message','return','flow','inject'], 'modify': {'input': {'replace': True,'merge':True}}, 'flow': {'operations':['stop']}, 'inject': {'context': {'append':True,'deliverAt':['now','next_turn']}}}
        requests[k] = r
        responses[k] = {'jsonrpc':'2.0','id':r['id'],'result':{'protocolVersion':'draft','effects':deepcopy(effects or [])}}
    return {'id':name,'requests':requests,'responses':responses,'steps':[],'expected':actual()}

def op(op, **kw): return {'op':op, **kw}
def start(k='a',slot=None): return [op('send',key=k,slot=slot or k),op('wait',key=k,count=1)]
def finish(k='a',slot=None): return [op('release',key=k),op('receive',slot=slot or k),op('accept',key=k)]
def rid(s,k='a'): return s['requests'][k]['id']
def state(label=None): return {'decision':'allow','executed':True,'input':{'value': 'settled' if label is None else label},'messages':['published' if label is None else 'published-'+label]}
def distinguish(s, key, label):
    s['responses'][key]['result']['effects'][0]['value']={'value':label}
    s['responses'][key]['result']['effects'][1]['text']='published-'+label
EFFECTS=[{'type':'modify','target':'input','operation':'replace','value':{'value':'settled'}},{'type':'message','text':'published'},{'type':'allow'}]
ALL=EFFECTS+[{'type':'return','value':{'supplied':True}},{'type':'flow','operation':'continue','instruction':'continue staged'},{'type':'inject','target':'context','operation':'append','deliverAt':'now','items':[{'id':'injected','kind':'text','mediaType':'text/plain','selection':'metadata'}]}]
# Injection effect's canonical shape is taken from the established shared suite.
core=json.loads((HERE/'scenarios.json').read_text())['scenarios']
inject=next(e for s in core for e in ((s.get('response') or {}).get('result') or {}).get('effects',[]) if isinstance(e,dict) and e.get('type')=='inject')
ALL[-1]=deepcopy(inject)
scenarios=[]
for name,after in [('cancel-before-reply',False),('cancel-after-reply-before-acceptance',True)]:
    s=scenario(name,ALL); s['steps']=start()
    if after: s['steps'] += [op('release',key='a'),op('receive',slot='a')]
    s['steps'] += [op('cancel',key='a'),op('failOpen',key='a'),op('accept',key='a')]
    if not after: s['steps'] += [op('release',key='a'),op('receive',slot='a'),op('accept',key='a')]
    s['expected']=actual(cancelled=[rid(s)],ignored=[] if after else ['a']); scenarios.append(s)
s=scenario('late-old-while-next-pending',EFFECTS,('a','b')); distinguish(s,'b','next'); s['steps']=start()+[op('cancel',key='a')]+start('b')+[op('release',key='a'),op('receive',slot='a'),op('accept',key='b')]+finish('b')+[op('failOpen',key='a')]; s['expected']=actual(cancelled=[rid(s)],ignored=['a'],published=[rid(s,'b')],states={rid(s,'b'):state('next')}); scenarios.append(s)
s=scenario('duplicate-reply-ignored',EFFECTS); s['steps']=start()+finish()+[op('send',key='a',slot='duplicate'),op('wait',key='a',count=2),op('receive',slot='duplicate'),op('accept',key='a')]; s['expected']=actual(published=[rid(s)],ignored=['duplicate'],states={rid(s):state()}); scenarios.append(s)
s=scenario('request-specific-acceptance',EFFECTS,('a','b')); distinguish(s,'a','first'); distinguish(s,'b','second'); s['steps']=start()+start('b')+[op('release',key='a'),op('receive',slot='a'),op('accept',key='b'),op('accept',key='a')]+finish('b'); s['expected']=actual(published=[rid(s),rid(s,'b')],states={rid(s):state('first'),rid(s,'b'):state('second')}); scenarios.append(s)
s=scenario('two-staged-reverse-acceptance',EFFECTS,('a','b')); distinguish(s,'a','first'); distinguish(s,'b','second'); s['steps']=start()+start('b')+[op('release',key='b'),op('receive',slot='b'),op('release',key='a'),op('receive',slot='a'),op('accept',key='a'),op('accept',key='b')]; s['expected']=actual(published=[rid(s),rid(s,'b')],states={rid(s):state('first'),rid(s,'b'):state('second')}); scenarios.append(s)
s=scenario('first-staged-response-wins',EFFECTS); second=deepcopy(s['responses']['a']); second['result']['effects'][0]['value']={'value':'MUST-NOT-REPLACE'}; second['result']['effects'][1]['text']='MUST-NOT-PUBLISH'; s['responseSequences']={'a':[deepcopy(s['responses']['a']),second]}; s['steps']=start()+[op('release',key='a'),op('receive',slot='a'),op('send',key='a',slot='duplicate'),op('wait',key='a',count=2),op('receive',slot='duplicate'),op('accept',key='a')]; s['expected']=actual(published=[rid(s)],ignored=['duplicate'],states={rid(s):state()}); scenarios.append(s)
s=scenario('interrupt-after-publication',EFFECTS); s['steps']=start()+finish()+[op('cancel',key='a'),op('accept',key='a'),op('failOpen',key='a'),op('observe',key='a',subscription='metadata')]; s['expected']=actual(published=[rid(s)],cancelled=[rid(s)],states={rid(s):state()},observations=[{'eventId':rid(s),'subscription':'metadata','input':{'value':'settled'}}]); scenarios.append(s)
s=scenario('settled-observer-effects-ignored',EFFECTS); s['steps']=start()+finish()+[op('observe',key='a',subscription='body'),op('observe',key='a',subscription='metadata'),op('accept',key='a')]; s['expected']=actual(published=[rid(s)],states={rid(s):state()},observations=[{'eventId':rid(s),'subscription':x,'input':{'value':'settled'}} for x in ['body','metadata']]); scenarios.append(s)
s=scenario('denied-boundary-observed',[{'type':'deny','reason':'blocked'}]); s['steps']=start()+finish()+[op('observe',key='a',subscription='metadata')]; s['expected']=actual(published=[rid(s)],states={rid(s):{'decision':'deny','executed':False,'input':{'value':'original'},'messages':[]}},observations=[{'eventId':rid(s),'subscription':'metadata','input':{'value':'original'}}]); scenarios.append(s)
s=scenario('cancelled-boundary-observed',ALL); s['steps']=start()+[op('cancel',key='a'),op('observe',key='a',subscription='metadata'),op('release',key='a'),op('receive',slot='a'),op('accept',key='a'),op('failOpen',key='a')]; s['expected']=actual(cancelled=[rid(s)],ignored=['a'],observations=[{'eventId':rid(s),'subscription':'metadata','input':{'value':'original'}}]); scenarios.append(s)
for name,effects,accepted in [
    ('stopped-boundary-observed',[{'type':'flow','operation':'stop','reason':'Stop this boundary'}],{'decision':'allow','executed':False,'flow':'stop','input':{'value':'original'},'messages':[]}),
    ('ask-boundary-observed',[{'type':'ask','reason':'confirm'}],{'decision':'ask','executed':False,'input':{'value':'original'},'messages':[]})]:
    s=scenario(name,effects); s['steps']=start()+finish()+[op('observe',key='a',subscription='metadata')]
    s['expected']=actual(published=[rid(s)],states={rid(s):accepted},observations=[{'eventId':rid(s),'subscription':'metadata','input':{'value':'original'}}]); scenarios.append(s)
def upload(ref,text,subscription='body',**kw):
    body=text.encode() if isinstance(text,str) else text
    return op('upload',subscription=subscription,ref=ref,bodyBase64=base64.b64encode(body).decode('ascii'),size=len(body),sha256=hashlib.sha256(body).hexdigest()) | kw
def item(ref,text): return {'id':'logical-item','kind':'text','mediaType':'text/plain','selection':'body','body':{k:v for k,v in upload(ref,text).items() if k in ('ref','size','sha256')}}
s=scenario('immutable-upload-and-subscription-views',EFFECTS); ref='immutable-v1'; body=item(ref,'original bytes'); gap={'id':'logical-item','kind':'text','mediaType':'text/plain','selection':'body','gap':{'reason':'content permission denied','path':'items.logical-item'}}
s['steps']=[upload(ref,'original bytes'),upload('immutable-retry','original bytes'),upload('immutable-changed','changed bytes'),upload('immutable-v2','changed bytes'),upload(ref,'original bytes','metadata')]+start()+finish()+[op('observe',key='a',subscription='body',items=[body]),op('observe',key='a',subscription='metadata',items=[gap])]; s['expected']=actual(published=[rid(s)],states={rid(s):state()},uploadStatuses=[201,201,201,201,403],observations=[{'eventId':rid(s),'subscription':x,'input':{'value':'settled'}} for x in ['body','metadata']]); scenarios.append(s)
s=scenario('upload-integrity-rejection'); s['steps']=[upload('bad-size','body',size=99),upload('bad-hash','body',sha256='0'*64),upload('unauthorized','secret','metadata')]; s['expected']=actual(uploadStatuses=[400,400,403]); scenarios.append(s)
s=scenario('changed-body-new-reference',EFFECTS); s['steps']=[upload('changed-new-ref','new bytes')]+start()+finish()+[op('observe',key='a',subscription='body',items=[item('changed-new-ref','new bytes')])]; s['expected']=actual(published=[rid(s)],states={rid(s):state()},uploadStatuses=[201],observations=[{'eventId':rid(s),'subscription':'body','input':{'value':'settled'}}]); scenarios.append(s)
s=scenario('normal-content-kinds',EFFECTS); items=[]
for kind in ['reasoning','skill','native']:
    ref='normal-'+kind; s['steps'].append(upload(ref,kind+' content')); i=item(ref,kind+' content'); i.update(id=kind+'-item',kind=kind); items.append(i)
s['steps']+=start()+finish()+[op('observe',key='a',subscription='body',items=items)]; s['expected']=actual(published=[rid(s)],states={rid(s):state()},uploadStatuses=[201]*3,observations=[{'eventId':rid(s),'subscription':'body','input':{'value':'settled'}}]); scenarios.append(s)
s=scenario('arbitrary-binary-and-empty-content',EFFECTS)
raw=bytes(range(256))+b'\x00\xff\xfe'
s['steps']=[upload('binary-ref',raw),upload('empty-ref',b'')]+start()+finish()+[op('observe',key='a',subscription='body',items=[dict(item('binary-ref',raw),id='binary',mediaType='application/octet-stream'),dict(item('empty-ref',b''),id='empty')])]
s['expected']=actual(published=[rid(s)],states={rid(s):state()},uploadStatuses=[201,201],observations=[{'eventId':rid(s),'subscription':'body','input':{'value':'settled'}}]); scenarios.append(s)
s=scenario('intercept-content-upload-before-send',EFFECTS)
s['requests']['a']['params']['event']['items']=[item('intercept-body',b'\xff\x00intercept')]
s['steps']=[upload('intercept-body',b'\xff\x00intercept')]+start()+finish()
s['expected']=actual(published=[rid(s)],states={rid(s):state()},uploadStatuses=[201]); scenarios.append(s)
s=scenario('stdio-unsolicited-old-before-live',EFFECTS,('a','b')); s['transports']=['stdio']; distinguish(s,'a','first'); distinguish(s,'b','second'); stray=deepcopy(s['responses']['a']); stray['result']['effects'][0]['value']={'value':'stale-must-not-replace'}; s['steps']=start()+finish()+start('b')+[op('emit',response=stray)]+finish('b'); s['expected']=actual(published=[rid(s),rid(s,'b')],ignored=['unsolicited:'+rid(s)],states={rid(s):state('first'),rid(s,'b'):state('second')}); scenarios.append(s)
s=scenario('stdio-unknown-before-live',EFFECTS); s['transports']=['stdio']; stray=deepcopy(s['responses']['a']); stray['id']='unknown:'+s['id']; s['steps']=start()+[op('emit',response=stray)]+finish(); s['expected']=actual(published=[rid(s)],ignored=['unsolicited:'+stray['id']],states={rid(s):state()}); scenarios.append(s)
from generate_observation_chains import scenarios as observation_chains
scenarios.extend(observation_chains(BASE))
if __name__=='__main__':
    from observation_wire import ObservationValidator
    validator=ObservationValidator()
    for s in scenarios:
        for field,name in [('requests','intercept-request'),('responses','intercept-response')]:
            for message in s[field].values():
                errors=validator.schema_errors(message,name)
                if errors:raise ValueError(s['id']+': '+str(errors))
    (HERE/'lifecycle-scenarios.json').write_text(json.dumps({'version':1,'scenarios':scenarios},indent=2)+'\n')
    (HERE/'observation-chain-scenarios.json').write_text(json.dumps({'version':1,'scenarios':[s for s in scenarios if 'chain' in s]},indent=2)+'\n')
    print(f'{len(scenarios)} lifecycle scenarios')
