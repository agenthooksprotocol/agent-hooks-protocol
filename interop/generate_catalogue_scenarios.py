#!/usr/bin/env python3
"""Wire fixtures, never a language-runtime evaluator or native truth oracle."""
from copy import deepcopy
import json
from pathlib import Path
from observation_wire import ObservationValidator

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
EXECUTION=('tool.before','tool.after','turn.start','turn.finish.before','turn.end','turn.progress',
 'model.request.before','model.response.after','model.error','model.switch.before','model.switch.after',
 'tool.permission.request','tool.permission.resolved','tool.progress','tool.batch.after',
 'context.compact.before','context.compact.after')
LINEAGE=('task.change.before','task.change.after','workspace.change.before','workspace.change.after','file.changed')


def namespace_items(event,identity):
    """Standalone examples are distinct logical content, not one item changing role."""
    identities={}
    def collect(value):
        if isinstance(value,dict):
            if all(k in value for k in ('id','kind','mediaType')):
                identities[value['id']]=identity+':item:'+value['id']
            for child in value.values():collect(child)
        elif isinstance(value,list):
            for child in value:collect(child)
    def replace(value):
        if isinstance(value,dict):
            for key,child in value.items():
                if key in ('id','parentItemId') and isinstance(child,str) and child in identities:
                    value[key]=identities[child]
                else:replace(child)
        elif isinstance(value,list):
            for child in value:replace(child)
    collect(event);replace(event)


def notification(kind,identity,parent=None,source='urn:ahp:catalogue'):
    if kind=='tool.before':
        event=deepcopy(json.loads((HERE/'scenarios.json').read_text())['scenarios'][0]['request']['params']['event'])
    elif kind=='tool.after':
        event=deepcopy(json.loads((ROOT/'fixtures/draft/http/observe-tool-after.valid.json').read_text())['params']['event'])
    else:
        event=deepcopy(json.loads((ROOT/'fixtures/draft/http'/('catalogue-'+kind+'.valid.json')).read_text())['params']['event'])
    namespace_items(event,identity)
    event.update(id=identity,source=source)
    if parent is not None:event['parentEventId']=parent
    return {'jsonrpc':'2.0','method':'hooks/observe','params':{'protocolVersion':'draft','event':event}}


