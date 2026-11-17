"""Golden SDK host-auth semantics; no OAuth wire-schema generation."""
import json
from pathlib import Path
import unittest

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


def valid_expires_in(value):
    if value is None:
        return True
    if type(value) is int:
        return value > 0
    return (isinstance(value, str) and value.isascii() and value.isdigit()
            and int(value) > 0)


def basically_valid(response):
    return (response.get("token_type", "").lower() == "bearer"
            and bool(response.get("access_token"))
            and valid_expires_in(response.get("expires_in")))


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
                ceiling = set(row["allowed"])
                if refresh:
                    ceiling &= set(row["previous_granted"])
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
                granted = set(response.get("scope", " ".join(inherited)).split()) & ceiling
                self.assertEqual(granted, set(row["granted"]))
                self.assertEqual(len(row["granted"]), len(granted))
                self.assertEqual("known" if "expires_in" in response else "unknown", row["expiry"])
                usable = set(row["required"]) <= granted
                self.assertEqual(usable, row["usable"])
                self.assertEqual(None if usable else "access_denied", row.get("error"))

    def test_extra_scopes_are_ignored_not_rejected(self):
        cases = {row["id"]: row for row in self.vectors["token_responses"]}
        for name in ("unallowed-added-scope-ignored", "refresh-broadening-scope-ignored"):
            row = cases[name]
            self.assertTrue(set(row["response"]["scope"].split()) - set(row["granted"]))
            self.assertTrue(row["accepted"] and row["usable"])

    def test_expires_in_and_refresh_persistence_vectors_are_exercised(self):
        rows = self.vectors["token_responses"]
        cases = {row["id"]: row for row in rows}
        self.assertTrue(cases["string-expires-in"]["accepted"])
        self.assertFalse(cases["fractional-expires-in"]["accepted"])
        self.assertFalse(cases["zero-expires-in"]["accepted"])
        self.assertEqual({True, False}, {row["refresh_preserved"] for row in rows})

    def test_lifecycle_coverage_manifest_is_well_formed(self):
        ids = [row["id"] for row in self.vectors["lifecycle"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(row["expect"] for row in self.vectors["lifecycle"]))
        self.assertNotIn("quarantin", FIXTURE.read_text())


if __name__ == "__main__":
    unittest.main()
