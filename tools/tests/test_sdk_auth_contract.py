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


def bearer_params(header):
    """Strict single-Bearer-challenge auth-params; None when unparsable or a
    parameter is duplicated."""
    if not re.match(r"Bearer(?: |$)", header):
        return None
    rest, params, pos = header[len("Bearer"):].strip(" \t"), {}, 0
    while pos < len(rest):
        match = BEARER_PARAM.match(rest, pos)
        if not match:
            return None
        name = match.group(1).lower()
        if name in params:
            return None
        quoted = match.group(2)
        params[name] = (re.sub(r"\\(.)", r"\1", quoted) if quoted is not None
                        else match.group(3))
        pos = match.end()
        if pos < len(rest):
            if rest[pos] != ",":
                return None
            pos += 1
    return params


def challenge_scope(value):
    """Single-SP-separated RFC 6749 scope tokens, else False."""
    tokens = value.split(" ")
    return tokens if all(SCOPE_TOKEN.fullmatch(token) for token in tokens) else False


def decide_scope(value):
    """Reference scope decision: R = H | E | Ch; requested scopes never required."""
    requested = set(value["requested"]) | set(value.get("preset_scopes", []))
    ceiling = set(value.get("ceiling", requested))
    if "host_error" in value:
        # Host-policy errors keep their code; any scopes are stripped.
        code = value["host_error"]["code"]
        return {"code": code, **({"scopes": []} if code in SCOPED_CODES else {})}
    challenge = value.get("challenge")
    status = challenge["status"] if challenge else None
    out = {"retired": True} if status == 401 else {}

    def deny(scopes, with_required=True):
        result = dict(out, code="access_denied", scopes=sorted(scopes), network_calls=0)
        if with_required:
            result["required"] = sorted(scopes)
        return result

    if not requested <= ceiling:
        return dict(out, code="invalid_configuration", network_calls=0)
    base = set(value["host_required"]) | set(value["explicit"])
    hint, step_up = set(), set()
    if challenge:
        params = bearer_params(challenge["www_authenticate"])
        scopes = None
        if params is not None and "scope" in params:
            scopes = challenge_scope(params["scope"])
        if params is None or scopes is False:
            return deny([], with_required=False)
        if status == 401:
            hint = set(scopes or ()) & ceiling
        else:
            assert params.get("error") == "insufficient_scope"
            if scopes is None and not base:
                return deny([], with_required=False)
            step_up = set(scopes or ())
    required = base | step_up
    if not required <= ceiling:
        return deny(required)
    granted = value["granted"]
    covered = granted is not None and required <= set(granted) & ceiling
    if status == 403 and covered:
        return deny(required)
    if covered and status != 401:
        return dict(out, code=None, action="use", required=sorted(required))
    acquisition = sorted(set(granted or ()) | ((requested | required | hint) & ceiling))
    result = dict(out, required=sorted(required), acquisition_scopes=acquisition)
    if value["flow"] == "client_credentials":
        return dict(result, code=None, action="acquire")
    if value["path"] == "prepare":
        return dict(result, code=None, action="login")
    return dict(result, code="interaction_required", scopes=sorted(required))