def build():
    rows=[]
    def wire(identity,messages,category='execution',rejection=None):
        rows.append({'id':identity,'category':category,'steps':[{'op':'rawNotify' if rejection else 'notify','message':m} for m in messages],
                     'expected':{'sent':messages,'registrations':[],
                                 'deliveries':[{'accepted':rejection is None,'errorKind':rejection} for _ in messages]}})
    for kind in EXECUTION+LINEAGE:wire('catalogue:'+kind,[notification(kind,'catalogue:'+kind)],'execution' if kind in EXECUTION else 'lineage')
    before=notification('task.change.before','task:proposal')
    before['params']['event']['task'].update(operation='update',change={'status':'done'})
    after=notification('task.change.after','task:actual','task:proposal')
    after['params']['event']['task'].update(operation='update',prior={'status':'open'},change={'status':'blocked'})
    wire('task-modified-actual-pair',[before,after],'lineage')
    workspace=notification('workspace.change.before','workspace:proposal')
    actual=notification('workspace.change.after','workspace:actual','workspace:proposal')
    actual['params']['event']['workspace']['change']={'cwd':'/actually-applied'}
    file=notification('file.changed','file:actual','workspace:actual')
    wire('workspace-file-causal-chain',[workspace,actual,file],'lineage')
    wire('filtered-parent',[notification('task.change.after','filtered:after','not-delivered')],'lineage')
    wire('source-local-parent',[notification('task.change.before','same',source='urn:source:a'),
                              notification('task.change.after','other','same',source='urn:source:b')],'lineage')
    wire('late-parent-first-child',[notification('task.change.after','late:child','late:parent'),
                                    notification('task.change.before','late:parent')],'lineage')
    wrong=notification('task.change.after','wrong:task','task:proposal'); wrong['params']['event']['task']['id']='another-task'
    wire('wrong-known-task-parent',[wrong],'lineage','lineage')
    cycle=notification('task.change.before','cycle:self','cycle:self')
    wire('source-local-cycle',[cycle],'lineage','lineage')
    noop=notification('task.change.after','noop:actual'); noop['params']['event']['task'].update(operation='update',prior={'status':'open'},change={'status':'open'})
    wire('no-op-actual-task',[noop],'lineage','lineage')
    invalid=notification('model.error','invalid:model-error'); invalid['params']['event'].pop('error')
    wire('typed-model-error-required',[invalid],'execution','schema')
    invalid=notification('tool.after','invalid:execution'); invalid['params']['event']['execution']={'status':'invented'}
    wire('typed-execution-enum',[invalid],'execution','schema')
    # A healthy event after all receiver negatives proves continued processing.
    wire('healthy-after-rejections',[notification('turn.end','healthy:after-rejections')])
    registration={'protocolVersion':'draft','hooks':[{'id':'com.example.catalogue','transport':{
        'type':'http','url':'https://policy.example.invalid/hooks'},'subscriptions':[{'events':['tool.after'],'mode':'observe','content':{'default':'metadata'}}]}]}
    def register(identity,value=None,requirements=None,context=None,accepted=True):
        rows.append({'id':identity,'category':'registration','steps':[{'op':'register',
                     'registration':deepcopy(value or registration),'requirements':requirements or [],
                     'context':context or {'interactive':True,'environment':{}}}],
                     'expected':{'sent':[],'registrations':[{'accepted':accepted}],'deliveries':[]}})
    register('registration-observe-accepted')
    intercept=deepcopy(registration);intercept['hooks'][0]['subscriptions']=[{'events':['tool.before'],'mode':'intercept','timeoutMs':500,'failurePolicy':'fail-closed','content':{'default':'metadata'}}]
    register('registration-intercept-accepted',intercept)
    duplicate=deepcopy(registration);duplicate['hooks'].append(deepcopy(duplicate['hooks'][0]))
    register('registration-duplicate-backend',duplicate,accepted=False)
    missing=deepcopy(intercept);missing['hooks'][0]['subscriptions'][0].pop('failurePolicy')
    register('registration-explicit-failure-policy-required',missing,accepted=False)
    observer=deepcopy(registration);observer['hooks'][0]['subscriptions'][0]['timeoutMs']=100
    register('registration-observe-policy-forbidden',observer,accepted=False)
    unsupported=deepcopy(registration);unsupported['hooks'][0]['subscriptions'][0]['events']=['hook.failure']
    register('registration-unsupported-event',unsupported,accepted=False)
    observe_only=deepcopy(intercept);observe_only['hooks'][0]['subscriptions'][0]['events']=['model.error']
    register('registration-observe-only-interception',observe_only,accepted=False)
    register('registration-unenforceable-ask',intercept,[{'event':'tool.before','mode':'intercept','effects':['ask']}],{'interactive':False,'environment':{}},False)
    register('registration-supported-modify',intercept,[{'event':'tool.before','mode':'intercept','effects':['modify'],'modify':{'input':{'merge':True}}}])
    register('registration-unsupported-modify-target',intercept,[{'event':'tool.before','mode':'intercept','effects':['modify'],'modify':{'nonexistent':{'replace':True}}}],accepted=False)
    bearer=deepcopy(registration);bearer['hooks'][0]['authentication']={'type':'bearer','tokenEnv':'CATALOGUE_TOKEN'}
    register('registration-resolved-credential',bearer,context={'interactive':True,'environment':{'CATALOGUE_TOKEN':'TEST-ONLY-catalogue'}})
    register('registration-missing-credential',bearer,accepted=False)
    managed=deepcopy(intercept);managed['hooks'][0]['subscriptions'][0].update(scope='managed',disableable=False)
    register('registration-unenforceable-managed-scope',managed,accepted=False)
    # Preserve existing adapter scenario-loader shape without inventing intercepts.
    for row in rows:row.update(requests={},responses={})
    return {'version':1,'suite':'catalogue','scenarios':rows}


def validate(document):
    validator=ObservationValidator()
    for scenario in document['scenarios']:
        for step in scenario['steps']:
            if step['op']=='register' and scenario['expected']['registrations'][0]['accepted']:
                errors=validator.schema_errors(step['registration'],'registration')
                if errors:raise ValueError(scenario['id']+': '+str(errors))
            if step['op']=='notify':
                errors=validator.errors(step['message'])
                if errors:raise ValueError(scenario['id']+': '+str(errors))
    return document


if __name__=='__main__':
    doc=validate(build());(HERE/'catalogue-scenarios.json').write_text(json.dumps(doc,indent=2)+'\n')
    print(str(len(doc['scenarios']))+' catalogue/lineage/registration scenarios')
