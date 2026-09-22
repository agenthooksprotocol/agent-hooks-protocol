"""Independent wire capture must establish a request, not infer missing secrets."""
from copy import deepcopy
from unittest import TestCase, main
from content_upload import reference, upload
from sender_isolation_matrix import capture_upload, capture_errors


class IsolationEvidenceTests(TestCase):
    def test_actual_anonymous_raw_upload(self):
        body=bytes(range(256))+b'\x00\xff'
        with capture_upload() as (endpoint,captures):
            upload({'endpoint':endpoint,'timeoutMs':1000,'maxBytes':1024},'body',reference('sender-isolation',body),body)
            self.assertEqual(capture_errors(captures,body),[])
            for header in ('authorization','proxy-authorization','cookie'):
                contaminated=deepcopy(captures);contaminated[0]['sensitiveHeaders']=[header]
                self.assertTrue(capture_errors(contaminated,body))
            self.assertTrue(capture_errors(captures,b'wrong body'))
            self.assertTrue(capture_errors(captures*2,body))
    def test_no_request_is_not_credential_isolation(self):
        self.assertTrue(capture_errors([],b'body'))


if __name__=='__main__':main()
