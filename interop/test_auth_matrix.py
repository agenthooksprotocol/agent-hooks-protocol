#!/usr/bin/env python3
"""Real-wire auth matrix; run from any cwd. No credentials/log bodies in reports."""
import argparse
import base64
from contextlib import contextmanager
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import time
import unittest
import io
from jsonschema import Draft202012Validator
from urllib.parse import urlencode, urlsplit
import auth
from matrix import equal, stop, receipt_matches

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FIXTURES = HERE / 'fixtures'
LANGUAGES = ('python', 'go', 'rust', 'typescript')
MODES = ('none', 'bearer', 'oauth', 'workload', 'mtls')


CAPABILITIES = Draft202012Validator(json.loads(
    (HERE.parent / 'schema/draft/capabilities.schema.json').read_text()))


def valid_capabilities(value):
    return CAPABILITIES.is_valid(value)


def valid_receipts(before, after, request, intercept):
    if not isinstance(before, list) or not isinstance(after, list):
        return False
    if not intercept:
        return equal(before, after)
    if len(after) != len(before) + 1 or not equal(after[:-1], before):
        return False
    return receipt_matches(after[-1],request)


def certificate_evidence(before, after):
    """A single sequential probe must have one new certificate rejection event.

    A generic tlsClientError/reset is not proof. Only the adapter's certificate
    classification plus the matching actual event/code deltas can qualify EOF.
    """
    fields = ('tlsClientErrors', 'tlsRejections')
    if any(type(h.get(k)) is not int for h in (before, after) for k in fields):
        return False
    if any(after[k] - before[k] != 1 for k in fields):
        return False
    for key in ('tlsClientErrorCodes', 'tlsRejectionCodes'):
        old, new = before.get(key), after.get(key)
        if not isinstance(old, dict) or not isinstance(new, dict):
            return False
        if any(type(v) is not int for v in list(old.values()) + list(new.values())):
            return False
        delta = {k: new.get(k, 0) - old.get(k, 0) for k in old.keys() | new.keys()}
        if sum(delta.values()) != 1 or any(v < 0 for v in delta.values()):
            return False
        if key == 'tlsRejectionCodes':
            codes = [k for k, v in delta.items() if v == 1]
            if len(codes) != 1 or not any(part in codes[0] for part in
                    ('CERT', 'SELF_SIGNED', 'UNABLE_TO_VERIFY', 'UNKNOWN_CA')):
                return False
    return True


def wait_ready(path, process, timeout):
    # Shared stop() retained. Shared ready() currently rejects additive health
    # metrics; this local version permits the established TS TLS evidence fields.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            value = json.loads(path.read_text())
            if control(value['controlEndpoint'], '/health').get('ready') is not True:
                raise ValueError('Invalid readiness health')
            return value
        if process.poll() is not None:
            raise RuntimeError('Server exited before readiness')
        time.sleep(.02)
    raise TimeoutError('Readiness watchdog expired')


def exchange(endpoint, path, headers=(), payload=None, context=None, form=False):
    url = urlsplit(endpoint)
    cls = http.client.HTTPSConnection if url.scheme == 'https' else http.client.HTTPConnection
    kwargs = {'context': context} if url.scheme == 'https' else {}
    connection = cls(url.hostname, url.port, timeout=5, **kwargs)
    try:
        connection.putrequest('POST' if payload is not None else 'GET', path)
        # Separate raw fields, not a dictionary or comma-joined header.
        for key, value in headers:
            connection.putheader(key, value)
        if payload is not None:
            connection.putheader('Content-Length', str(len(payload)))
            connection.putheader('Content-Type', 'application/x-www-form-urlencoded' if form else 'application/json')
        connection.endheaders(payload)
        response = connection.getresponse()
        data = response.read(1048577)
        if len(data) > 1048576:
            raise ValueError('oversized response')
        return response.status, data
    finally:
        connection.close()


def control(endpoint, path):
    status, data = exchange(endpoint, path)
    if status != 200:
        raise ValueError('control failure')
    return json.loads(data)


def tls(identity=None):
    context = ssl.create_default_context(cafile=str(FIXTURES / 'ca.pem'))
    # Shared legacy fixture omits AKI; retain chain/hostname/expiry verification.
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    if identity:
        context.load_cert_chain(FIXTURES / f'{identity}.pem', FIXTURES / f'{identity}-key.pem')
    return context


