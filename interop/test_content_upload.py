"""Actual HTTP reference binding regressions, NOT cross-language runtime claims."""
import http.client
import json
import os
from copy import deepcopy
from unittest import TestCase, main
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit
from content_upload import Receiver, serve, reference, upload, deliver, selected_item, post
from matrix import HERE, load


class ContentBindingTests(TestCase):
    def setUp(self):
        self.receiver = Receiver({'Bearer upload': {'body'}, None: {'anonymous'}},
                                 {'Bearer event': {'body'}, None: {'anonymous'}})
        self.endpoint = self.enterContext(serve(self.receiver.handler('/upload', '/observe')))
        self.enterContext(patch.dict(os.environ, {'AHP_TEST_UPLOAD': 'upload'}))
        self.config = {'endpoint': self.endpoint+'/upload', 'timeoutMs': 1000,
                       'maxBytes': 4096, 'auth': {'type': 'bearer', 'tokenEnv': 'AHP_TEST_UPLOAD'}}
        event = deepcopy(load(HERE/'scenarios.json')['scenarios'][0]['request']['params']['event'])
        self.message = {'jsonrpc':'2.0','method':'hooks/observe','params':{
            'protocolVersion':'draft','event':event}}

    def attach(self, body):
        item=selected_item({'id':'binary','kind':'text','mediaType':'application/octet-stream'},
                           {'default':'body'},body,'ref')
        self.message['params']['event']['items']=[item]
        return item['body']

    def status(self, headers, body=b''):
        url=urlsplit(self.endpoint)
        c=http.client.HTTPConnection(url.hostname,url.port,timeout=2)
        try:
            c.putrequest('POST','/upload')
            for k,v in headers: c.putheader(k,v)
            c.endheaders(body); response=c.getresponse(); response.read(); return response.status
        finally: c.close()

    def headers(self, body=b''):
        return [('Content-Type','application/octet-stream'),('Content-Length',str(len(body))),
                ('Authorization','Bearer upload'),('AHP-Content-SHA256',reference('ref',body)['sha256'])]

    def test_binary_upload_confirmed_before_actual_event(self):
        body=bytes(range(256))+b'\x00\xff\xfe'; self.attach(body)
        deliver(self.message,'body',self.config,{'ref':body},self.endpoint+'/observe','event')
        canonical=self.receiver.events[0]['params']['event']['items'][0]['body']
        self.assertEqual(canonical, reference(canonical['ref'],body))
        self.assertNotEqual(canonical['ref'],'ref')
        self.assertEqual(self.receiver.contents[('body',canonical['ref'])],body)
        self.assertEqual([x[0] for x in self.receiver.trace],['upload','event'])
        expected=deepcopy(self.message)
        expected['params']['event']['items'][0]['body']=canonical
        self.assertEqual(self.receiver.events,[expected])
        self.assertEqual(self.message['params']['event']['items'][0]['body']['ref'],'ref')

    def test_empty_body_is_not_missing(self):
        self.attach(b''); deliver(self.message,'body',self.config,{'ref':b''},self.endpoint+'/observe','event')
        self.assertEqual(list(self.receiver.contents.values()),[b''])

    def test_upload_failure_prevents_event(self):
        self.attach(b'x'); os.environ['AHP_TEST_UPLOAD']='event'
        with self.assertRaises(HTTPError) as error:
            deliver(self.message,'body',self.config,{'ref':b'x'},self.endpoint+'/observe','event')
        self.assertEqual(error.exception.code,401); self.assertEqual(self.receiver.events,[])

    def test_anonymous_upload_does_not_inherit_event_auth(self):
        self.attach(b'x'); self.config.pop('auth')
        with self.assertRaises(HTTPError) as error:
            deliver(self.message,'body',self.config,{'ref':b'x'},self.endpoint+'/observe','event')
        self.assertEqual(error.exception.code,404)
        self.assertEqual(list(self.receiver.contents.values()),[b'x'])
        self.assertEqual(next(iter(self.receiver.contents))[0],'anonymous')
        self.assertEqual(self.receiver.events,[])

    def test_explicit_anonymous_authorization(self):
        self.config.pop('auth'); canonical=upload(self.config,'ignored-local-label',reference('ref',b'x'),b'x')
        self.assertEqual(self.receiver.contents[('anonymous',canonical['ref'])],b'x')

    def test_repeated_uploads_preserve_immutable_receiver_refs(self):
        descriptors=[upload(self.config,'ignored',reference('ref',body),body) for body in (b'a',b'a',b'b')]
        self.assertEqual(len({d['ref'] for d in descriptors}),3)
        for descriptor,body in zip(descriptors,(b'a',b'a',b'b')):
            self.assertEqual(descriptor,reference(descriptor['ref'],body))
            self.assertEqual(self.receiver.contents[('body',descriptor['ref'])],body)

    def test_duplicate_auth_and_framing_rejected(self):
        for extra in [('Authorization','Bearer upload'),('Content-Length','0'),('Transfer-Encoding','chunked'),('Content-Encoding','gzip')]:
            with self.subTest(extra=extra): self.assertEqual(self.status(self.headers()+[extra]),400)
        self.assertEqual(self.receiver.contents,{})

    def test_caller_identity_headers_are_rejected(self):
        for header in ('AHP-Subscription','AHP-Content-Ref'):
            with self.subTest(header=header):
                self.assertEqual(self.status(self.headers()+[(header,'forged')]),400)
        self.assertEqual(self.receiver.contents,{})

    def test_credential_scope_cannot_resolve_another_scope(self):
        canonical=upload(self.config,'anonymous',reference('ref',b'x'),b'x')
        self.attach(b'x').update(canonical)
        with self.assertRaises(HTTPError) as error:
            post(self.endpoint+'/observe',json.dumps(self.message).encode(),{'Content-Type':'application/json'})
        self.assertEqual(error.exception.code,404)
        self.assertEqual(self.receiver.events,[])

    def test_upload_auth_does_not_authorize_event(self):
        self.attach(b'x')
        with self.assertRaises(HTTPError) as error:
            deliver(self.message,'body',self.config,{'ref':b'x'},self.endpoint+'/observe','upload')
        self.assertEqual(error.exception.code,401)
        self.assertEqual(list(self.receiver.contents.values()),[b'x'])
        self.assertEqual(self.receiver.events,[])

    def test_size_limit_never_truncates(self):
        self.config['maxBytes']=1
        with self.assertRaises(ValueError):upload(self.config,'body',reference('ref',b'long'),b'long')
        self.assertEqual(self.receiver.contents,{})

    def test_hash_mismatch_never_commits(self):
        headers=[(k,'0'*64 if k=='AHP-Content-SHA256' else v) for k,v in self.headers(b'x')]
        self.assertEqual(self.status(headers,b'x'),400); self.assertEqual(self.receiver.contents,{})

    def test_upload_redirect_never_forwards_credentials_or_bytes(self):
        from http.server import BaseHTTPRequestHandler
        target=self.endpoint+'/upload'
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(307); self.send_header('Location',target)
                self.send_header('Content-Length','0'); self.end_headers()
        with serve(Redirect) as endpoint:
            self.config['endpoint']=endpoint+'/redirect'
            with self.assertRaises(HTTPError) as error:upload(self.config,'body',reference('ref',b'x'),b'x')
            self.assertEqual(error.exception.code,307)
        self.assertEqual(self.receiver.contents,{})

    def test_invalid_confirmation_prevents_event(self):
        from http.server import BaseHTTPRequestHandler
        good=reference('receiver-assigned',b'x')
        cases=[(204,'application/json',b''),
               (200,'application/json',json.dumps(good).encode()),
               (201,'text/plain',json.dumps(good).encode()),
               (201,'application/json',b'not json'),
               (201,'application/json',json.dumps({**good,'size':2}).encode()),
               (201,'application/json',json.dumps({**good,'sha256':'0'*64}).encode()),
               (201,'application/json',json.dumps({**good,'extra':True}).encode()),
               (201,'application/json',json.dumps({**good,'ref':''}).encode())]
        for status,media,payload in cases:
            with self.subTest(status=status,media=media,payload=payload):
                class Confirmation(BaseHTTPRequestHandler):
                    def log_message(self,*args): pass
                    def do_POST(self):
                        self.rfile.read(int(self.headers['Content-Length']))
                        self.send_response(status)
                        self.send_header('Content-Type',media)
                        self.send_header('Content-Length',str(len(payload)))
                        self.end_headers(); self.wfile.write(payload)
                with serve(Confirmation) as endpoint:
                    self.config['endpoint']=endpoint+'/upload'
                    self.attach(b'x')
                    with self.assertRaises(ValueError):
                        deliver(self.message,'body',self.config,{'ref':b'x'},self.endpoint+'/observe','event')
        self.assertEqual(self.receiver.events,[])

    def test_event_descriptor_must_match_stored_bytes(self):
        canonical=upload(self.config,'body',reference('ref',b'x'),b'x')
        for changed in ({'size':2},{'sha256':'0'*64}):
            self.attach(b'x').update({**canonical,**changed})
            with self.assertRaises(HTTPError) as error:
                post(self.endpoint+'/observe',json.dumps(self.message).encode(),
                     {'Content-Type':'application/json','Authorization':'Bearer event'})
            self.assertEqual(error.exception.code,400)
        self.assertEqual(self.receiver.contents[('body',canonical['ref'])],b'x')
        self.assertEqual(self.receiver.events,[])

    def test_local_integrity_mismatch_prevents_upload(self):
        for changed in ({'size':2},{'sha256':'0'*64}):
            with self.assertRaises(ValueError):
                upload(self.config,'body',{**reference('ref',b'x'),**changed},b'x')
        self.assertEqual(self.receiver.contents,{})

    def test_projection_removes_bodies_before_delivery(self):
        descriptor={'id':'reasoning','kind':'reasoning','mediaType':'text/plain','category':'text'}
        for choice in ('metadata','omit'):
            item=selected_item(descriptor,{'default':'body','reasoning':choice},b'secret','ref')
            self.assertNotIn('body',item); self.assertEqual(item['selection'],choice)
            self.message['params']['event']['items']=[item]
            deliver(self.message,'body',self.config,{},self.endpoint+'/observe','event')
        item=selected_item(descriptor,{'default':'body'},b'secret','ref',authorized=False)
        self.assertNotIn('body',item); self.assertEqual(item['gap']['reason'],'unauthorized')
        self.message['params']['event']['items']=[item]
        deliver(self.message,'body',self.config,{},self.endpoint+'/observe','event')
        self.assertEqual(self.receiver.contents,{})
        self.assertEqual([entry[0] for entry in self.receiver.trace],['event']*3)


if __name__ == '__main__': main()
