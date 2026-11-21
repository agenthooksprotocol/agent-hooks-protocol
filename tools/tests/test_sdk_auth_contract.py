"""Golden SDK host-auth semantics; no OAuth wire-schema generation."""
import json
import re
from pathlib import Path
import unittest
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "conformance/draft/sdk-auth-lifecycle.json"


def frame(prefix, fields):
    return prefix + "".join(f"{len(value.encode('utf-8'))}:{value}" for value in fields)


def binding_key(value):
    return frame("ahp.oauth.binding:", [value[field] for field in
                 ("namespace", "backend_id", "purpose", "destination")])


def grant_key(value):
    partition = ("shared:" + value["grant_partition"] if value.get("grant_partition")
                 else "binding:" + binding_key(value))
    return frame("ahp.oauth.grant:", [value[field] for field in
                 ("namespace", "issuer", "resource", "client_identity", "flow")] + [partition])


def registration_key(value):
    """Client-credentials DCR records only; PKCE registers per login."""
    return frame("ahp.oauth.registration:", [value["namespace"], value["issuer"]])


def loopback_portless(uri):
    """RFC 8252 section 7.3: a literal-loopback http redirect without userinfo,
    compared without its port. Other URIs are unchanged."""
    parts = urlsplit(uri)
    if (parts.scheme == "http" and parts.hostname in ("127.0.0.1", "::1")
            and "@" not in parts.netloc):
        host = "[::1]" if parts.hostname == "::1" else "127.0.0.1"
        return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
    return uri


SECRET_METHODS = ("client_secret_basic", "client_secret_post")


def valid_cimd_url(url):
    parts = urlsplit(url)
    return (parts.scheme == "https" and bool(parts.hostname) and bool(parts.path)
            and "#" not in url and parts.username is None and parts.password is None
            and not any(seg in (".", "..") for seg in parts.path.split("/")))


def select_client(value):
    """Redirect check, then pre-registered, then CIMD (PKCE only), then permitted DCR."""
    pkce = value["flow"] == "authorization_code_pkce"
    redirect = urlsplit(value["redirect"] or "")
    if pkce and redirect.scheme == "http" and redirect.hostname not in ("127.0.0.1", "::1"):
        # HTTP only to a literal loopback IP; localhost is never accepted.
        return {"error": "invalid_configuration"}
    if value["preregistered_client_id"]:
        return {"source": "preregistered", "client_id": value["preregistered_client_id"],
                "client_identity": "preregistered:" + value["preregistered_client_id"]}
    metadata = value["as_metadata"]
    cimd = value["cimd"]
    if pkce and cimd and metadata.get("client_id_metadata_document_supported") is True:
        if (not valid_cimd_url(cimd["url"])
                or loopback_portless(value["redirect"]) not in
                {loopback_portless(uri) for uri in cimd["redirect_uris"]}):
            return {"error": "invalid_configuration"}
        return {"source": "cimd", "client_id": cimd["url"], "token_endpoint_auth_method": "none",
                "client_identity": "cimd:" + cimd["url"]}
    # DCR checks: permission/endpoint, then client-credentials feasibility,
    # then endpoint destination policy.
    endpoint = metadata.get("registration_endpoint")
    if not value["dcr_permitted"] or not endpoint:
        return {"error": "credential_unavailable"}
    method = "none"
    if not pkce:
        supported = metadata.get("token_endpoint_auth_methods_supported",
                                 ["client_secret_basic"])
        method = next((m for m in SECRET_METHODS if m in supported), None)
        if method is None or not value["secret_hook"]:
            return {"error": "credential_unavailable"}
    if urlsplit(endpoint).scheme != "https":
        return {"error": "untrusted_destination"}
    return {"source": "dcr", "token_endpoint_auth_method": method, "client_identity": "dcr"}


def validate_registration(row):
    status, body, request = row["status"], row["response"], row["request"]

    def reject(code):
        return {"accepted": False, "error": code}

    if 300 <= status < 400:
        return reject("untrusted_destination")
    if 200 <= status < 300 and (status not in (200, 201) or not isinstance(body, dict)):
        return reject("provider_failed")
    if status in (401, 403):
        return reject("credential_unavailable")
    if status in (408, 429) or status >= 500:
        return reject("provider_failed")
    if 400 <= status < 500:
        return reject("invalid_configuration")
    # A 200/201 JSON object failing validation is deterministic: no retry.
    failed = reject("invalid_configuration")
    client_id = body.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        return failed
    if "redirect_uris" in body and (
            not isinstance(body["redirect_uris"], list)
            or not set(request.get("redirect_uris", [])) <= set(body["redirect_uris"])):
        return failed
    flow_grant = ("authorization_code" if row["flow"] == "authorization_code_pkce"
                  else "client_credentials")
    if "grant_types" in body and flow_grant not in body["grant_types"]:
        return failed
    if body.get("token_endpoint_auth_method",
                request["token_endpoint_auth_method"]) != request["token_endpoint_auth_method"]:
        return failed
    if row["flow"] == "authorization_code_pkce":
        # A public client's secret and its expiry are ignored, never persisted.
        body = {k: v for k, v in body.items()
                if k not in ("client_secret", "client_secret_expires_at")}
    secret = body.get("client_secret")
    if row["flow"] == "client_credentials" and (not isinstance(secret, str) or not secret):
        return failed
    expires_at = body.get("client_secret_expires_at")
    if expires_at is not None and (type(expires_at) is not int or expires_at < 0
                                   or (expires_at and expires_at <= row["now"])):
        return failed
    return {"accepted": True, "expiry": "known" if expires_at else "none",
            "record": {"client_id": client_id, "client_secret_expires_at": expires_at,
                       "secret_ref": secret is not None}}


def valid_expires_in(value):
    if value is None:
        return True
    if type(value) is int:
        return value > 0
    return (isinstance(value, str) and value.isascii() and value.isdigit()
            and int(value) > 0)


SCOPE_TOKEN = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]+")


def response_scope(response):
    """Return the response scope list, None when it is to be treated as omitted,
    or False when it is malformed."""
    value = response.get("scope")
    if value is None or (isinstance(value, str) and not value.strip(" \t\r\n")):
        return None
    if not isinstance(value, str):
        return False
    tokens = value.split(" ")
    if not all(SCOPE_TOKEN.fullmatch(token) for token in tokens):
        return False
    return tokens


def basically_valid(response):
    return (response.get("token_type", "").lower() == "bearer"
            and bool(response.get("access_token"))
            and valid_expires_in(response.get("expires_in"))
            and response_scope(response) is not False)


SCOPED_CODES = ("access_denied", "interaction_required")
HTTP_TOKEN = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
BEARER_PARAM = re.compile(r'[ \t]*(' + HTTP_TOKEN + r')[ \t]*=[ \t]*(?:"((?:[^"\\]|\\.)*)"|('
                          + HTTP_TOKEN + r'))[ \t]*')


TOKEN68 = re.compile(r"[ \t]*([A-Za-z0-9._~+/-]+=*)[ \t]*(?=,|$)")
# A whole token not followed by "=" (a new auth-scheme); the lookaheads stop
# backtracking into a shorter token.
NEW_CHALLENGE = re.compile(r"[ \t]*(" + HTTP_TOKEN + r")(?![!#$%&'*+.^_`|~0-9A-Za-z-])"
                           r"(?![ \t]*=)")


