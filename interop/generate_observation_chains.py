"""Authored chain cases: expected wire data is independent of adapter decisions."""
from copy import deepcopy


def scenarios(base):
    rows = []
    modify = {'type':'modify','target':'input','operation':'replace','value':{'value':'settled'}}
    deny = {'type':'deny','reason':'policy'}
    stop = {'type':'flow','operation':'stop','reason':'stop'}
    invalid = {'type':'return','value':{'unadvertised':True}}
    for case in ['deny','stop','deny-stop','deny-no-explicit','fail-open','fail-closed','interrupt','continue','native']:
        ident = 'observation-chain-' + case
        original = deepcopy(base)
        original['id'] = original['params']['event']['id'] = ident
        original['params']['event']['tool']['input'] = {'value':'original'}
        original['params']['event']['items'] = [{'id':ident+':item','kind':'text','mediaType':'text/plain','selection':'metadata'}]
        original['params']['capabilities'] = {'effects':['allow','deny','modify','flow'],'modify':{'input':{'replace':True,'merge':True}},'flow':{'operations':['stop']}}
        subs = [{'id':ident+':'+key,'backend':'shared' if key in ('a','audit') else key,'mode':'observe' if key=='audit' else 'intercept','failurePolicy':'fail-closed' if case=='fail-closed' else 'fail-open','content':'omit' if key=='b' else 'metadata'} for key in ['a','b','c','audit']]
        if case=='deny-no-explicit':subs=subs[:-1]
        if case=='native':subs=subs[-1:]
        effects = [modify]+([deny,stop] if case=='deny-stop' else [stop] if case=='stop' else [deny])
        calls, final, failures = 1, 'settled', []
        sequence = [effects]
        if case in ('fail-open','fail-closed'):
            sequence = [[invalid],[modify,deny]]
            calls,final,failures = (2,'settled',[subs[0]['id']]) if case=='fail-open' else (1,'original',[subs[0]['id']])
        elif case=='interrupt': final='original'
        elif case=='continue': calls=3; sequence=[[modify,{'type':'allow'}],[],[deny]]
        elif case=='native':calls=0;final='original'
        responses=[{'jsonrpc':'2.0','id':ident,'result':{'protocolVersion':'draft','effects':value}} for value in sequence]
        called=subs[:calls]
        remaining=subs[calls:]
        messages=[]
        for i,sub in enumerate(called):
            request=deepcopy(original)
            request['params']['event']['tool']['input']={'value':'settled' if i>0 and case!='fail-open' else 'original'}
            if sub['content']=='omit':request['params']['event']['items']=[]
            messages.append(request)
        observations=[]
        for sub in remaining:
            event=deepcopy(original['params']['event']);event['tool']['input']={'value':final}
            if sub['content']=='omit':event['items']=[]
            observations.append({'jsonrpc':'2.0','method':'hooks/observe','params':{'protocolVersion':'draft','event':event}})
        rows.append({'id':ident,'chain':{'subscriptions':subs,'interrupt':case=='interrupt','holdObservers':case=='interrupt'},'requests':{'a':original},'responses':{'a':responses[-1]},'responseSequences':{'a':responses},'steps':[],
          'expected':{'called':[s['id'] for s in called],'failures':failures,'observations':[s['id'] for s in remaining],'input':{'value':final}},'chainProof':{'requests':messages,'observations':observations}})
    return rows