@contextmanager
def server(language, mode):
    sdk = ROOT / f'{language}-sdk'
    manifest = json.loads((sdk / 'interop/adapter.json').read_text())
    with tempfile.TemporaryDirectory(prefix='ahp-negative-auth-') as directory:
        directory = Path(directory)
        ready, config = directory / 'ready.json', directory / 'config.json'
        config.write_text(json.dumps(dict(transport='http', readinessFile=str(ready),
            scenarioFile=str(HERE / 'scenarios.json'), auth=auth.configuration(mode, FIXTURES, 'server'))))
        config.chmod(0o600)
        with (directory / 'server.log').open('wb') as log:
            process = subprocess.Popen(manifest['server'] + ['--config', str(config)], cwd=sdk,
                stdout=log, stderr=log, start_new_session=True)
            try:
                endpoints = wait_ready(ready, process, 90)
                yield endpoints
            finally:
                stop(process)



def bearer(value):
    return [('Authorization', 'Bearer ' + value)]


def changed_token(mode, **claims):
    head, body, _ = auth.token(mode).split('.')
    value = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
    value.update(claims)
    body = base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).rstrip(b'=').decode()
    message = head + '.' + body
    mac = hmac.new(auth.signing_key(mode).encode(), message.encode(), hashlib.sha256).digest()
    return message + '.' + base64.urlsafe_b64encode(mac).rstrip(b'=').decode()


def positive_token(mode):
    if mode == 'bearer':
        return auth.BEARER
    if mode == 'workload':
        return auth.token(mode)
    with auth.Issuer() as issuer:
        form = urlencode(dict(grant_type='client_credentials', client_id=auth.CLIENT_ID,
            client_secret=auth.CLIENT_SECRET, audience=auth.AUDIENCE)).encode()
        status, data = exchange(issuer.endpoint, '/token', payload=form, form=True)
        result = json.loads(data)
        if status != 200 or issuer.issued != 1 or result['token_type'] != 'Bearer':
            raise RuntimeError('OAuth issuance failed')
        return result['access_token']


def run(language, mode, row):
    results = []
    with server(language, mode) as endpoints:
        api, ctl = endpoints['endpoint'], endpoints['controlEndpoint']
        good_context = tls('client') if mode == 'mtls' else None
        good = bearer(positive_token(mode)) if mode in ('bearer', 'oauth', 'workload') else []
        negatives = []
        if good:
            negatives = [('missing', [], None), ('wrong', bearer('TEST-ONLY-wrong'), None)]
            if mode in ('oauth', 'workload'):
                for name, claims in [('issuer', {'iss': 'urn:wrong'}), ('audience', {'aud': 'urn:wrong'}),
                        ('purpose', {'purpose': 'wrong'}), ('expired', {'exp': auth.CLOCK - 1})]:
                    negatives.append(('jwt-' + name, bearer(changed_token(mode, **claims)), None))
                head, body, mac = auth.token(mode).split('.')
                mac = ('A' if mac[0] != 'A' else 'B') + mac[1:]
                negatives.extend([
                    ('jwt-signature', bearer(head + '.' + body + '.' + mac), None),
                    ('cross-purpose', bearer(auth.token('workload' if mode == 'oauth' else 'oauth')), None),
                    ('static-bearer-no-downgrade', bearer(auth.BEARER), None)])
            negatives.extend([
                ('duplicate-valid-valid', good + good, None),
                ('duplicate-valid-wrong', good + bearer('TEST-ONLY-wrong'), None),
                ('duplicate-wrong-valid', bearer('TEST-ONLY-wrong') + good, None)])
        elif mode == 'mtls':
            negatives = [('missing-client-certificate', [], tls()),
                         ('untrusted-client-certificate', [], tls('untrusted-client'))]
        cases = [(name, headers, context, True) for name, headers, context in negatives]
        cases.append(('positive-after-negatives', good, good_context, False))
        if mode == 'none':
            # Missing/wrong credentials are not negative cases for no-auth mode.
            cases.append(('none-ignores-wrong-credentials', bearer('TEST-ONLY-wrong'), None, False))
        for name, headers, context, negative in cases:
            for path in ('/capabilities', '/intercept'):
                checks, observed = [], None
                before, after = None, None
                tls_evidence = None
                def check(label, value):
                    checks.append({'assertion': label, 'passed': bool(value)})
                try:
                    before = control(ctl, '/receipts')['requests']
                    health_before = control(ctl, '/health')
                    payload = json.dumps(row['request']).encode() if path == '/intercept' else None
                    try:
                        status, data = exchange(api, path, headers, payload, context)
                        observed = f'HTTP {status}'
                        if negative:
                            check('explicit auth rejection (401/403)', status in (401, 403))
                        else:
                            check('positive HTTP 200', status == 200)
                            value = json.loads(data)
                            check('valid positive response', equal(value, row['response']) if path == '/intercept'
                                  else valid_capabilities(value))
                    except (ssl.SSLError, ConnectionResetError) as error:
                        reason = getattr(error, 'reason', '') or ''
                        observed = 'TLS ' + (reason or type(error).__name__)
                        alert = reason in (
                            'TLSV13_ALERT_CERTIFICATE_REQUIRED', 'TLSV1_ALERT_UNKNOWN_CA',
                            'SSLV3_ALERT_BAD_CERTIFICATE', 'SSLV3_ALERT_HANDSHAKE_FAILURE',
                            'TLSV1_ALERT_ACCESS_DENIED', 'SSLV3_ALERT_CERTIFICATE_UNKNOWN',
                            'TLSV1_ALERT_DECRYPT_ERROR')
                        health_after = control(ctl, '/health')
                        proved_eof = (isinstance(error, (ssl.SSLEOFError, ConnectionResetError)) and language == 'typescript'
                                      and certificate_evidence(health_before, health_after))
                        if proved_eof:
                            tls_evidence = {label: {k: health[k] for k in (
                                'tlsClientErrors', 'tlsClientErrorCodes', 'tlsRejections', 'tlsRejectionCodes')}
                                for label, health in (('before', health_before), ('after', health_after))}
                        check('explicit TLS alert or correlated server certificate rejection',
                              negative and mode == 'mtls' and (alert or proved_eof))
                    after = control(ctl, '/receipts')['requests']
                    check('server remains healthy', control(ctl, '/health').get('ready') is True)
                    if negative:
                        check('zero accepted receipts after negative', after == [])
                    else:
                        check('positive receipt delta and canonical identity/envelope',
                              valid_receipts(before, after, row['request'], path == '/intercept'))
                except Exception as error:
                    check('no infrastructure/transport failure: ' + type(error).__name__, False)
                results.append(dict(language=language, mode=mode, case=name, endpoint=path,
                    negative=negative, observed=observed, tlsEvidence=tls_evidence,
                    receiptsBefore=len(before) if before is not None else None,
                    receiptsAfter=len(after) if after is not None else None, status='passed' if all(c['passed'] for c in checks) else 'failed',
                    checks=checks))
    return results


