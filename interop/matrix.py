#!/usr/bin/env python3
"""Actual SDK matrix: control orchestration only, never applies effects."""
import argparse
import concurrent.futures
from collections import Counter
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from auth import Issuer, configuration

HERE = Path(__file__).resolve().parent
MODES = ('none', 'bearer', 'oauth', 'mtls', 'workload')

def load(path):
    return json.loads(Path(path).read_text())

def write(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    temporary.replace(path)

def control(endpoint, route):
    with urllib.request.urlopen(endpoint + route, timeout=5) as response:
        return json.load(response)

def ready(path, process, timeout):
    deadline = time.monotonic() + timeout
    event = threading.Event()
    while time.monotonic() < deadline:
        if Path(path).exists():
            value = load(path)
            health = control(value['controlEndpoint'], '/health')
            if not isinstance(health, dict) or health.get('ready') is not True:
                raise ValueError('Invalid readiness health')
            return value
        if process.poll() is not None:
            raise RuntimeError('Server exited before readiness')
        event.wait(.02)  # Readiness only; never race-test ordering.
    raise TimeoutError('Readiness watchdog expired')

def kill_group(pid, sig):
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        # Darwin can report EPERM for an already empty process group.
        snapshot = subprocess.run(['ps', '-axo', 'pgid=,stat='], capture_output=True, text=True, check=True, timeout=5)
        if any(line.split()[0] == str(pid) and not line.split()[1].startswith('Z') for line in snapshot.stdout.splitlines() if line.split()):
            raise


def stop(process):
    if process is None:
        return
    kill_group(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    kill_group(process.pid, signal.SIGKILL)
    process.wait(timeout=3)

def equal(a, b):
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    return a == b

# Diagnostics may be additive; executable/visible semantic output may not.
OPTIONAL_SEMANTIC_FIELDS = frozenset(('result','flow','injections',
    'continuationInstructions','continuationRemaining','rejected'))


def receipt_matches(receipt, request):
    """Accept exact actual canonical wire only, optionally with routing metadata."""
    if not isinstance(receipt,dict) or not isinstance(request,dict):return False
    if 'accepted' in receipt and receipt['accepted'] is not True:return False
    if not equal(receipt.get('id'),request.get('id')) or receipt.get('method')!=request.get('method'):return False
    if 'eventId' in receipt and not equal(receipt['eventId'],request.get('params',{}).get('event',{}).get('id')):return False
    message=receipt if 'jsonrpc' in receipt else receipt.get('message')
    return (isinstance(message,dict) and message.get('jsonrpc')=='2.0'
            and equal(message,request))


def verify(scenarios, report, receipts, language, exit_code):
    errors = []
    results = report.get('results')
    if report.get('language') != language or not isinstance(results, list):
        raise ValueError('Malformed adapter report')
    ids = [s['id'] for s in scenarios]
    if Counter(r.get('id') for r in results) != Counter(ids):
        errors.append('Report IDs must match scenarios exactly once')
    requests = receipts.get('requests')
    if not isinstance(requests, list):
        raise ValueError('Malformed receipts')
    if Counter(r.get('id') for r in requests) != Counter(ids):
        errors.append('Receipt IDs must match scenarios exactly once')
    indexed = {s['id']: s for s in scenarios}
    for receipt in requests:
        if 'accepted' in receipt and receipt['accepted'] is not True:
            errors.append('Receipt explicitly not accepted')
        scenario = indexed.get(receipt.get('id'))
        if scenario is None or receipt.get('method') != scenario['request']['method']:
            errors.append('Invalid receipt correlation or method')
        elif not receipt_matches(receipt,scenario['request']):
            errors.append('Receipt must contain exact actual canonical request')
    if exit_code != 0:
        errors.append('Client exited nonzero')
    by_id = {r.get('id'): r for r in results}
    rows = []
    for scenario in scenarios:
        result = by_id.get(scenario['id'], {})
        actual = result.get('actual')
        passed = result.get('status') == 'passed' and 'actual' in result
        expected = scenario.get('expected', {'expectError': True})
        if scenario.get('expectError'):
            # A missing/null output is not evidence of fail-closed rejection.
            passed = passed and equal(actual, {'rejected': True})
        else:
            passed = passed and isinstance(actual, dict) and all(k in actual and equal(v, actual[k]) for k, v in expected.items())
            if isinstance(actual,dict) and any(k in actual and k not in expected for k in OPTIONAL_SEMANTIC_FIELDS):
                passed=False
        rows.append(dict(id=scenario['id'], expected=expected, actual=actual,
                         status='passed' if passed else 'failed', adapterStatus=result.get('status', 'missing')))
    invalidate(rows, errors)
    return rows, sorted(set(errors))

def invalidate(rows, errors):
    # An integrity failure makes the entire combination unverified. Preserve
    # expected/actual and adapterStatus, but never count these as verified passes.
    if errors:
        for row in rows:
            row['status'] = 'failed'


def summarize(groups):
    return dict(groups=dict(Counter(r['status'] for r in groups)),
                scenarios=dict(Counter(s['status'] for r in groups for s in r['results'])))


def relay(config_path):
    """Forward NDJSON unchanged; snapshot receipts before final response delivery.

    The real server stays in the matrix-owned client process group. Capturing
    receipts before forwarding prevents client shutdown racing the snapshot.
    """
    cfg = load(config_path)
    write(cfg['relayIdentity'], {'pgid': os.getpgrp()})
    server = subprocess.Popen(cfg['relayCommand'] + ['--config', config_path], cwd=cfg['relayCwd'],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        info = ready(cfg['readinessFile'], server, cfg['watchdog'])
        count = len(load(cfg['scenarioFile'])['scenarios'])
        received = 0
        for line in sys.stdin.buffer:
            server.stdin.write(line)
            server.stdin.flush()
            response = server.stdout.readline()
            if not response:
                raise RuntimeError('Server closed response stream')
            if json.loads(line).get('method') == 'hooks/intercept':
                received += 1
            if received == count:
                write(cfg['receiptFile'], control(info['controlEndpoint'], '/receipts'))
            sys.stdout.buffer.write(response)
            sys.stdout.buffer.flush()
    finally:
        server.terminate()
        try:
            server.wait(timeout=2)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=2)

def run_group(client, server, scenarios, scenario_file, issuer, timeout):
    output = []
    for transport in ('http', 'stdio'):
        for mode in MODES:
            base = dict(client=client['language'], server=server['language'], transport=transport, auth=mode)
            if transport == 'stdio' and mode != 'none':
                output.append(dict(**base, status='inapplicable', errors=[], results=[dict(id=s['id'], expected=s.get('expected', {'expectError': True}), actual=None, status='inapplicable') for s in scenarios]))
                continue
            processes = []
            with tempfile.TemporaryDirectory(prefix='ahp-matrix-') as temp:
                temp = Path(temp)
                sc = dict(transport=transport, readinessFile=str(temp/'ready.json'), scenarioFile=str(scenario_file),
                          auth=configuration(mode, HERE/'fixtures', 'server', issuer))
                cc = dict(transport=transport, scenarioFile=str(scenario_file), reportFile=str(temp/'report.json'),
                          auth=configuration(mode, HERE/'fixtures', 'client', issuer))
                rows = [dict(id=s['id'], expected=s.get('expected', {'expectError': True}), actual=None, status='failed', adapterStatus='missing') for s in scenarios]
                errors = []
                try:
                    with (temp/'stderr.log').open('wb') as log:
                        if transport == 'http':
                            write(temp/'server.json', sc)
                            process = subprocess.Popen(server['server'] + ['--config', str(temp/'server.json')], cwd=server['cwd'], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                            processes.append(process)
                            cc['endpoint'] = ready(sc['readinessFile'], process, timeout)['endpoint']
                        else:
                            sc.update(relayCommand=server['server'], relayCwd=server['cwd'], watchdog=timeout, receiptFile=str(temp/'receipts.json'), relayIdentity=str(temp/'relay.json'))
                            write(temp/'server.json', sc)
                            cc.update(serverCommand=[sys.executable, str(Path(__file__).resolve()), '--relay'], serverCwd=str(HERE), serverConfig=str(temp/'server.json'))
                        write(temp/'client.json', cc)
                        process = subprocess.Popen(client['client'] + ['--config', str(temp/'client.json')], cwd=client['cwd'], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                        processes.append(process)
                        code = process.wait(timeout=timeout)
                        report = load(cc['reportFile'])
                        try:
                            receipts = control(load(sc['readinessFile'])['controlEndpoint'], '/receipts') if transport == 'http' else load(temp/'receipts.json')
                        except Exception as exc:
                            receipts = {'requests': []}
                            errors.append('Receipts: ' + type(exc).__name__)
                        rows, validation_errors = verify(scenarios, report, receipts, client['language'], code)
                        errors.extend(validation_errors)
                except Exception as exc:
                    # Never copy logs/config credentials to the public artifact.
                    errors.append(type(exc).__name__)
                finally:
                    if (temp/'relay.json').exists():
                        try:
                            pgid = load(temp/'relay.json')['pgid']
                            if pgid not in [p.pid for p in processes]:
                                kill_group(pgid, signal.SIGKILL)
                        except Exception as exc:
                            errors.append('Relay cleanup: ' + type(exc).__name__)
                    for process in reversed(processes):
                        try:
                            stop(process)
                        except Exception as exc:
                            errors.append('Cleanup: ' + type(exc).__name__)
                invalidate(rows, errors)
                status = 'passed' if not errors and all(r['status'] == 'passed' for r in rows) else 'failed'
                output.append(dict(**base, status=status, errors=errors, results=rows))
    return output

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clients', nargs='+')
    parser.add_argument('--servers', nargs='+')
    parser.add_argument('--output', type=Path, default=HERE/'matrix-results.json')
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--relay', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--config', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.relay:
        relay(args.config)
        return 0
    adapters = []
    for path in sorted(HERE.parent.parent.glob('*-sdk/interop/adapter.json')):
        adapters.append(dict(load(path), cwd=str(path.parent.parent)))
    names = {a['language'] for a in adapters}
    for selected in (args.clients, args.servers):
        if selected and not set(selected) <= names:
            parser.error('Unknown language; available: ' + ', '.join(sorted(names)))
    if args.jobs < 1 or args.timeout <= 0 or not adapters:
        parser.error('Positive jobs/timeout and discovered adapters required')
    scenarios = load(HERE/'scenarios.json')['scenarios']
    if len({s['id'] for s in scenarios}) != len(scenarios):
        parser.error('Duplicate scenario IDs')
    with Issuer() as issuer, concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_group, c, s, scenarios, HERE/'scenarios.json', issuer, args.timeout)
                   for c in adapters for s in adapters
                   if (not args.clients or c['language'] in args.clients) and (not args.servers or s['language'] in args.servers)]
        groups = [row for future in futures for row in future.result()]
    groups.sort(key=lambda r: (r['client'], r['server'], r['transport'], r['auth']))
    summary = summarize(groups)
    write(args.output, dict(version=1, scenarioCount=len(scenarios), summary=summary, groups=groups))
    print(json.dumps(summary, sort_keys=True))
    return int(any(r['status'] == 'failed' for r in groups))

if __name__ == '__main__':
    sys.exit(main())