def parse_challenges(header):
    """RFC 9110 challenge list: [(scheme, params dict or token68 str)], or None
    when the header is syntactically malformed or repeats a parameter."""
    challenges, pos = [], 0
    while pos < len(header):
        if header[pos] in " \t,":
            pos += 1
            continue
        scheme = NEW_CHALLENGE.match(header, pos)
        if not scheme:
            return None
        pos = scheme.end()
        if pos < len(header) and header[pos] not in " \t,":
            return None
        params = {}
        param = BEARER_PARAM.match(header, pos)
        token68 = None if param else TOKEN68.match(header, pos)
        if token68 and token68.group(1):
            challenges.append((scheme.group(1), token68.group(1)))
            pos = token68.end()
            continue
        while param:
            name = param.group(1).lower()
            if name in params:
                return None
            quoted = param.group(2)
            params[name] = (re.sub(r"\\(.)", r"\1", quoted) if quoted is not None
                            else param.group(3))
            pos = param.end()
            if pos >= len(header):
                break
            if header[pos] != ",":
                return None
            # After a comma: another auth-param, or a bare token starting a new challenge.
            param = BEARER_PARAM.match(header, pos + 1)
            if param:
                pos += 1
            elif NEW_CHALLENGE.match(header, pos + 1):
                break
            else:
                return None
        challenges.append((scheme.group(1), params))
    return challenges


def bearer_params(header):
    """Auth-params of the one Bearer challenge; None when the header is
    malformed, has no or two Bearer challenges, or Bearer carries a token68
    (RFC 6750: auth-params only). Other schemes, token68 or not, are skipped."""
    challenges = parse_challenges(header)
    if challenges is None:
        return None
    bearer = [value for scheme, value in challenges if scheme.lower() == "bearer"]
    if len(bearer) != 1 or isinstance(bearer[0], str):
        return None
    return bearer[0]


def challenge_scope(value):
    """Single-SP-separated RFC 6749 scope tokens, else False."""
    tokens = value.split(" ")
    return tokens if all(SCOPE_TOKEN.fullmatch(token) for token in tokens) else False


def scope_list_valid(scopes):
    return all(isinstance(s, str) and SCOPE_TOKEN.fullmatch(s) for s in scopes)


def unbound_discovery_requests(challenge):
    """One protected-resource discovery: the challenge's resource_metadata when
    it parses and carries one, else the path-specific and root well-knowns."""
    params = bearer_params(challenge["www_authenticate"]) if challenge else None
    return 1 if params and "resource_metadata" in params else 2


NO_REQUESTS = {"authorization_server_requests": 0, "network_calls": 0}


def decide_scope(value):
    """Reference decide(): step 0, then target checks, then steps 1-9, in order."""
    requested = set(value["requested"])
    ceiling = set(value.get("ceiling", value["requested"]))
    host, explicit = set(value["host_required"]), set(value["explicit"])
    grant, challenge = value["grant"], value.get("challenge")
    status = challenge["status"] if challenge else None
    sent = value.get("sent")
    # 0. A 401 retires the sent generation, if current, before every other step.
    retired = {}
    # Only a client-credentials DCR client has a registration record to replace.
    forced = (value.get("force_registration", False) and value["flow"] == "client_credentials"
              and value.get("client_source") == "dcr")
    # A forced re-registration replaces the client: its old tokens are unusable.
    token_valid = grant is not None and grant["token"] == "valid" and not forced
    if status == 401:
        current = grant is not None and sent is not None and grant["generation"] == sent
        retired = {"retired": current}
        token_valid = token_valid and not current
    # Target, configuration and host-callback failures come after step 0 and keep
    # their own code; classify() strips any scopes.
    for failure in ("target_error", "host_error"):
        if failure in value:
            code = value[failure]["code"]
            return dict(retired, code=code, **({"scopes": []} if code in SCOPED_CODES else {}))
    local = {"authorization_server_requests": 0}
    local.update({"network_calls_max": unbound_discovery_requests(challenge)}
                 if value["binding"] == "unbound" else {"network_calls": 0})

    def fail(code, scopes=None, required=None):
        out = dict(retired, code=code, **local)
        if code in SCOPED_CODES:
            out["scopes"] = sorted(scopes or ())
        if required is not None:
            out["required"] = sorted(required)
        return out

    # 1. Configuration.
    preset = value.get("preset_scopes")
    if (not requested <= ceiling or (preset is not None and set(preset) != requested)
            or not all(scope_list_valid(s) for s in (requested, ceiling, host, explicit))):
        return fail("invalid_configuration")
    # 2. Challenge form.
    hint, step_up = set(), set()
    if challenge:
        params = bearer_params(challenge["www_authenticate"])
        scopes = None
        if params is not None and "scope" in params:
            scopes = challenge_scope(params["scope"])
        if params is None or scopes is False:
            return fail("access_denied")
        if status == 403:
            if params.get("error") != "insufficient_scope":
                return fail("access_denied")
            if scopes is None and not host | explicit:
                return fail("access_denied")
            step_up = set(scopes or ())
        else:
            hint = set(scopes or ())
    # 3. 401 hint: P & C, for a same-operation client-credentials acquisition only.
    hint &= ceiling
    required = host | explicit | step_up
    # 4. Ceiling.
    if not required <= ceiling:
        return fail("access_denied", required, required)
    granted = set(grant["granted"]) if grant else set()
    covered = grant is not None and required <= granted & ceiling
    # 5. Server disagrees with the record.
    if status == 403 and covered:
        if sent is None or grant["generation"] == sent:
            return fail("access_denied", required, required)
        return {"code": None, "action": "use", "required": sorted(required), **NO_REQUESTS}
    # 6. Use.
    if token_valid and covered:
        counts = ({"authorization_server_requests": 0,
                   "network_calls_max": local["network_calls_max"]}
                  if value["binding"] == "unbound" else NO_REQUESTS)
        return dict(retired, code=None, action="use", required=sorted(required), **counts)
    # 7. Refresh, PKCE grants only; client credentials reissue at step 8.
    if (value["flow"] == "authorization_code_pkce" and grant is not None
            and grant["refresh"] and not forced and required <= granted):
        return dict(retired, code=None, action="refresh", required=sorted(required))
    previous = granted
    # 8. Client credentials, with the same-operation hint only.
    if value["flow"] == "client_credentials":
        return dict(retired, code=None, action="acquire", required=sorted(required),
                    acquisition_scopes=sorted(previous | ((requested | required | hint) & ceiling)),
                    **({"authorization_server_requests_min": 1} if forced else {}))
    # 9. PKCE.
    if value["path"] == "prepare":
        return dict(retired, code=None, action="login", required=sorted(required),
                    acquisition_scopes=sorted(previous | ((requested | required) & ceiling)))
    # No authorization-server request is needed to return interaction_required;
    # an unbound target still ran its protected-resource discovery.
    extra = ({"network_calls_max": local["network_calls_max"]}
             if value["binding"] == "unbound" else {})
    return dict(retired, code="interaction_required", scopes=sorted(required),
                required=sorted(required), authorization_server_requests=0, **extra)


OPTIONAL_FEATURES = {"token_validation_callback", "complete_handle",
                     "legacy_prepare_time_host_required"}
CALLBACK_DEFECTS = ("redirect_mismatch", "redirect_query_mismatch", "state_duplicated",
                    "iss_mismatch", "fragment", "userinfo", "missing_code", "code_and_error",
                    "duplicate_param", "unparseable")
TOKEN_ENDPOINT_ERRORS = ("invalid_grant", "invalid_client", "unauthorized_client",
                         "access_denied", "invalid_scope", "server_error", "unreachable",
                         "not_found", "invalid_response")