class PositiveControlTests(unittest.TestCase):
    assertions = 0

    def check(self, value):
        type(self).assertions += 1
        self.assertTrue(value)

    def setUp(self):
        self.request = json.loads((HERE / 'scenarios.json').read_text())['scenarios'][0]['request']
        self.receipt = {'id': self.request['id'], 'method': self.request['method'], 'message': self.request}
        self.before = {'tlsClientErrors': 0, 'tlsRejections': 0,
                       'tlsClientErrorCodes': {}, 'tlsRejectionCodes': {}}
        self.after = {'tlsClientErrors': 1, 'tlsRejections': 1,
                      'tlsClientErrorCodes': {'ERR_SSL_INVALID_PADDING': 1},
                      'tlsRejectionCodes': {'CERT_SIGNATURE_FAILURE': 1}}

    def test_canonical_capabilities_positive(self):
        self.check(valid_capabilities({'effects': []}))
        self.check(valid_capabilities({'effects': ['deny', 'vendor.effect']}))

    def test_numeric_effect_rejected(self):
        self.check(not valid_capabilities({'effects': [123]}))

    def test_missing_and_duplicate_effects_rejected(self):
        self.check(not valid_capabilities({}))
        self.check(not valid_capabilities({'effects': ['deny', 'deny']}))

    def test_invalid_modify_rejected(self):
        self.check(not valid_capabilities({'effects': [], 'modify': {'input': {'replace': 123}}}))

    def test_receipt_metadata_and_full_envelope_positive(self):
        self.check(valid_receipts([], [self.receipt], self.request, True))
        self.check(valid_receipts([], [self.request], self.request, True))
        self.check(valid_receipts([], [dict(self.receipt, accepted=True)], self.request, True))

    def test_metadata_only_receipt_rejected(self):
        self.check(not valid_receipts([], [{'id':self.request['id'],'method':self.request['method']}],self.request,True))
    def test_changed_actual_message_rejected(self):
        message=json.loads(json.dumps(self.request));message['params']['event']['source']='urn:forged'
        self.check(not valid_receipts([], [dict(self.receipt,message=message)],self.request,True))
    def test_nonboolean_accepted_rejected(self):
        for value in (None,1,'true',False):self.check(not valid_receipts([], [dict(self.receipt,accepted=value)],self.request,True))

    def test_receipt_wrong_id_and_method_rejected(self):
        for field in ('id', 'method'):
            self.check(not valid_receipts([], [dict(self.receipt, **{field: 'wrong'})], self.request, True))

    def test_receipt_missing_identity_rejected(self):
        self.check(not valid_receipts([], [{}], self.request, True))

    def test_receipt_explicitly_unaccepted_rejected(self):
        self.check(not valid_receipts([], [dict(self.receipt, accepted=False)], self.request, True))

    def test_receipt_full_envelope_mutation_rejected(self):
        bad = dict(self.request, params={})
        self.check(not valid_receipts([], [bad], self.request, True))

    def test_receipt_missing_extra_or_changed_history_rejected(self):
        self.check(not valid_receipts([], [], self.request, True))
        self.check(not valid_receipts([], [self.receipt, self.receipt], self.request, True))
        self.check(not valid_receipts([self.receipt], [{}, self.receipt], self.request, True))

    def test_discovery_cannot_add_or_mutate_receipts(self):
        self.check(valid_receipts([self.receipt], [self.receipt], self.request, False))
        self.check(not valid_receipts([], [self.receipt], self.request, False))
        self.check(not valid_receipts([self.receipt], [{}], self.request, False))

    def test_correlated_certificate_evidence_positive(self):
        self.check(certificate_evidence(self.before, self.after))

    def test_generic_tls_error_not_certificate_proof(self):
        bad = dict(self.after, tlsRejections=0, tlsRejectionCodes={})
        self.check(not certificate_evidence(self.before, bad))
        bad = dict(self.after, tlsRejectionCodes={'ECONNRESET': 1})
        self.check(not certificate_evidence(self.before, bad))

    def test_stale_or_multiple_tls_events_not_proof(self):
        self.check(not certificate_evidence(self.after, self.after))
        self.check(not certificate_evidence(self.before, dict(self.after, tlsClientErrors=2)))

    def test_missing_or_unmatched_tls_metrics_not_proof(self):
        self.check(not certificate_evidence({}, {}))
        self.check(not certificate_evidence(self.before, dict(self.after, tlsClientErrorCodes={})))
        self.check(not certificate_evidence(self.before, dict(self.after, tlsRejectionCodes={})))


