"""Verify captured serial-chain wire messages, not helper-returned summaries."""
from collections import Counter
import json
from observation_wire import ObservationValidator


def verify_chains(scenarios, report, receipts, language, exit_code):
    errors=[]
    if exit_code or report.get('language')!=language:errors.append('chain client failed or wrong language')
    results=report.get('results',[])
    if Counter(r.get('id') for r in results)!=Counter(s['id'] for s in scenarios):errors.append('chain result multiplicity')
    if any(r.get('status','passed')!='passed' for r in results):errors.append('chain failed or skipped result')
    by_id={r['id']:r.get('actual') for r in results}
    validator=ObservationValidator()
    for scenario in scenarios:
        ident=scenario['requests']['a']['id']
        event_id=scenario['requests']['a']['params']['event']['id']
        entries=[e for e in receipts['entries'] if e.get('id',e.get('eventId')) in (ident,event_id)]
        if by_id.get(scenario['id'])!=scenario['expected']:errors.append(scenario['id']+': chain local result mismatch')
        if any(e.get('kind') not in ('received','replied','cancelled','chain-settled','observed','observer-blocked') for e in entries):errors.append('unknown chain receipt')
        requests=[e.get('message') for e in entries if e['kind']=='received']
        if requests!=scenario['chainProof']['requests']:errors.append('chain intercept selection/effective request mismatch')
        notes=[e.get('message') for e in entries if e['kind']=='observed']
        expected=scenario['chainProof']['observations']
        def key(note):return json.dumps(note, sort_keys=True)
        if sorted(notes,key=key)!=sorted(expected,key=key):errors.append('chain automatic downgrade/permission/effective view mismatch')
        for receipt in entries:
            if receipt['kind']=='observed':
                params=receipt.get('message',{}).get('params',{})
                if receipt.get('event')!=params.get('event') or receipt.get('eventId')!=params.get('event',{}).get('id'):errors.append('chain receipt differs from exact wire notification')
            if receipt['kind'] in ('cancelled','chain-settled') and receipt.get('scenario')!=scenario['id']:errors.append('chain milestone scenario mismatch')
        for note in notes:errors.extend(validator.errors(note))
        for request in requests:errors.extend(validator.schema_errors(request,'intercept-request'))
        received=[i for i,e in enumerate(entries) if e['kind']=='received']
        replied=[i for i,e in enumerate(entries) if e['kind']=='replied']
        settled=[i for i,e in enumerate(entries) if e['kind']=='chain-settled']
        observed=[i for i,e in enumerate(entries) if e['kind']=='observed']
        cancelled=[i for i,e in enumerate(entries) if e['kind']=='cancelled']
        blocked=[i for i,e in enumerate(entries) if e['kind']=='observer-blocked']
        if scenario['chain'].get('holdObservers'):
            if len(blocked)!=len(expected) or not settled or any(i<=settled[0] for i in blocked):errors.append('missing post-stop blocked observer proof')
        elif blocked:errors.append('unexpected observer gate')
        if len(replied)!=len(received) or any(a>=b for a,b in zip(received,replied)):errors.append('chain response multiplicity/order')
        if len(settled)!=1 or any(i<=settled[0] for i in observed):errors.append('observation before chain settlement')
        if any(replied[i]>=received[i+1] for i in range(min(len(replied),len(received))-1)):errors.append('interceptors were not serial')
        if scenario['chain'].get('interrupt'):
            if len(cancelled)!=1 or not received or not replied or not settled or not received[0]<cancelled[0]<=settled[0]<replied[0]:errors.append('interruption waited for pending request')
        elif cancelled:errors.append('unexpected chain interruption')
        elif settled and replied and replied[-1]>=settled[0]:errors.append('chain settled before response')
    return errors
