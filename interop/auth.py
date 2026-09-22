"""TEST ONLY synthetic local trust; no production federation or credentials."""
import base64
import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

CLOCK = 1893456000
ISSUER = 'urn:ahp:interop:local-issuer'
AUDIENCE = 'urn:ahp:interop:local-server'
BEARER = 'TEST-ONLY-ahp-interop-static-bearer'
CLIENT_ID = 'ahp-interop-client'
CLIENT_SECRET = 'TEST-ONLY-ahp-interop-client-secret'


def signing_key(purpose):
    return f'TEST-ONLY-ahp-interop-{purpose}-signing-key'


def token(purpose, **overrides):
    claims = dict(iss=ISSUER, sub=CLIENT_ID, aud=AUDIENCE, purpose=purpose, iat=CLOCK, exp=CLOCK + 3600)
    claims.update(overrides)
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).rstrip(b'=').decode()
    message = encode(dict(alg='HS256', typ='JWT')) + '.' + encode(claims)
    mac = hmac.new(signing_key(purpose).encode(), message.encode(), hashlib.sha256).digest()
    return message + '.' + base64.urlsafe_b64encode(mac).rstrip(b'=').decode()


class Issuer:
    """Bounded local OAuth client credentials endpoint with distinct signing key."""
    def __enter__(self):
        owner = self
        owner.issued = 0
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.connection.settimeout(5)
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 16384 or self.path != '/token':
                        raise ValueError()
                    form = parse_qs(self.rfile.read(length).decode(), keep_blank_values=True)
                    expected = dict(grant_type='client_credentials', client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
                    valid = all(form.get(k) == [v] for k, v in expected.items())
                    valid = valid and form.get('audience', [AUDIENCE]) == [AUDIENCE]
                except (ValueError, UnicodeError):
                    valid = False
                if valid:
                    owner.issued += 1
                    body = dict(access_token=token('oauth'), token_type='Bearer', expires_in=3600)
                else:
                    body = dict(error='invalid_client')
                data = json.dumps(body).encode()
                self.send_response(200 if valid else 401)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f'http://127.0.0.1:{self.server.server_port}/token'
        return self
    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def configuration(mode, fixtures, role, issuer=None):
    result = dict(mode=mode)
    if mode == 'bearer':
        result['token'] = BEARER
    elif mode in ('oauth', 'workload'):
        result.update(signingKey=signing_key(mode), issuer=ISSUER, audience=AUDIENCE, purpose=mode, clock=CLOCK)
        if role == 'client':
            if mode == 'workload':
                result['assertion'] = token('workload')
            else:
                result.update(tokenEndpoint=issuer.endpoint, clientId=CLIENT_ID, clientSecret=CLIENT_SECRET)
    elif mode == 'mtls':
        result.update(caFile=str(fixtures / 'ca.pem'), certFile=str(fixtures / f'{role}.pem'), keyFile=str(fixtures / f'{role}-key.pem'))
    return result


class AuthenticationError(ValueError):
    """Credentials did not establish authorization for this protected resource."""


def resolve_bearer(config, environment, credentials):
    """Resolve one endpoint's bearer reference; never fall back to another endpoint."""
    if config.get('type') != 'bearer':
        raise AuthenticationError('unsupported credential mechanism')
    env, ref = config.get('tokenEnv'), config.get('tokenRef')
    if bool(env) == bool(ref):
        raise AuthenticationError('exactly one credential reference is required')
    value = environment.get(env) if env else credentials.get(ref)
    if not isinstance(value, str) or not value or '\r' in value or '\n' in value:
        raise AuthenticationError('credential unavailable')
    return value


def authorize(headers, policy, *, peer_certificate=None):
    """TEST ONLY resource-side enforcement; no identity comes from protocol bodies.

    HS256 keys are synthetic pinned issuer keys, NOT a production JWT/OIDC stack.
    A peer certificate must come from a CERT_REQUIRED TLS socket, never a header.
    """
    mode = policy['mode']
    if mode == 'mtls':
        if not peer_certificate:
            raise AuthenticationError('verified client certificate required')
        subject = dict(pair for rdn in peer_certificate.get('subject', ()) for pair in rdn)
        principal = subject.get('commonName')
        if principal not in policy['principals']:
            raise AuthenticationError('certificate principal not authorized')
        return principal
    values = headers.get_all('Authorization', [])
    if len(values) != 1 or not values[0].startswith('Bearer '):
        raise AuthenticationError('one bearer credential required')
    value = values[0][7:]
    if mode == 'bearer':
        if not hmac.compare_digest(value.encode(), policy['token'].encode()):
            raise AuthenticationError('invalid bearer credential')
        return policy['principal']
    if mode not in ('oauth', 'workload'):
        raise AuthenticationError('unsupported authentication mechanism')
    try:
        head, body, signature = value.split('.')
        def decode(part):
            return base64.b64decode(part + '=' * (-len(part) % 4), altchars=b'-_', validate=True)
        header = json.loads(decode(head))
        claims = json.loads(decode(body))
        expected = hmac.new(policy['signingKey'].encode(), (head + '.' + body).encode(), hashlib.sha256).digest()
        if header != {'alg': 'HS256', 'typ': 'JWT'} or not hmac.compare_digest(decode(signature), expected):
            raise ValueError('signature')
        now = policy['clock']
        if claims.get('iss') != policy['issuer'] or claims.get('aud') != policy['audience'] or claims.get('purpose') != mode:
            raise ValueError('resource or issuer')
        if type(claims.get('exp')) is not int or claims['exp'] <= now:
            raise ValueError('expiry')
        if type(claims.get('iat')) is not int or claims['iat'] > now:
            raise ValueError('issued in future')
        if 'nbf' in claims and (type(claims['nbf']) is not int or claims['nbf'] > now):
            raise ValueError('not yet valid')
        if not isinstance(claims.get('sub'), str) or not claims['sub']:
            raise ValueError('principal')
        if not isinstance(claims.get('scope', ''), str) or not set(policy.get('scopes', [])).issubset(claims.get('scope', '').split()):
            raise ValueError('scope')
        return claims['sub']
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise AuthenticationError('invalid resource credential') from exc
