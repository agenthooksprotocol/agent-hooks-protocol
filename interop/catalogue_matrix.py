#!/usr/bin/env python3
"""Actual SDK-to-SDK catalogue/lineage/registration grid; no central evaluator."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path
from auth import Issuer
from matrix import MODES, equal, load, write
from lifecycle_matrix import HERE, ROOT, LANGUAGES, discover, run_group
from observation_wire import ObservationValidator
from generate_catalogue_scenarios import EXECUTION, LINEAGE


def verify(scenarios,report,receipts,language,exit_code=0):
    errors=[]
    if exit_code!=0:errors.append('client exit '+str(exit_code))
    if not isinstance(report,dict) or report.get('language')!=language:return errors+['wrong report language/type']
    results=report.get('results')
    if not isinstance(results,list) or any(not isinstance(r,dict) for r in results):return errors+['malformed results']
    if Counter(r.get('id') for r in results)!=Counter(s['id'] for s in scenarios):errors.append('result IDs must match exactly once')
    indexed={r.get('id'):r for r in results}
    for scenario in scenarios:
        result=indexed.get(scenario['id'],{})
        if result.get('status','passed')!='passed':errors.append(scenario['id']+': not passed')
        expected={k:v for k,v in scenario['expected'].items() if k!='deliveries'}
        if not equal(result.get('actual'),expected):errors.append(scenario['id']+': actual wire/registration result mismatch')
    entries=receipts.get('entries') if isinstance(receipts,dict) else None
    if not isinstance(entries,list) or any(not isinstance(e,dict) for e in entries):return errors+['missing receiver receipts']
    validator=ObservationValidator()
    discoveries=[e for e in entries if e.get('kind')=='discovery']
    if len(discoveries)!=1:errors.append('exactly one real discovery required')
    else:
        discovery=discoveries[0]
        request=discovery.get('request'); response=discovery.get('response')
        errors.extend(validator.schema_errors(request,'capabilities-request'))
        errors.extend(validator.schema_errors(response,'capabilities-response'))
        if not equal(response,report.get('discovery')):errors.append('client discovery differs from actual response')
        if not isinstance(request,dict) or not isinstance(response,dict) or request.get('id')!=response.get('id'):errors.append('discovery correlation mismatch')
        if isinstance(response,dict):
            manifest=response.get('result',{}).get('manifest',{})
            supported={e.get('event'):e for e in manifest.get('events',[])}
            if any(kind not in supported or 'observe' not in supported[kind].get('modes',[]) for kind in EXECUTION+LINEAGE):errors.append('catalogue observation support not advertised')
            if 'hook.failure' in supported:errors.append('catalogue fixture requires hook.failure unsupported')
            if manifest.get('managedPolicy',{}).get('scopes')!=['user','project']:errors.append('catalogue fixture requires user/project scopes only')
    expected=[]
    for scenario in scenarios:
        for message,delivery in zip(scenario['expected']['sent'],scenario['expected']['deliveries']):
            params=message['params'];event=params['event']
            if delivery['accepted']:
                expected.append({'kind':'observed','eventId':event['id'],'event':event,'message':message})
                errors.extend(validator.errors(message))
            else:
                expected.append({'kind':'rejected','eventId':event['id'],'message':message,'errorKind':delivery['errorKind']})
    received=[e for e in entries if e.get('kind')!='discovery']
    if not equal(received,expected):errors.append('exact actual receiver delivery/rejection evidence mismatch')
    return errors


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=ROOT)
    p.add_argument('--scenarios',type=Path,default=HERE/'catalogue-scenarios.json')
    p.add_argument('--client',choices=LANGUAGES);p.add_argument('--server',choices=LANGUAGES)
    p.add_argument('--transport',choices=('http','stdio'));p.add_argument('--auth',choices=MODES)
    p.add_argument('--workers',type=int,default=4);p.add_argument('--timeout',type=int,default=90)
    p.add_argument('--output',type=Path,default=HERE/'catalogue-matrix-results.json')
    args=p.parse_args();adapters=discover(args.root.resolve())
    groups=[(c,s,t,a) for c in ([args.client] if args.client else LANGUAGES)
            for s in ([args.server] if args.server else LANGUAGES)
            for t in ([args.transport] if args.transport else ('http','stdio'))
            for a in ([args.auth] if args.auth else MODES) if t=='http' or a=='none']
    if not groups:p.error('no applicable groups')
    results=[]
    with Issuer() as issuer, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(run_group,c,s,t,adapters,args.scenarios.resolve(),args.timeout,a,issuer,'catalogue',verify) for c,s,t,a in groups]
        for future in as_completed(futures):
            row=future.result();results.append(row)
            print(('PASS ' if row['passed'] else 'FAIL ')+row['group']+' '+ '; '.join(row['errors'][:2]),flush=True)
    scenarios=load(args.scenarios)['scenarios'];passed=sum(r['passed'] for r in results)
    categories=Counter(s['category'] for s in scenarios)
    summary={'groups':len(results),'passed':passed,'failed':len(results)-passed,'scenarioCount':len(scenarios),
             'scenarioExecutions':sum(r['scenarios'] for r in results if r['passed']),
             'categoryScenarios':dict(categories),'categoryVerifiedExecutions':{k:v*passed for k,v in categories.items()},
             'scope':'Synthetic SDK wire validation, source-local lineage and registration enforcement; not native harness occurrence',
             'results':sorted(results,key=lambda r:r['group'])}
    write(args.output,summary);print(f'{passed}/{len(results)} groups; {summary["scenarioExecutions"]} verified executions')
    return int(passed!=len(results))


if __name__=='__main__':raise SystemExit(main())
