#!/usr/bin/env python3
"""Real SDK senders: event auth present, upload auth absent, independent sink.

This is sender credential isolation, not a second semantic evaluator. The event
receiver is each SDK's real lifecycle server; upload capture is a distinct origin.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import re
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
from auth import Issuer
from matrix import MODES, load, write
from lifecycle_matrix import HERE, ROOT, LANGUAGES, discover, run_group, verify


@contextmanager
def capture_upload():
    captures=[];lock=threading.Lock()
    class Capture(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            self.connection.settimeout(5)
            try:
                required=('Content-Length','Content-Type','AHP-Content-SHA256')
                if any(len(self.headers.get_all(name,[]))!=1 for name in required):
                    raise ValueError('missing/duplicate framing')
                if self.headers.get_all('Content-Encoding') or self.headers.get_all('Transfer-Encoding'):
                    raise ValueError('unsupported encoding')
                if not re.fullmatch(r'0|[1-9][0-9]*',self.headers['Content-Length']):
                    raise ValueError('noncanonical length')
                length=int(self.headers['Content-Length'])
                if not 0<=length<=1048576:raise ValueError('size')
                body=self.rfile.read(length)
                if len(body)!=length:raise ValueError('framing')
                entry={'path':self.path,'size':len(body),'sha256':hashlib.sha256(body).hexdigest(),
                       'subscription':self.headers.get('AHP-Subscription'),
                       'ref':self.headers.get('AHP-Content-Ref'),
                       'declaredHash':self.headers.get('AHP-Content-SHA256'),
                       'contentType':self.headers.get('Content-Type'),
                       'sensitiveHeaders':sorted(name.lower() for name in self.headers.keys()
                          if name.lower() in ('authorization','proxy-authorization','cookie')),
                       'transferEncoding':self.headers.get('Transfer-Encoding')}
                with lock:captures.append(entry)
                if (entry['subscription'] is not None or entry['ref'] is not None
                        or entry['sensitiveHeaders'] or entry['transferEncoding'] is not None
                        or entry['contentType'] != 'application/octet-stream'
                        or entry['declaredHash'] != entry['sha256']):
                    raise ValueError('invalid anonymous raw upload')
                descriptor={'ref':'urn:uuid:'+str(uuid.uuid4()),
                            'size':entry['size'],'sha256':entry['sha256']}
                entry['descriptor']=descriptor
                payload=json.dumps(descriptor,separators=(',',':')).encode()
                status=201
            except (ValueError,OSError):status=400;payload=b''
            self.send_response(status)
            if status==201:self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(payload)));self.end_headers()
            self.wfile.write(payload)
    server=ThreadingHTTPServer(('127.0.0.1',0),Capture)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield 'http://127.0.0.1:'+str(server.server_port)+'/capture',captures
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
        if thread.is_alive():raise RuntimeError('capture cleanup timeout')


def capture_errors(captures,body):
    if len(captures)!=1:return ['exactly one independent upload capture required']
    entry=captures[0];descriptor=entry.get('descriptor',{})
    digest=hashlib.sha256(body).hexdigest()
    expected={'path':'/capture','size':len(body),'sha256':digest,
              'subscription':None,'ref':None,'declaredHash':digest,
              'contentType':'application/octet-stream','sensitiveHeaders':[],'transferEncoding':None,
              'descriptor':descriptor}
    errors=[]
    if entry!=expected:errors.append('independent capture contains credentials or incorrect raw upload')
    ref=descriptor.get('ref') if isinstance(descriptor,dict) else None
    if (not isinstance(ref,str) or not ref.startswith('urn:uuid:')
            or descriptor!={'ref':ref,'size':len(body),'sha256':digest}):
        errors.append('missing or incorrect receiver-assigned canonical descriptor')
    return errors


def run(language,transport,mode,adapters,issuer,timeout):
    body=bytes(range(256))+b'\x00\xffsender-isolation'
    scenario=deepcopy(next(s for s in load(HERE/'lifecycle-scenarios.json')['scenarios'] if s['id']=='intercept-content-upload-before-send'))
    # The separate upload origin cannot supply the event receiver's content store.
    # This probe tests credential isolation, not cross-origin reference resolution.
    request=scenario['requests']['a'];request['params']['event'].pop('items',None)
    request['params'].pop('subscriptionId',None)
    scenario['expected']['uploadStatuses']=[201]
    step=scenario['steps'][0]
    step.update(ref='sender-isolation',bodyBase64=base64.b64encode(body).decode(),size=len(body),sha256=hashlib.sha256(body).hexdigest())
    with tempfile.TemporaryDirectory(prefix='ahp-sender-isolation-') as directory, capture_upload() as (endpoint,captures):
        path=Path(directory)/'scenarios.json';write(path,{'version':1,'scenarios':[scenario]})
        def check(scenarios,report,receipts,client,code):
            errors=capture_errors(captures,body)
            if not isinstance(report,dict) or not isinstance(report.get('results'),list):return errors+['missing client result']
            for result in report['results']:
                if result.get('actual',{}).get('uploadStatuses')!=[201]:errors.append('anonymous upload not actually confirmed')
            # Validate actual event execution/capture with the original lifecycle
            # checker. Upload evidence is independently checked above, not injected
            # or synthesized into the SDK receiver's receipt log.
            event_scenarios=deepcopy(scenarios);event_report=deepcopy(report)
            for item in event_scenarios:
                item['steps']=[st for st in item['steps'] if st['op']!='upload'];item['expected']['uploadStatuses']=[]
            for result in event_report['results']:result.setdefault('actual',{})['uploadStatuses']=[]
            return errors+verify(event_scenarios,event_report,receipts,client,code)
        result=run_group(language,language,transport,adapters,path,timeout,mode,issuer,
                         verifier=check,upload_override={'endpoint':endpoint,'timeoutMs':5000,'maxBytes':1048576},receiver_checks=False)
        result['independentUploads']=len(captures)
        result['credentialIsolationVerified']=result['passed'] and not capture_errors(captures,body)
        return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workers',type=int,default=4)
    p.add_argument('--timeout',type=int,default=90);p.add_argument('--language',choices=LANGUAGES)
    p.add_argument('--output',type=Path,default=HERE/'sender-isolation-results.json');args=p.parse_args()
    adapters=discover(ROOT);groups=[(lang,t,a) for lang in ([args.language] if args.language else LANGUAGES)
                                  for t in ('http','stdio') for a in MODES if t=='http' or a=='none']
    rows=[]
    with Issuer() as issuer, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(run,*g,adapters,issuer,args.timeout) for g in groups]
        for future in as_completed(futures):
            result=future.result();rows.append(result);print(('PASS ' if result['passed'] else 'FAIL ')+result['group']+' '+ '; '.join(result['errors'][:2]),flush=True)
    summary={'groups':len(rows),'passed':sum(r['passed'] for r in rows),
             'independentUploads':sum(r['independentUploads'] for r in rows),
             'verifiedCredentialIsolation':sum(r['credentialIsolationVerified'] for r in rows),
             'scope':'Four actual SDK senders, independent HTTP upload origin with auth absent; five HTTP event auth modes and stdio process trust. Does not prove HTTPS client-certificate noninheritance.',
             'results':sorted(rows,key=lambda r:r['group'])}
    write(args.output,summary);print(f"{summary['passed']}/{summary['groups']} sender isolation groups")
    return int(summary['passed']!=summary['groups'])


if __name__=='__main__':raise SystemExit(main())
