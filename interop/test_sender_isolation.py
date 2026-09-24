"""Independent wire capture must establish a request, not infer missing secrets."""
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from unittest import TestCase, main
from unittest.mock import patch
import urllib.error
import urllib.request
from content_upload import NoRedirect
from sender_isolation_matrix import capture_upload, capture_errors, run


def send(endpoint, body, headers=None):
    headers = {'Content-Type': 'application/octet-stream',
               'Content-Length': str(len(body)),
               'AHP-Content-SHA256': hashlib.sha256(body).hexdigest()} | (headers or {})
    request = urllib.request.Request(endpoint, data=body, headers=headers, method='POST')
    return urllib.request.build_opener(NoRedirect).open(request, timeout=2)


class IsolationEvidenceTests(TestCase):
    def test_actual_anonymous_raw_upload(self):
        body=bytes(range(256))+b'\x00\xff'
        with capture_upload() as (endpoint,captures):
            with send(endpoint,body) as response:
                self.assertEqual(response.status,201)
                self.assertEqual(response.headers['Content-Type'],'application/json')
                payload=response.read()
                self.assertEqual(int(response.headers['Content-Length']),len(payload))
                descriptor=json.loads(payload)
            self.assertEqual(descriptor,captures[0]['descriptor'])
            self.assertEqual(set(descriptor),{'ref','size','sha256'})
            self.assertEqual(descriptor['size'],len(body))
            self.assertEqual(descriptor['sha256'],hashlib.sha256(body).hexdigest())
            self.assertTrue(descriptor['ref'].startswith('urn:uuid:'))
            self.assertEqual(capture_errors(captures,body),[])
            for field,value in [('sensitiveHeaders',['authorization']),
                                ('sensitiveHeaders',['proxy-authorization']),
                                ('sensitiveHeaders',['cookie']),
                                ('subscription','body'),('ref','caller-ref'),
                                ('transferEncoding','chunked'),('declaredHash','0'*64)]:
                contaminated=deepcopy(captures);contaminated[0][field]=value
                self.assertTrue(capture_errors(contaminated,body),field)
            for field,value in [('ref','caller-ref'),('size',0),('sha256','0'*64)]:
                contaminated=deepcopy(captures);contaminated[0]['descriptor'][field]=value
                self.assertTrue(capture_errors(contaminated,body),field)
            self.assertTrue(capture_errors(captures,b'wrong body'))
            self.assertTrue(capture_errors(captures*2,body))

    def test_capture_rejects_legacy_binding_credentials_and_wrong_integrity(self):
        for headers in [{'AHP-Subscription':'Ym9keQ'}, {'AHP-Content-Ref':'cmVm'},
                        {'Authorization':'Bearer secret'}, {'Proxy-Authorization':'secret'},
                        {'Cookie':'secret'}, {'AHP-Content-SHA256':'0'*64},
                        {'Content-Type':'application/json'}, {'Content-Length':'1048577'},
                        {'Transfer-Encoding':'chunked'}, {'Content-Encoding':'gzip'},
                        {'Content-Length':'+4'}, {'Content-Length':'04'}]:
            with self.subTest(headers=headers), capture_upload() as (endpoint,captures):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    send(endpoint,b'body',headers)
                self.assertEqual(error.exception.code,400)
                error.exception.close()
                self.assertTrue(capture_errors(captures,b'body'))

    def test_empty_upload_has_receiver_assigned_descriptor(self):
        with capture_upload() as (endpoint,captures):
            with send(endpoint,b'') as response:
                self.assertEqual(json.load(response),captures[0]['descriptor'])
            self.assertEqual(capture_errors(captures,b''),[])

    def test_redirect_is_not_followed(self):
        with capture_upload() as (endpoint,captures):
            class Redirect(BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def do_POST(self):
                    self.send_response(307)
                    self.send_header('Location',endpoint)
                    self.send_header('Content-Length','0')
                    self.end_headers()
            server=ThreadingHTTPServer(('127.0.0.1',0),Redirect)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    send('http://127.0.0.1:'+str(server.server_port),b'body')
                self.assertEqual(error.exception.code,307)
                error.exception.close()
                self.assertEqual(captures,[])
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)

    def test_actual_sender_scenario_and_status_are_checked(self):
        def adapter(*args,**kwargs):
            scenario=json.loads(args[4].read_text())['scenarios'][0]
            self.assertNotIn('subscriptionId',scenario['requests']['a']['params'])
            self.assertEqual(scenario['expected']['uploadStatuses'],[201])
            import base64
            body=base64.b64decode(scenario['steps'][0]['bodyBase64'])
            with send(kwargs['upload_override']['endpoint'],body) as response:
                self.assertEqual(response.status,201)
                self.assertNotEqual(json.load(response)['ref'],scenario['steps'][0]['ref'])
            report={'results':[{'actual':deepcopy(scenario['expected'])}]}
            with patch('sender_isolation_matrix.verify',return_value=['event proof checked']) as verify:
                errors=kwargs['verifier']([scenario],report,{},'python',0)
                self.assertEqual(errors,['event proof checked'])
                verify.assert_called_once()
                self.assertEqual(verify.call_args.args[0][0]['requests'],scenario['requests'])
                report['results'][0]['actual']['uploadStatuses']=[204]
                errors=kwargs['verifier']([scenario],report,{},'python',0)
                self.assertIn('anonymous upload not actually confirmed',errors)
            return {'passed':True}
        with patch('sender_isolation_matrix.run_group',side_effect=adapter):
            self.assertTrue(run('python','http','none',{},None,5)['credentialIsolationVerified'])

    def test_no_request_is_not_credential_isolation(self):
        self.assertTrue(capture_errors([],b'body'))


if __name__=='__main__':main()