def regression_tests():
    PositiveControlTests.assertions = 0
    result = unittest.TextTestRunner(stream=io.StringIO()).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(PositiveControlTests))
    return dict(tests=result.testsRun, assertions=PositiveControlTests.assertions,
                failures=len(result.failures), errors=len(result.errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unit-tests', action='store_true', help='Run only positive-control regression tests')
    parser.add_argument('--language', choices=LANGUAGES, action='append')
    parser.add_argument('--mode', choices=MODES, action='append')
    parser.add_argument('--report', type=Path, default=HERE / 'auth-test-report.json')
    args = parser.parse_args()
    regressions = regression_tests()
    print('Regression tests: ' + json.dumps(regressions), flush=True)
    if args.unit_tests or regressions['failures'] or regressions['errors']:
        return bool(regressions['failures'] or regressions['errors'])
    row = json.loads((HERE / 'scenarios.json').read_text())['scenarios'][0]
    results = []
    for language in args.language or LANGUAGES:
        for mode in args.mode or MODES:
            try:
                rows = run(language, mode, row)
            except Exception as error:
                rows = [dict(language=language, mode=mode, case='setup-or-cleanup', status='failed',
                             checks=[dict(assertion=type(error).__name__, passed=False)])]
            results.extend(rows)
            print(f'{language}/{mode}: {sum(r["status"] == "passed" for r in rows)}/{len(rows)} cases passed', flush=True)
    summary = dict(cases=len(results), passed=sum(r['status'] == 'passed' for r in results),
                   failed=sum(r['status'] == 'failed' for r in results),
                   assertions=sum(len(r['checks']) for r in results),
                   failedAssertions=sum(not c['passed'] for r in results for c in r['checks']), unsupported=0)
    args.report.write_text(json.dumps(dict(summary=summary, regressionTests=regressions, reproduction='python-sdk/.venv/bin/python agent-hooks-protocol/interop/test_auth_matrix.py',
        targetedReproduction='Add --language python|go|rust|typescript --mode bearer|oauth|workload|mtls --report /tmp/auth-repro.json',
        findings=[{k: r[k] for k in ('language', 'mode', 'case', 'checks')}
                  for r in results if r['status'] != 'passed'], scope=[
        'HTTP endpoint auth and capability discovery separately; all four adapters and five modes.',
        'none accepts missing/wrong credentials intentionally; not negative authentication coverage.',
        'JWT purpose mutated under correct key; cross-purpose uses other purpose and signing key.',
        'Each negative requires explicit auth rejection, healthy control, zero accepted receipts.',
        'Positive discovery and exact scenario response/receipt delta follow all negatives.',
        'Local synthetic trust only; not production federation or content authorization.',
        'EOF/reset qualifies only with correlated actual certificate-rejection counters; uncorroborated resets/timeouts/unsupported never pass. Temporary configs/logs removed; process groups killed.'
    ], results=results), indent=2) + '\n')
    print(json.dumps(summary))
    return bool(summary['failed'])


if __name__ == '__main__':
    raise SystemExit(main())
