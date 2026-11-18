"""Golden SDK host-auth semantics; no OAuth wire-schema generation."""
import hashlib
import json
import re
from pathlib import Path
import unittest
from urllib.parse import urlsplit

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
                 ("namespace", "issuer", "resource", "client_id", "flow")] + [partition])


def registration_key(value):
    return frame("ahp.oauth.registration:", [value[field] for field in
                 ("namespace", "issuer", "flow", "redirect")])


def request_digest(value):
    scope = " ".join(sorted(set(value.get("registration_scope", []))))
    return hashlib.sha256(frame("ahp.oauth.registration-request:", [
        value.get("client_name", ""), value.get("software_id", ""),
        value.get("software_version", ""), scope,
        value.get("initial_access_token_ref", "")]).encode("utf-8")).hexdigest()


SECRET_METHODS = ("client_secret_basic", "client_secret_post")


def valid_cimd_url(url):
    parts = urlsplit(url)
    return (parts.scheme == "https" and bool(parts.hostname) and bool(parts.path)
            and "#" not in url and parts.username is None and parts.password is None
            and not any(seg in (".", "..") for seg in parts.path.split("/")))


def select_client(value):
    """Pre-registered, then CIMD (PKCE only), then permitted DCR."""
    if value["preregistered_client_id"]:
        return {"source": "preregistered", "client_id": value["preregistered_client_id"]}
    metadata = value["as_metadata"]
    pkce = value["flow"] == "authorization_code_pkce"
    cimd = value["cimd"]
    if pkce and cimd and metadata.get("client_id_metadata_document_supported") is True:
        if (not valid_cimd_url(cimd["url"])
                or value["redirect"] not in cimd["redirect_uris"]):
            return {"error": "invalid_configuration"}
        return {"source": "cimd", "client_id": cimd["url"], "token_endpoint_auth_method": "none"}
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
    return {"source": "dcr", "token_endpoint_auth_method": method}


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
    if "redirect_uris" in body and body["redirect_uris"] != request.get("redirect_uris"):
        return failed
    flow_grant = ("authorization_code" if row["flow"] == "authorization_code_pkce"
                  else "client_credentials")
    if "grant_types" in body and flow_grant not in body["grant_types"]:
        return failed
    if body.get("token_endpoint_auth_method",
                request["token_endpoint_auth_method"]) != request["token_endpoint_auth_method"]:
        return failed
    secret = body.get("client_secret")
    if (row["flow"] == "authorization_code_pkce") != (secret is None):
        return failed
    if secret is not None and (not isinstance(secret, str) or not secret):
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
                if not refresh:
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

    def test_registration_keys_are_utf8_length_framed(self):
        cases = {row["id"]: row for row in self.vectors["registration_keys"]}
        for name, row in cases.items():
            with self.subTest(name):
                self.assertEqual(registration_key(row["input"]), row["registration_key"])
                self.assertNotIn("not-a-real-secret", row["registration_key"])
        base = cases["dcr-pkce-loopback"]["registration_key"]
        # Registration metadata, endpoint, scope and secrets are not key material.
        self.assertEqual(base, cases["dcr-pkce-nonkey-metadata"]["registration_key"])
        for name in ("dcr-pkce-other-redirect", "dcr-issuer-trailing-slash",
                     "dcr-client-credentials", "dcr-unicode-namespace"):
            self.assertNotEqual(base, cases[name]["registration_key"])
        self.assertEqual(cases["dcr-client-credentials"]["input"]["redirect"], "")

    def test_registration_request_digests(self):
        cases = {row["id"]: row for row in self.vectors["registration_request_digests"]}
        for name, row in cases.items():
            with self.subTest(name):
                self.assertEqual(request_digest(row["input"]), row["request_digest"])
        base = cases["policy-base"]["request_digest"]
        self.assertEqual(base, cases["policy-scope-reordered-duplicated"]["request_digest"])
        for name in ("policy-scope-changed", "policy-client-name-changed",
                     "policy-initial-access-token-changed", "policy-minimal"):
            self.assertNotEqual(base, cases[name]["request_digest"])

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
                         "dcr-registers-once-across-processes", "dcr-preregistered-wins",
                         "dcr-expired-secret-reregisters", "dcr-invalid-client-reregisters-once",
                         "dcr-policy-disallows-registration", "dcr-secret-not-in-token-record",
                         "lookup-never-registers-pkce", "dcr-failed-marker-bounds-registration",
                         "prepare-force-reregistration", "dcr-policy-change-reregisters",
                         "dcr-replaced-secret-deleted", "cc-registration-auto-repairs-after-backoff",
                         "cc-proactive-reregistration-before-expiry",
                         "marker-blocks-only-registration",
                         "stale-callback-does-not-delete-new-registration",
                         "registration-transient-failure-backs-off",
                         "registration-retry-after-honoured",
                         "marker-update-atomic-across-processes",
                         "proactive-reregistration-once-across-processes",
                         "proactive-renewal-failure-keeps-current-registration"):
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
        self.assertNotIn("quarantin", FIXTURE.read_text())


if __name__ == "__main__":
    unittest.main()