def complete_login(value):
    """Reference complete(): enforce the stored R and report login_ended."""
    if value["pending"] != "pending":
        result = ({"code": "interaction_required", "scopes": []} if value["pending"] == "expired"
                  else {"failed": True})
        return dict(result, login_ended=True, grant_persisted=False)
    if value["callback"] == "invalid":
        return {"failed": True, "login_ended": False, "grant_persisted": False}
    if value["callback"] == "denied":
        return {"failed": True, "login_ended": True, "grant_persisted": False}
    if value["exchange"] == "invalid_grant":
        return {"code": "interaction_required", "scopes": [], "login_ended": True,
                "grant_persisted": False}
    if value["exchange"] == "invalid_response":
        return {"code": "provider_failed", "login_ended": True, "grant_persisted": False}
    required = set(value["stored_required"])
    if not required <= set(value["granted"]) & set(value["ceiling"]):
        return {"code": "access_denied", "scopes": sorted(required), "login_ended": True,
                "grant_persisted": True}
    return {"code": None, "grant_persisted": True}


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
                self.assertEqual(decide_scope(value), expect)
                self.assertIn(expect["code"], codes | {None})
                self.assert_error_scope_set(expect)
                for scope in value.get("preset_scopes", []):
                    # Preset scopes are requested, never required.
                    self.assertNotIn(scope, expect.get("required", []))
                    self.assertNotIn(scope, expect.get("scopes", []))

    def test_scope_decision_coverage(self):
        rows = {row["id"]: row for row in self.vectors["scope_decisions"]}
        refusals = [row for row in rows.values()
                    if row["expect"].get("code") == "access_denied"
                    and row["expect"].get("required")]
        # Ceiling refusal is local on every path.
        self.assertEqual({row["input"]["path"] for row in refusals},
                         {"fresh", "cached", "prepare", "credential", "challenge", "preemptive"})
        for row in refusals:
            self.assertEqual(row["expect"]["network_calls"], 0)
        for name in ("empty", "repeated-space", "leading-space", "trailing-space",
                     "tab-separator", "invalid-character", "duplicated-parameter", "401"):
            self.assertEqual(rows["malformed-challenge-scope-" + name]["expect"]["scopes"], [])
        # A 401 scope never becomes a requirement or an access_denied.
        for row in rows.values():
            if row["input"].get("challenge", {}).get("status") == 401:
                self.assertTrue(row["expect"]["retired"])
                params = bearer_params(row["input"]["challenge"]["www_authenticate"])
                scopes = challenge_scope(params["scope"]) if params and "scope" in params else []
                if scopes:
                    host = set(row["input"]["host_required"]) | set(row["input"]["explicit"])
                    self.assertEqual(set(row["expect"]["required"]), host)
                    self.assertNotEqual(row["expect"]["code"], "access_denied")
        self.assertEqual(rows["step-up-carries-full-required-set"]["expect"]["scopes"],
                         ["read", "write"])
        self.assertEqual(rows["insufficient-scope-already-granted-denied"]["expect"]["code"],
                         "access_denied")
        for name in ("requested-outside-ceiling-invalid-configuration",
                     "preset-outside-ceiling-invalid-configuration"):
            self.assertEqual(rows[name]["expect"]["code"], "invalid_configuration")
        for row in rows.values():
            if "host_error" in row["input"]:
                error = row["input"]["host_error"]
                self.assertTrue(error["scopes"])
                self.assertFalse(row["expect"].get("scopes"))
                self.assertIn(error["callback"],
                              ("client_selection", "token_validation", "destination"))
                if error["callback"] == "destination":
                    # The code this contract defines for that destination refusal.
                    self.assertEqual(error["code"], {
                        "registration_endpoint": "untrusted_destination",
                        "issuer_metadata": "discovery_failed"}[error["target"]])

    def test_completions(self):
        codes = {row["code"] for row in self.vectors["recovery"]}
        ended = set()
        for row in self.vectors["completions"]:
            with self.subTest(row["id"]):
                expect = row["expect"]
                self.assertEqual(complete_login(row["input"]), expect)
                failed = expect.get("code") is not None or expect.get("failed", False)
                # Every complete() error, and only an error, carries login_ended.
                self.assertEqual(failed, "login_ended" in expect)
                self.assertIn(expect.get("code"), codes | {None})
                self.assert_error_scope_set(expect)
                if expect.get("code") == "access_denied":
                    self.assertEqual(expect["scopes"],
                                     sorted(set(row["input"]["stored_required"])))
                    self.assertTrue(expect["grant_persisted"] and expect["login_ended"])
                ended.add(expect.get("login_ended"))
        self.assertEqual(ended, {None, True, False})

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
                         "scope-challenge-without-required-scope"):
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