def complete_login(value):
    """Reference complete(): steps 1-7, in order."""
    def error(code, ended, persisted=False, scopes=()):
        out = {"code": code}
        if code in SCOPED_CODES:
            out["scopes"] = sorted(scopes)
        return dict(out, login_ended=ended, grant_persisted=persisted)

    # 1. State lookup, before any host callback.
    if value["pending"] != "pending":
        return error("interaction_required", True)
    # 2. Callback validation (a handle naming another login is invalid); the
    # login named by state stays pending.
    if value["callback"] in CALLBACK_DEFECTS or value["handle"] == "other":
        return error("access_denied", False)
    # 3. Host policy before the claim.
    if not value["source_permitted"]:
        return error("invalid_configuration", False)
    if "host_error" in value:
        return error(value["host_error"]["code"], False)
    # 4. Claim; an uncertain claim re-reads the store.
    if value["claim"] != "ok":
        return error(value["claim"]["error_code"], value["claim"]["reread"] == "gone")
    # 5. Consent denied.
    if value["callback"] == "denied":
        return error("access_denied", True)
    if value["callback"] == "denied_invalid_client":
        return error("invalid_configuration" if value["client_source"] == "cimd"
                     else "access_denied", True)
    # 6. Exchange.
    exchange = value["exchange"]
    if exchange == "invalid_grant":
        return error("interaction_required", True)
    if exchange == "invalid_client":
        return error({"preregistered": "credential_unavailable", "cimd": "invalid_configuration",
                      "dcr": "interaction_required"}[value["client_source"]], True)
    if exchange != "ok":
        return error("provider_failed", True)
    # 7. Persist, then enforce the stored R (legacy: prepare-time H, else current H).
    required = value["stored_required"]
    if required is None:
        required = value.get("prepare_time_host_required", value["current_host_required"])
    if not set(required) <= set(value["granted"]) & set(value["ceiling"]):
        return error("access_denied", True, persisted=True, scopes=set(required))
    return {"code": None, "grant_persisted": True}


def token_error(value):
    """Reference renewal-error mapping for refresh and client-credentials grants."""
    grant_type, source, error = value["grant_type"], value["client_source"], value["error"]
    refetch = value.get("refetch")
    if refetch == "failed":
        return {"code": "discovery_failed"}
    if refetch is not None:
        # A host destination refusal of the refetch keeps the host's code.
        return {"code": refetch["code"]}
    if error == "invalid_grant":
        return {"code": "refresh_rejected" if grant_type == "refresh" else "credential_unavailable"}
    if error == "unauthorized_client":
        return {"code": "credential_unavailable"}
    if error == "invalid_client":
        if grant_type == "client_credentials" and source == "dcr":
            # Re-register once, re-acquire once; a repeat is credential_unavailable.
            return {"code": "credential_unavailable", "reregistrations": 1}
        return {"code": {"preregistered": "credential_unavailable", "cimd": "invalid_configuration",
                         "dcr": "interaction_required"}[source]}
    return {"code": "provider_failed"}


# A host destination refusal keeps the host's code (default untrusted_destination),
# whichever URL it refuses; discovery_failed is for fetch and validation failures.
DESTINATION_TARGETS = {"protected_resource_metadata", "issuer_metadata",
                       "registration_endpoint", "token_endpoint"}
TOKEN_ERROR_INPUT = {"grant_type", "client_source", "error", "refetch"}
TOKEN_ERROR_EXPECT = {"code", "reregistrations"}
SCOPE_DECISION_INPUT = {"flow", "path", "binding", "requested", "ceiling", "preset_scopes",
                        "host_required", "explicit", "grant", "sent", "challenge",
                        "prior_401_hint", "host_error", "target_error", "client_source",
                        "force_registration"}
SCOPE_DECISION_EXPECT = {"code", "scopes", "required", "action", "acquisition_scopes", "retired",
                         "authorization_server_requests_min",
                         "authorization_server_requests", "network_calls", "network_calls_max"}
COMPLETION_INPUT = {"pending", "handle", "client_source", "source_permitted", "callback",
                    "host_error", "claim", "exchange", "stored_required",
                    "prepare_time_host_required", "current_host_required", "granted", "ceiling"}
COMPLETION_EXPECT = {"code", "scopes", "login_ended", "grant_persisted"}
VECTOR_KEYS = {"id", "requires", "input", "expect"}


class SDKAuthContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vectors = json.loads(FIXTURE.read_text())

    def test_recovery_vocabulary(self):
        mapping = {row["code"]: row["recovery"] for row in self.vectors["recovery"]}
        self.assertEqual(mapping, {
            "invalid_configuration": "configure", "unsupported_binding": "configure",
            "credential_unavailable": "configure", "interaction_required": "login",
            "access_denied": "none", "refresh_rejected": "login",
            "discovery_failed": "retry", "untrusted_destination": "configure",
            "provider_failed": "retry",
        })

    def test_keys_are_utf8_length_framed(self):
        for row in self.vectors["keys"]:
            with self.subTest(row["id"]):
                self.assertEqual(binding_key(row["input"]), row["binding_key"])
                self.assertEqual(grant_key(row["input"]), row["grant_key"])

    def test_identity_and_reuse_boundaries(self):
        cases = {row["id"]: row for row in self.vectors["keys"]}
        self.assertNotEqual(cases["default-event"]["grant_key"], cases["default-upload"]["grant_key"])
        self.assertEqual(cases["shared-event"]["grant_key"], cases["shared-upload"]["grant_key"])
        # Neither the allowed ceiling nor secrets/auth method/metadata are key material.
        for name in ("secret-auth-method-and-capability-rotation", "scope-ceiling-widening"):
            self.assertEqual(cases["default-event"]["grant_key"], cases[name]["grant_key"])
        self.assertNotEqual(cases["default-event"]["input"]["allowed_scopes"],
                            cases["scope-ceiling-widening"]["input"]["allowed_scopes"])
        self.assertNotEqual(cases["default-event"]["grant_key"], cases["resource-trailing-slash"]["grant_key"])
        for row in self.vectors["keys"]:
            for secret in ("not-a-real-secret", "client_secret_basic"):
                self.assertNotIn(secret, row["grant_key"] + row["binding_key"])

    def test_response_vectors_intersect_grant_with_ceiling(self):
        for row in self.vectors["token_responses"]:
            with self.subTest(row["id"]):
                response = row["response"]
                refresh = row.get("exchange") == "refresh"
                inherited = row["previous_granted"] if refresh else row["requested"]
                # Acquisition: bounded by previous grant plus acquisition ceiling.
                # Refresh: bounded only by the previous grant. The current ceiling
                # filters at use.
                previous = set(row.get("previous_granted", []))
                bound = previous if refresh else previous | set(row["allowed"])
                # Requested scopes outside the ceiling are invalid_configuration.
                self.assertLessEqual(set(row["requested"]), set(row["allowed"]))
                self.assertEqual(row.get("exchange") == "reacquire", "wire_request" in row)
                if "wire_request" in row:
                    self.assertEqual(set(row["wire_request"]), previous |
                                     ((set(row["requested"]) | set(row["required"]))
                                      & set(row["allowed"])))
                accepted = basically_valid(response)
                self.assertEqual(accepted, row["accepted"])
                # A refresh keeps its previous token when the response is invalid or
                # omits one; an initial exchange persists only an accepted response's token.
                self.assertEqual(refresh or (accepted and "refresh_token" in response),
                                 row["refresh_preserved"])
                if not accepted:
                    self.assertFalse(row["usable"])
                    self.assertEqual(row["error"], "provider_failed")
                    self.assertNotIn("granted", row)
                    continue
                scopes = response_scope(response)
                granted = set(inherited if scopes is None else scopes) & bound
                self.assertEqual(granted, set(row["granted"]))
                self.assertEqual(len(row["granted"]), len(granted))
                effective = granted & set(row["allowed"])
                self.assertEqual(effective, set(row.get("effective", row["granted"])))
                self.assertEqual("known" if "expires_in" in response else "unknown", row["expiry"])
                usable = set(row["required"]) <= effective
                self.assertEqual(usable, row["usable"])
                self.assertEqual(None if usable else "access_denied", row.get("error"))
                # access_denied carries R, deduplicated and byte-sorted.
                self.assertEqual(None if usable else sorted(set(row["required"])),
                                 row.get("error_scopes"))

    def test_extra_scopes_are_ignored_not_rejected(self):
        cases = {row["id"]: row for row in self.vectors["token_responses"]}
        for name in ("unallowed-added-scope-ignored", "refresh-broadening-scope-ignored"):
            row = cases[name]
            self.assertTrue(set(row["response"]["scope"].split()) - set(row["granted"]))
            self.assertTrue(row["accepted"] and row["usable"])

    def test_blank_and_malformed_scope_vectors_are_exercised(self):
        cases = {row["id"]: row for row in self.vectors["token_responses"]}
        for exchange in ("initial", "refresh"):
            for name in ("empty", "whitespace", "null"):
                row = cases[f"{exchange}-{name}-scope"]
                self.assertIn("scope", row["response"])
                self.assertTrue(row["accepted"] and row["usable"])
                self.assertEqual(row["granted"], row.get("previous_granted", row["requested"]))
            for name in ("numeric", "array"):
                row = cases[f"{exchange}-{name}-scope"]
                self.assertNotIsInstance(row["response"]["scope"], (str, type(None)))
                self.assertEqual(row["error"], "provider_failed")
            for name in ("repeated-separator", "leading-separator", "tab-separator"):
                row = cases[f"{exchange}-{name}-scope"]
                self.assertTrue(row["response"]["scope"].strip(" \t\r\n"))
                self.assertEqual(row["error"], "provider_failed")

    def test_expires_in_and_refresh_persistence_vectors_are_exercised(self):
        rows = self.vectors["token_responses"]
        cases = {row["id"]: row for row in rows}
        self.assertTrue(cases["string-expires-in"]["accepted"])
        self.assertFalse(cases["fractional-expires-in"]["accepted"])
        self.assertFalse(cases["zero-expires-in"]["accepted"])
        self.assertEqual({True, False}, {row["refresh_preserved"] for row in rows})
        kept = cases["step-up-keeps-previous-grant"]
        self.assertLessEqual(set(kept["previous_granted"]), set(kept["granted"]))
        narrowed = cases["refresh-narrow-ceiling-keeps-grant"]
        self.assertLess(set(narrowed["effective"]), set(narrowed["granted"]))

    def assert_error_scope_set(self, expect):
        code = expect.get("code")
        self.assertEqual(code in SCOPED_CODES, "scopes" in expect, expect)
        if "scopes" in expect:
            scopes = expect["scopes"]
            self.assertEqual(scopes, sorted(set(scopes)))
            self.assertTrue(all(SCOPE_TOKEN.fullmatch(scope) for scope in scopes))

    def test_scope_decisions(self):
        codes = {row["code"] for row in self.vectors["recovery"]}
        for row in self.vectors["scope_decisions"]:
            with self.subTest(row["id"]):
                value, expect = row["input"], row["expect"]
                # Every field is defined in the spec; unknown fields fail.
                self.assertLessEqual(set(row), VECTOR_KEYS)
                self.assertLessEqual(set(value), SCOPE_DECISION_INPUT)
                self.assertLessEqual(set(expect), SCOPE_DECISION_EXPECT)
                self.assertEqual(decide_scope(value), expect)
                self.assertIn(expect["code"], codes | {None})
                self.assert_error_scope_set(expect)
                self.assertIn(value["path"], ("fresh", "cached", "prepare", "credential",
                                              "challenge", "preemptive"))
                self.assertIn(value["binding"], ("unbound", "preset", "stored",
                                                 "stored_without_metadata"))
                self.assertEqual("client_source" in value,
                                 value["binding"] == "unbound" or "force_registration" in value)
                effective = ("force_registration" in value and value["flow"] == "client_credentials"
                             and value.get("client_source") == "dcr")
                self.assertEqual("authorization_server_requests_min" in expect, effective)
                if "force_registration" in value:
                    self.assertIs(value["force_registration"], True)
                    self.assertEqual(value["path"], "prepare")
                    if effective:
                        self.assertGreaterEqual(expect["authorization_server_requests_min"], 1)
                        self.assertNotEqual(expect.get("action"), "use")
                # authorization_server_requests accompanies the network counts, every
                # use and every step-9 interaction_required, and is always 0 there.
                self.assertEqual("authorization_server_requests" in expect,
                                 "network_calls" in expect or "network_calls_max" in expect
                                 or (expect["code"] == "interaction_required"
                                     and "host_error" not in value))
                if expect.get("action") == "use":
                    self.assertEqual(expect["authorization_server_requests"], 0)
                    if value["binding"] == "unbound":
                        self.assertEqual(expect["network_calls_max"],
                                         unbound_discovery_requests(value.get("challenge")))
                    else:
                        self.assertEqual(expect["network_calls"], 0)
                self.assertEqual("preset_scopes" in value and value["binding"] != "preset", False)
                self.assertEqual("sent" in value, "challenge" in value)
                self.assertEqual(expect["code"] is None, "action" in expect)
                self.assertEqual(expect.get("action") in ("acquire", "login"),
                                 "acquisition_scopes" in expect)
                # Step 0: every 401 reports retirement, whatever the outcome.
                self.assertEqual(value.get("challenge", {}).get("status") == 401,
                                 "retired" in expect)
                if "target_error" in value:
                    self.assertEqual(set(value["target_error"]), {"check", "code"})
                    self.assertEqual(value["target_error"]["check"], "unsupported_binding")
                if "retired" in expect:
                    self.assertEqual(expect["retired"],
                                     value["grant"] is not None
                                     and value["sent"] == value["grant"]["generation"])
                self.assertFalse("network_calls" in expect and "network_calls_max" in expect)
                if "network_calls" in expect or "network_calls_max" in expect:
                    self.assertEqual(expect["authorization_server_requests"], 0)
                if value["grant"] is not None:
                    self.assertEqual(set(value["grant"]), {"generation", "granted", "token", "refresh"})
                for scope in set(value.get("preset_scopes", [])) - set(value["host_required"]):
                    # Preset scopes are requested, never required.
                    self.assertNotIn(scope, expect.get("required", []))
                    self.assertNotIn(scope, expect.get("scopes", []))
                if "prior_401_hint" in value:
                    # An earlier operation's 401 hint never reaches this one.
                    stray = set(value["prior_401_hint"]) - set(value["requested"])
                    self.assertTrue(stray and stray <= set(value["ceiling"]))
                    self.assertFalse(stray & set(expect.get("acquisition_scopes", [])))

    def test_scope_decision_coverage(self):
        rows = {row["id"]: row for row in self.vectors["scope_decisions"]}
        refusals = [row for row in rows.values()
                    if row["expect"].get("code") == "access_denied"
                    and row["expect"].get("required")]
        # Ceiling refusal is local on every path; unbound targets fetch PRM at most once.
        self.assertEqual({row["input"]["path"] for row in refusals},
                         {"fresh", "cached", "prepare", "credential", "challenge", "preemptive"})
        self.assertEqual({row["input"]["binding"] for row in refusals},
                         {"unbound", "preset", "stored"})
        for row in refusals:
            unbound = row["input"]["binding"] == "unbound"
            self.assertEqual(row["expect"].get("network_calls_max" if unbound else "network_calls"),
                             unbound_discovery_requests(row["input"].get("challenge")) if unbound else 0)
        # An unbound target is an anonymous request answered by the vector's 401.
        unbound = [row for row in rows.values() if row["input"]["binding"] == "unbound"]
        for row in unbound:
            # fresh: an anonymous request answered by the vector's 401; prepare: no challenge.
            self.assertIn(row["input"]["path"], ("fresh", "prepare"))
            if row["input"]["path"] == "fresh":
                self.assertEqual(row["input"]["challenge"]["status"], 401)
                self.assertIsNone(row["input"]["sent"])
            else:
                self.assertNotIn("challenge", row["input"])
        self.assertEqual({row["expect"]["network_calls_max"] for row in unbound}, {1, 2})
        self.assertEqual(rows["unbound-unparseable-challenge-denied"]["expect"]["scopes"], [])
        self.assertEqual(rows["client-credentials-with-refresh-token-reissues"]["expect"]["action"],
                         "acquire")
        self.assertTrue(rows["client-credentials-with-refresh-token-reissues"]["input"]["grant"]["refresh"])
        for name in ("empty", "repeated-space", "leading-space", "trailing-space",
                     "tab-separator", "invalid-character", "duplicated-parameter",
                     "two-bearer-challenges", "bad-auth-param", "401"):
            self.assertEqual(rows["malformed-challenge-scope-" + name]["expect"]["scopes"], [])
        # Step 0 retires before target checks, host callbacks and steps 1 and 2.
        for name in ("malformed-challenge-scope-401", "challenge-401-invalid-configuration-retires-first",
                     "challenge-401-unsupported-binding-retires-first",
                     "challenge-401-host-callback-failure-retires-first"):
            self.assertTrue(rows[name]["expect"]["retired"])
        # An issuer-metadata destination refusal keeps the host's code, never
        # discovery_failed, on prepare and on a challenged operation.
        refused = [row for row in rows.values()
                   if row["input"].get("host_error", {}).get("target") == "issuer_metadata"]
        self.assertEqual({row["input"]["path"] for row in refused}, {"prepare", "challenge"})
        for row in refused:
            self.assertEqual(row["expect"], {"code": "untrusted_destination"})
            # Only an outcome that contacts the authorization server fetches issuer
            # metadata: client credentials (step 8), so every SDK must fetch it.
            self.assertEqual(row["input"]["flow"], "client_credentials")
        challenged = next(row for row in refused if row["input"]["path"] == "challenge")
        self.assertEqual(challenged["input"]["binding"], "stored_without_metadata")
        self.assertEqual(decide_scope({k: v for k, v in challenged["input"].items()
                                       if k != "host_error"})["action"], "acquire")
        # An older sent generation with a valid covering current one is used with no request.
        older = rows["challenge-401-older-generation-uses-current"]
        self.assertEqual(older["input"]["flow"], "client_credentials")
        self.assertLess(older["input"]["sent"], older["input"]["grant"]["generation"])
        self.assertEqual((older["expect"]["action"], older["expect"]["network_calls"]), ("use", 0))
        # An unbound pre-registered PKCE target makes only its protected-resource fetch.
        unbound_pkce = rows["unbound-preregistered-pkce-interaction-required"]
        self.assertEqual((unbound_pkce["input"]["client_source"], unbound_pkce["expect"]["code"],
                          unbound_pkce["expect"]["authorization_server_requests"],
                          unbound_pkce["expect"]["network_calls_max"]),
                         ("preregistered", "interaction_required", 0, 1))
        # A stored binding without persisted metadata decides no-contact outcomes locally.
        for name, code in (("stored-without-metadata-use", None),
                           ("stored-without-metadata-interaction-required", "interaction_required")):
            self.assertEqual(rows[name]["input"]["binding"], "stored_without_metadata")
            self.assertEqual((rows[name]["expect"]["code"],
                              rows[name]["expect"]["authorization_server_requests"]), (code, 0))
        # The force flag only affects a client-credentials DCR client.
        for name in ("prepare-forced-registration-pkce-keeps-usable-token",
                     "prepare-forced-registration-preregistered-keeps-usable-token"):
            self.assertTrue(rows[name]["input"]["force_registration"])
            self.assertEqual(rows[name]["expect"]["action"], "use")
        # prepare on an unbound target with a usable grant: discovery only.
        unbound_prepare = rows["prepare-unbound-usable-grant-uses"]
        self.assertEqual((unbound_prepare["expect"]["action"],
                          unbound_prepare["expect"]["authorization_server_requests"]), ("use", 0))
        # Host policy refusing a challenged stored target pre-empts the step-5 decision.
        refused_target = rows["host-policy-client-selection-refuses-challenged-target"]
        without = {k: v for k, v in refused_target["input"].items() if k != "host_error"}
        self.assertEqual(decide_scope(without)["code"], "access_denied")
        self.assertEqual(refused_target["expect"], {"code": "credential_unavailable"})
        # A forced re-registration never returns the replaced client's token.
        forced = rows["prepare-forced-reregistration-acquires"]
        self.assertEqual(forced["input"]["grant"]["token"], "valid")
        self.assertEqual(forced["expect"]["action"], "acquire")
        # An unbound target with a usable grant uses it after discovery only.
        reused = rows["unbound-usable-grant-uses"]
        self.assertEqual((reused["expect"]["action"], reused["expect"]["authorization_server_requests"],
                          reused["expect"]["network_calls_max"]), ("use", 0, 1))
        # A preset with no stored binding and a valid grant is used with no request.
        preset = rows["preset-scopes-requested-not-required"]
        self.assertEqual((preset["input"]["binding"], preset["input"]["grant"]["token"],
                          preset["expect"]["action"], preset["expect"]["authorization_server_requests"],
                          preset["expect"]["network_calls"]), ("preset", "valid", "use", 0, 0))
        # prepare serves a usable cached token without metadata, even with none persisted.
        cached = rows["prepare-stored-without-metadata-serves-cached-token"]
        self.assertEqual((cached["input"]["path"], cached["input"]["binding"], cached["expect"]["action"],
                          cached["expect"]["authorization_server_requests"], cached["expect"]["network_calls"]),
                         ("prepare", "stored_without_metadata", "use", 0, 0))
        # A PKCE 403 step-up on a stored binding needs no authorization-server request.
        step_up = rows["step-up-carries-full-required-set"]
        self.assertEqual((step_up["input"]["binding"], step_up["input"]["challenge"]["status"],
                          step_up["expect"]["code"], step_up["expect"]["authorization_server_requests"]),
                         ("stored", 403, "interaction_required", 0))
        # The malformed auth-param can only pass via the malformed rule (H is non-empty).
        bad = rows["malformed-challenge-scope-bad-auth-param"]
        self.assertTrue(bad["input"]["host_required"])
        self.assertIsNone(bearer_params(bad["input"]["challenge"]["www_authenticate"]))
        # A token68 directly after Bearer is malformed (H is non-empty, so only that
        # rule yields no scopes); a token68 on another scheme is skipped.
        token68 = rows["malformed-challenge-bearer-token68"]
        self.assertTrue(token68["input"]["host_required"])
        self.assertIsNone(bearer_params(token68["input"]["challenge"]["www_authenticate"]))
        self.assertEqual(token68["expect"]["scopes"], [])
        other = rows["challenge-non-bearer-token68-skipped"]
        challenges = parse_challenges(other["input"]["challenge"]["www_authenticate"])
        self.assertTrue(any(isinstance(v, str) and k.lower() != "bearer" for k, v in challenges))
        self.assertEqual(other["expect"]["scopes"], ["write"])
        unbound = rows["unbound-unparseable-challenge-denied"]
        self.assertIsNone(bearer_params(unbound["input"]["challenge"]["www_authenticate"]))
        self.assertEqual(unbound["expect"]["network_calls_max"], 2)
        # A bare name after a comma starts a new scheme: a Bearer challenge without scope.
        bare = rows["challenge-bare-name-after-comma-is-new-scheme"]
        self.assertEqual(bearer_params(bare["input"]["challenge"]["www_authenticate"]),
                         {"error": "insufficient_scope"})
        self.assertEqual(bare["expect"]["required"], bare["input"]["host_required"])
        # A 401 scope never becomes a requirement or an access_denied.
        for row in rows.values():
            challenge = row["input"].get("challenge")
            if challenge and challenge["status"] == 401:
                params = bearer_params(challenge["www_authenticate"])
                scopes = challenge_scope(params["scope"]) if params and "scope" in params else []
                if scopes and "required" in row["expect"]:
                    self.assertEqual(set(row["expect"]["required"]),
                                     set(row["input"]["host_required"]) | set(row["input"]["explicit"]))
                    self.assertNotEqual(row["expect"]["code"], "access_denied")
        expected = {
            "challenge-401-scope-added-to-request": "acquire",
            "challenge-401-hint-not-used-by-later-operation": "acquire",
            "challenge-401-older-generation-uses-current": "use",
            "insufficient-scope-newer-generation-uses-current": "use",
            "expired-token-refreshes": "refresh",
            "prepare-explicit-and-host-required-union": "login",
            "preset-scopes-omitted-no-constraint": "use",
        }
        for name, action in expected.items():
            self.assertEqual(rows[name]["expect"]["action"], action, name)
        for name in ("insufficient-scope-already-granted-denied",
                     "insufficient-scope-anonymous-grant-covers-denied", "challenge-403-other-denied"):
            self.assertEqual(rows[name]["expect"]["code"], "access_denied")
        self.assertIsNone(rows["insufficient-scope-anonymous-grant-covers-denied"]["input"]["sent"])
        self.assertNotIn("preset_scopes", rows["preset-scopes-omitted-no-constraint"]["input"])
        self.assertEqual(rows["stored-grant-lacking-required-interaction-required"]["expect"]["code"],
                         "interaction_required")
        for name in ("requested-outside-ceiling-invalid-configuration",
                     "preset-outside-ceiling-invalid-configuration",
                     "preset-scopes-differ-from-client-invalid-configuration",
                     "invalid-host-required-token-invalid-configuration"):
            self.assertEqual(rows[name]["expect"]["code"], "invalid_configuration")
        for row in rows.values():
            if "host_error" in row["input"]:
                error = row["input"]["host_error"]
                self.assertTrue(error["scopes"])
                self.assertFalse(row["expect"].get("scopes"))
                self.assertIn(error["callback"],
                              ("client_selection", "token_validation", "destination"))
                self.assertEqual(error["callback"] == "destination", "target" in error)
                if error["callback"] == "destination":
                    # The code this contract defines for that destination refusal.
                    self.assertIn(error["target"], DESTINATION_TARGETS)
                    self.assertEqual(error["code"], "untrusted_destination")

    def test_completions(self):
        codes = {row["code"] for row in self.vectors["recovery"]}
        ended = set()
        for row in self.vectors["completions"]:
            with self.subTest(row["id"]):
                value, expect = row["input"], row["expect"]
                self.assertLessEqual(set(row), VECTOR_KEYS)
                self.assertLessEqual(set(value), COMPLETION_INPUT)
                self.assertLessEqual(set(expect), COMPLETION_EXPECT)
                self.assertLessEqual(COMPLETION_INPUT - {"host_error", "prepare_time_host_required",
                                                        "current_host_required"}, set(value))
                self.assertEqual(value["stored_required"] is None, "current_host_required" in value)
                self.assertFalse("prepare_time_host_required" in value
                                 and value["stored_required"] is not None)
                self.assertIn(value["exchange"], TOKEN_ENDPOINT_ERRORS + ("ok",))
                self.assertEqual(complete_login(value), expect)
                # Every complete() error has a pinned code and carries login_ended.
                self.assertEqual(expect["code"] is not None, "login_ended" in expect)
                self.assertIn(expect["code"], codes | {None})
                self.assert_error_scope_set(expect)
                if expect["code"] == "access_denied" and expect["scopes"]:
                    # Only the stored-R check (step 7) attaches scopes.
                    self.assertTrue(expect["grant_persisted"] and expect["login_ended"])
                ended.add(expect.get("login_ended"))
        self.assertEqual(ended, {None, True, False})
        rows = {row["id"]: row for row in self.vectors["completions"]}
        self.assertEqual({row["input"]["callback"] for row in rows.values()} & set(CALLBACK_DEFECTS),
                         set(CALLBACK_DEFECTS))
        self.assertEqual({row["input"]["exchange"] for row in rows.values()},
                         set(TOKEN_ENDPOINT_ERRORS) | {"ok"})
        self.assertEqual({row["input"]["client_source"] for row in rows.values()
                          if row["input"]["exchange"] == "invalid_client"},
                         {"preregistered", "cimd", "dcr"})
        claims = [row["input"]["claim"] for row in rows.values()]
        for claim in claims:
            # claim is "ok" or an uncertain-claim object with exactly the defined members.
            if claim != "ok":
                self.assertEqual(set(claim), {"outcome", "error_code", "reread"})
                self.assertEqual(claim["outcome"], "uncertain")
                self.assertIn(claim["reread"], ("gone", "pending", "undetermined"))
        self.assertEqual({claim["reread"] for claim in claims if claim != "ok"},
                         {"gone", "pending", "undetermined"})
        for name in ("complete-unknown-state-with-handle", "complete-unknown-state-without-handle",
                     "complete-unknown-state-failing-host-callback", "complete-missing-state-login-ended"):
            self.assertEqual(rows[name]["expect"]["code"], "interaction_required")
            self.assertTrue(rows[name]["expect"]["login_ended"])
        self.assertIn("host_error", rows["complete-unknown-state-failing-host-callback"]["input"])
        # A handle naming another login is an invalid callback: both logins stay pending.
        self.assertEqual(rows["complete-handle-names-other-login"]["expect"],
                         {"code": "access_denied", "scopes": [], "login_ended": False,
                          "grant_persisted": False})
        self.assertIsNone(rows["complete-with-server-added-params"]["expect"]["code"])
        # Host policy, including destination checks, runs before the claim even
        # for a consent denial.
        for name in ("complete-token-endpoint-destination-refused-login-pending",
                     "complete-consent-denied-destination-refused-login-pending"):
            self.assertEqual(rows[name]["input"]["host_error"]["target"], "token_endpoint")
            self.assertEqual(rows[name]["expect"]["code"], "untrusted_destination")
            self.assertFalse(rows[name]["expect"]["login_ended"])
        for name in ("complete-consent-denied-source-not-permitted-login-pending",
                     "complete-consent-denied-client-selection-failure-login-pending",
                     "complete-consent-denied-destination-refused-login-pending"):
            self.assertEqual(rows[name]["input"]["callback"], "denied")
            self.assertFalse(rows[name]["expect"]["login_ended"])
            self.assertNotIn("scopes", rows[name]["expect"])
        # An empty prepare-time H is used, not the current H.
        empty = rows["complete-legacy-record-empty-prepare-time-host-required"]
        self.assertEqual(empty["input"]["prepare_time_host_required"], [])
        self.assertTrue(empty["input"]["current_host_required"])
        self.assertIsNone(empty["expect"]["code"])
        legacy = [row for row in rows.values() if row["input"]["stored_required"] is None]
        self.assertEqual({"prepare_time_host_required" in row["input"] for row in legacy},
                         {True, False})

    def test_token_errors(self):
        codes = {row["code"] for row in self.vectors["recovery"]}
        rows = self.vectors["token_errors"]
        for row in rows:
            with self.subTest(row["id"]):
                self.assertLessEqual(set(row), VECTOR_KEYS)
                self.assertEqual(set(row["input"]) | {"refetch"}, TOKEN_ERROR_INPUT)
                if "refetch" in row["input"]:
                    refetch = row["input"]["refetch"]
                    self.assertIn(row["input"]["error"], ("unreachable", "not_found"))
                    if refetch != "failed":
                        self.assertEqual(set(refetch), {"callback", "target", "code", "scopes"})
                        self.assertEqual((refetch["callback"], refetch["target"]),
                                         ("destination", "issuer_metadata"))
                        self.assertEqual(refetch["code"], "untrusted_destination")
                        self.assertTrue(refetch["scopes"])
                self.assertLessEqual(set(row["expect"]), TOKEN_ERROR_EXPECT)
                self.assertEqual(token_error(row["input"]), row["expect"])
                self.assertIn(row["expect"]["code"], codes)
                self.assertIn(row["input"]["error"], TOKEN_ENDPOINT_ERRORS)
                self.assertFalse(row["input"]["client_source"] == "cimd"
                                 and row["input"]["grant_type"] != "refresh")
        # The stale-metadata refetch: refusal keeps the host's code, failure is
        # discovery_failed; both grant types cover the refusal.
        refetched = {(row["input"]["grant_type"], row["input"]["refetch"] if row["input"]["refetch"] == "failed"
                      else "refused"): row["expect"]["code"]
                     for row in rows if "refetch" in row["input"]}
        self.assertEqual(refetched, {("refresh", "refused"): "untrusted_destination",
                                     ("refresh", "failed"): "discovery_failed",
                                     ("client_credentials", "refused"): "untrusted_destination"})
        # Every error code is covered for both grant types.
        for grant_type in ("refresh", "client_credentials"):
            self.assertEqual({row["input"]["error"] for row in rows
                              if row["input"]["grant_type"] == grant_type},
                             set(TOKEN_ENDPOINT_ERRORS))
        self.assertEqual({(row["input"]["grant_type"], row["input"]["client_source"]) for row in rows
                          if row["input"]["error"] == "invalid_client"},
                         {("refresh", "preregistered"), ("refresh", "cimd"), ("refresh", "dcr"),
                          ("client_credentials", "preregistered"), ("client_credentials", "dcr")})

    def test_optional_feature_requirements(self):
        """requires names optional SDK features; untagged vectors apply to every SDK."""
        tagged = {}
        for section, rows in self.vectors.items():
            if not isinstance(rows, list):
                continue
            for row in rows:
                if "requires" in row:
                    self.assertTrue(row["requires"], row["id"])
                    self.assertLessEqual(set(row["requires"]), OPTIONAL_FEATURES, row["id"])
                    tagged[row["id"]] = set(row["requires"])
        self.assertEqual(tagged, {
            "host-policy-access-denied-scopes-stripped": {"token_validation_callback"},
            "complete-handle-names-other-login": {"complete_handle"},
            "complete-unknown-state-with-handle": {"complete_handle"},
            "complete-legacy-record-prepare-time-host-required": {"legacy_prepare_time_host_required"},
            "complete-legacy-record-empty-prepare-time-host-required":
                {"legacy_prepare_time_host_required"},
        })
        # Vectors whose record holds a prepare-time H need that feature; the
        # absent-field fallback stays untagged so every SDK tests it.
        for row in self.vectors["completions"]:
            holds = "prepare_time_host_required" in row["input"]
            self.assertEqual(holds, "legacy_prepare_time_host_required" in row.get("requires", []),
                             row["id"])
        fallback = [row for row in self.vectors["completions"]
                    if row["input"]["stored_required"] is None and "requires" not in row]
        self.assertEqual([row["id"] for row in fallback],
                         ["complete-legacy-record-current-host-required"])
        self.assertNotIn("prepare_time_host_required", fallback[0]["input"])
        self.assertIn("legacy_prepare_time_host_required", self.vectors["description"])
        spec = (ROOT / "spec/draft/sdk-auth-lifecycle.md").read_text()
        for feature in OPTIONAL_FEATURES:
            # Features are defined in the spec prose, not only the JSON description.
            self.assertIn("`%s`" % feature, spec)
        for field in (SCOPE_DECISION_INPUT | SCOPE_DECISION_EXPECT | COMPLETION_INPUT
                      | COMPLETION_EXPECT | TOKEN_ERROR_INPUT | TOKEN_ERROR_EXPECT
                      | {"outcome", "error_code", "reread", "check"}):
            self.assertIn("`%s`" % field, spec, field)
        # Rule 11 stays covered without the token-validation callback.
        untagged = [row["input"]["host_error"] for row in self.vectors["scope_decisions"]
                    + self.vectors["completions"]
                    if "host_error" in row["input"] and "requires" not in row]
        self.assertEqual({error["callback"] for error in untagged},
                         {"client_selection", "destination"})
        # Without a handle, unknown state is still covered.
        self.assertTrue([row for row in self.vectors["completions"]
                         if row["input"]["pending"] == "unknown" and "requires" not in row])
        # A host failure before the claim keeps the login pending (with a known state).
        for row in self.vectors["completions"]:
            if "host_error" in row["input"] and row["input"]["pending"] == "pending":
                self.assertFalse(row["expect"]["login_ended"])

    def test_scope_contract_lifecycle_ids_are_listed(self):
        ids = {row["id"] for row in self.vectors["lifecycle"]}
        for required in ("preset-scopes-requested-not-required",
                         "ceiling-refusal-before-network-every-path",
                         "requested-outside-ceiling-invalid-configuration",
                         "challenge-401-scope-requested-not-required",
                         "malformed-challenge-scope-denied",
                         "insufficient-scope-already-granted-denied",
                         "complete-enforces-stored-required-set",
                         "grant-narrower-than-required-denied",
                         "host-policy-errors-carry-no-scopes",
                         "complete-error-login-ended",
                         "insufficient-scope-step-up", "step-up-required-scopes-round-trip",
                         "prepare-required-outside-ceiling-denied",
                         "scope-challenge-without-required-scope",
                         "entry-points-route-through-decide", "transport-challenge-routing",
                         "replay-rejection-retires-token",
                         "complete-token-endpoint-errors-classified",
                         "renewal-token-errors-classified"):
            self.assertIn(required, ids)
        expects = {row["id"]: row["expect"] for row in self.vectors["lifecycle"]}
        self.assertNotIn("diagnostics only", expects["step-up-required-scopes-round-trip"])

    def test_registration_keys_are_utf8_length_framed(self):
        cases = {row["id"]: row for row in self.vectors["registration_keys"]}
        for name, row in cases.items():
            with self.subTest(name):
                self.assertEqual(registration_key(row["input"]), row["registration_key"])
                self.assertNotIn("not-a-real-secret", row["registration_key"])
        base = cases["cc-registration"]["registration_key"]
        # Registration metadata, endpoint, scope and secrets are not key material.
        self.assertEqual(base, cases["cc-registration-nonkey-metadata"]["registration_key"])
        for name in ("cc-issuer-trailing-slash", "cc-unicode-namespace"):
            self.assertNotEqual(base, cases[name]["registration_key"])
        self.assertNotIn("registration_request_digests", self.vectors)

    def test_issued_dcr_client_is_not_key_material(self):
        cases = {row["id"]: row for row in self.vectors["keys"]}
        first, second = cases["dcr-client-identity"], cases["dcr-reissued-client-same-key"]
        self.assertNotEqual(first["input"]["issued_client_id"], second["input"]["issued_client_id"])
        self.assertEqual(first["grant_key"], second["grant_key"])
        self.assertNotEqual(first["grant_key"], cases["default-event"]["grant_key"])
        for row in self.vectors["keys"]:
            self.assertNotIn("dcr-client", row["grant_key"])

    def test_loopback_redirect_matching_is_port_agnostic_for_cimd_only(self):
        self.assertEqual(loopback_portless("http://127.0.0.1:8765/cb"), "http://127.0.0.1/cb")
        self.assertEqual(loopback_portless("http://[::1]:8765/cb"), "http://[::1]/cb")
        for unchanged in ("http://u@127.0.0.1:8765/cb", "http://localhost:8765/cb",
                          "https://127.0.0.1:8765/cb"):
            self.assertEqual(loopback_portless(unchanged), unchanged)
        selection = {row["id"]: row for row in self.vectors["client_selection"]}
        for name in ("localhost-redirect-invalid", "preregistered-localhost-redirect-invalid"):
            self.assertEqual(selection[name]["expect"], {"error": "invalid_configuration"})
        for name in ("cimd-loopback-port-mismatch-accepted",
                     "cimd-portless-loopback-redirect-matches",
                     "cimd-ipv6-loopback-redirect-matches"):
            row = selection[name]
            self.assertNotIn(row["input"]["redirect"], row["input"]["cimd"]["redirect_uris"])
            self.assertEqual(row["expect"]["source"], "cimd")
        responses = {row["id"]: row for row in self.vectors["registration_responses"]}
        self.assertFalse(responses["pkce-echo-other-port-rejected"]["expect"]["accepted"])
        for row in self.vectors["registration_responses"]:
            if row["flow"] == "authorization_code_pkce":
                self.assertEqual(row["request"].get("application_type"), "native")

    def test_client_selection_order(self):
        codes = {row["code"] for row in self.vectors["recovery"]}
        sources = set()
        for row in self.vectors["client_selection"]:
            with self.subTest(row["id"]):
                self.assertEqual(select_client(row["input"]), row["expect"])
                self.assertIn(row["expect"].get("error", "credential_unavailable"), codes)
                sources.add(row["expect"].get("source"))
                if row["input"]["flow"] == "client_credentials":
                    self.assertNotEqual(row["expect"].get("source"), "cimd")
                    self.assertNotEqual(row["expect"].get("token_endpoint_auth_method"), "none")
        self.assertEqual(sources, {"preregistered", "cimd", "dcr", None})

    def test_registration_responses(self):
        codes = {row["code"] for row in self.vectors["recovery"]}
        outcomes = set()
        for row in self.vectors["registration_responses"]:
            with self.subTest(row["id"]):
                self.assertEqual(validate_registration(row), row["expect"])
                outcomes.add(row["expect"].get("error"))
                record = json.dumps(row["expect"].get("record", {}))
                # Secrets and RFC 7592 management material never reach the record.
                for forbidden in ("not-a-real-secret", "registration_access_token",
                                  "registration_client_uri", "client_secret\""):
                    self.assertNotIn(forbidden, record)
        self.assertLessEqual(outcomes - {None}, codes)
        self.assertEqual(outcomes, {None, "provider_failed", "invalid_configuration",
                                    "credential_unavailable", "untrusted_destination"})

    def test_registration_lifecycle_ids_are_listed(self):
        ids = {row["id"] for row in self.vectors["lifecycle"]}
        for required in ("registration-order-preregistered-cimd-dcr", "cimd-used-when-supported",
                         "cimd-not-supported-falls-back-to-dcr",
                         "cimd-redirect-mismatch-invalid-configuration",
                         "cimd-portless-loopback-redirect-matches",
                         "loopback-redirect-ephemeral-port-per-login",
                         "dcr-pkce-registers-per-login", "dcr-issued-client-stored-with-grant",
                         "dcr-pkce-invalid-client-drops-grant",
                         "dcr-registers-once-across-processes", "dcr-preregistered-wins",
                         "dcr-expired-secret-reregisters", "dcr-invalid-client-reregisters-once",
                         "dcr-policy-disallows-registration", "dcr-secret-not-in-token-record",
                         "lookup-never-registers-pkce", "dcr-replaced-secret-deleted",
                         "registration-transient-failure-backs-off",
                         "registration-retry-after-honoured",
                         "pending-login-owned-by-creating-process",
                         "pending-login-persisted-cross-process-completion",
                         "pending-logins-concurrent-per-state",
                         "pending-logins-capped-per-grant-key",
                         "pending-completion-newest-grant-wins"):
            self.assertIn(required, ids)
        selection = {row["id"] for row in self.vectors["client_selection"]}
        for shared in ("cimd-used-when-supported", "cimd-not-supported-falls-back-to-dcr",
                       "cimd-redirect-mismatch-invalid-configuration",
                       "dcr-policy-disallows-registration"):
            self.assertIn(shared, selection)

    def test_configuration_skip_lifecycle_ids_are_listed(self):
        ids = {row["id"] for row in self.vectors["lifecycle"]}
        codes = {row["code"] for row in self.vectors["recovery"]}
        for required in ("configuration-401-diagnostic-carries-auth-code",
                         "configuration-unavailable-retried-after-prepare",
                         "configuration-unavailable-backoff",
                         "configuration-auth-failure-not-retried-on-backoff",
                         "configuration-invalid-not-retried",
                         "configuration-retry-atomic-routes"):
            self.assertIn(required, ids)
        diagnostic = next(row for row in self.vectors["lifecycle"]
                          if row["id"] == "configuration-401-diagnostic-carries-auth-code")
        # The auth cause reuses the shared safe-code vocabulary.
        self.assertIn("interaction_required", diagnostic["expect"])
        self.assertIn("interaction_required", codes)

    def test_lifecycle_coverage_manifest_is_well_formed(self):
        ids = [row["id"] for row in self.vectors["lifecycle"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(row["expect"] for row in self.vectors["lifecycle"]))
        # Mechanisms removed for MCP parity stay removed.
        for gone in ("supersede", "marker", "proactive", "force-reregistration", "pending-login-reuse"):
            self.assertFalse([name for name in ids if gone in name], gone)
        self.assertNotIn("quarantin", FIXTURE.read_text())


if __name__ == "__main__":
    unittest.main()
