"""Isolated reference HTTP binding and actual-wire content test receiver.

No SDK adapter dependency, control API, fixture-ID dispatch, or semantic oracle.
"""
import base64
from contextlib import contextmanager
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from check_conformance import SchemaStore, Snapshot, SubsetValidator


def validate(name, value):
    store = SchemaStore(Snapshot.resolve(ROOT))
    path = ROOT / 'schema' / 'draft' / (name + '.schema.json')
    errors = SubsetValidator(store).validate(value, store.load(path), path)
    if errors:
        raise ValueError('; '.join(errors))


def encoded(value):
    return base64.urlsafe_b64encode(value.encode('utf-8')).decode('ascii').rstrip('=')


def decoded(value):
    if not value or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Invalid identifier header')
    result = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True).decode('utf-8')
    if encoded(result) != value:
        raise ValueError('Noncanonical identifier header')
    return result


def reference(ref, body):
    result = dict(ref=ref, size=len(body), sha256=hashlib.sha256(body).hexdigest())
    validate('content-reference', result)
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def post(endpoint, body, headers, timeout=5):
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', '::1', 'localhost')):
        raise ValueError('HTTPS required outside loopback')
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError('Endpoint credentials/fragments forbidden')
    request = urllib.request.Request(endpoint, data=body, headers=headers, method='POST')
    with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
        if response.status != 204:
            raise ValueError('Upload/event not confirmed: ' + str(response.status))


def upload(config, subscription, metadata, body):
    validate('content-upload', config)
    validate('content-reference', metadata)
    if reference(metadata['ref'], body) != metadata:
        raise ValueError('Local hash/size mismatch')
    if len(body) > config['maxBytes']:
        raise ValueError('Upload exceeds local bound; do not truncate')
    headers = {'Content-Type': 'application/octet-stream', 'Content-Length': str(len(body)),
               'AHP-Subscription': encoded(subscription), 'AHP-Content-Ref': encoded(metadata['ref']),
               'AHP-Content-SHA256': metadata['sha256']}
    if 'auth' in config:
        token = os.environ[config['auth']['tokenEnv']]
        if not token or '\r' in token or '\n' in token:
            raise ValueError('Invalid upload credential')
        headers['Authorization'] = 'Bearer ' + token
    post(config['endpoint'], body, headers, config['timeoutMs'] / 1000)


def category(item):
    if item['kind'] == 'reasoning':
        return 'reasoning'
    if 'category' in item:
        return item['category']
    media = item['mediaType'].split('/', 1)[0].lower()
    return {'text': 'text', 'image': 'images', 'audio': 'audio', 'video': 'video'}.get(media, 'files')


def selected_item(descriptor, selection, body, ref, authorized=True):
    """Return a normalized descriptor; source unavailable is not an opt-out."""
    validate('content-selection', selection)
    item = deepcopy(descriptor)
    choice = selection.get(category(item), selection['default'])
    item['selection'] = choice
    for field in ('body', 'gap'):
        item.pop(field, None)
    if choice == 'body':
        if not authorized:
            item['gap'] = {'reason': 'unauthorized'}
        elif body is None:
            item['gap'] = {'reason': 'source_unavailable'}
        else:
            item['body'] = reference(ref, body)
    validate('content-item', item)
    return item


def body_refs(message):
    # Normalized items only. Never recursively interpret opaque native payloads.
    return [item['body'] for item in message['params']['event'].get('items', []) if 'body' in item]


def deliver(message, subscription, config, bodies, endpoint, event_token):
    name = {'hooks/observe': 'observe-notification', 'hooks/intercept': 'intercept-request'}[message['method']]
    validate(name, message)
    for metadata in body_refs(message):
        upload(config, subscription, metadata, bodies[metadata['ref']])
    headers = {'Content-Type': 'application/json', 'AHP-Subscription': encoded(subscription)}
    if event_token is not None:
        headers['Authorization'] = 'Bearer ' + event_token
    post(endpoint, json.dumps(message).encode(), headers)


class Receiver:
    """Bounded in-memory conformance receiver; not production retention storage."""
    def __init__(self, upload_tokens, event_tokens, max_bytes=1024 * 1024):
        self.upload_tokens = upload_tokens
        self.event_tokens = event_tokens
        self.max_bytes = max_bytes
        self.contents = {}
        self.events = []
        self.trace = []
        self.lock = threading.Lock()

    def handler(self, upload_path=None, event_path=None):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, status):
                self.send_response(status)
                self.send_header('Content-Length', '0')
                self.end_headers()

            def do_POST(self):
                is_upload = upload_path is not None and self.path == upload_path
                if not is_upload and not (event_path is not None and self.path == event_path):
                    return self.reply(404)
                tokens = owner.upload_tokens if is_upload else owner.event_tokens
                auth = self.headers.get('Authorization')
                if auth not in tokens:
                    return self.reply(401)
                try:
                    subscription = decoded(self.headers.get('AHP-Subscription'))
                    if subscription not in tokens[auth]:
                        return self.reply(403)
                    names = ['Content-Length', 'AHP-Subscription', 'Content-Type']
                    if len(self.headers.get_all('Authorization', [])) > 1:
                        raise ValueError('Duplicate authorization')
                    if auth is not None:
                        names.append('Authorization')
                    if is_upload:
                        names += ['AHP-Content-Ref', 'AHP-Content-SHA256']
                    if any(len(self.headers.get_all(n, [])) != 1 for n in names):
                        raise ValueError('Missing/duplicate header')
                    if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Encoding'):
                        raise ValueError('Unsupported transfer/content encoding')
                    length = self.headers['Content-Length']
                    if not re.fullmatch(r'0|[1-9][0-9]*', length):
                        raise ValueError('Invalid byte length')
                    size = int(length)
                    if size > owner.max_bytes:
                        return self.reply(413)
                    self.connection.settimeout(2)
                    body = self.rfile.read(size)
                    if len(body) != size:
                        raise ValueError('Incomplete body')
                    with owner.lock:
                        if is_upload:
                            if self.headers['Content-Type'] != 'application/octet-stream':
                                raise ValueError('Raw octets required')
                            ref = decoded(self.headers.get('AHP-Content-Ref'))
                            metadata = {'ref': ref, 'size': size, 'sha256': self.headers.get('AHP-Content-SHA256')}
                            validate('content-reference', metadata)
                            if metadata != reference(ref, body):
                                raise ValueError('Integrity mismatch')
                            key = (subscription, ref)
                            if key in owner.contents and owner.contents[key] != body:
                                return self.reply(409)
                            owner.contents[key] = body
                            owner.trace.append(('upload', subscription, ref))
                        else:
                            if self.headers['Content-Type'] != 'application/json':
                                raise ValueError('JSON required')
                            message = json.loads(body)
                            # This test sink handles real observe notifications; intercept
                            # responses/effect evaluation remain the SDK adapter's concern.
                            validate('observe-notification', message)
                            if message['params']['subscriptionId'] != subscription:
                                return self.reply(403)
                            for metadata in body_refs(message):
                                stored = owner.contents.get((subscription, metadata['ref']))
                                if stored is None:
                                    return self.reply(404)
                                if reference(metadata['ref'], stored) != metadata:
                                    raise ValueError('Event integrity mismatch')
                            owner.events.append(message)
                            owner.trace.append(('event', subscription, message['params']['event']['id']))
                    return self.reply(204)
                except (ValueError, TypeError, KeyError, OSError):
                    return self.reply(400)
        return Handler


@contextmanager
def serve(handler):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("Receiver cleanup timed out")
