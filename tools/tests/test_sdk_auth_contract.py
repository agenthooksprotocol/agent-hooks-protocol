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
                 ("namespace", "issuer", "resource", "client_id", "flow")] +
                 [" ".join(sorted(set(value["allowed_scopes"]))), partition])


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
        for name in ("secret-and-capability-rotation", "scope-order-dedup"):
            self.assertEqual(cases["default-event"]["grant_key"], cases[name]["grant_key"])
        self.assertNotEqual(cases["default-event"]["grant_key"], cases["resource-trailing-slash"]["grant_key"])
        self.assertNotEqual(cases["default-event"]["grant_key"], cases["scope-ceiling-change"]["grant_key"])

    def test_response_vectors_separate_eligibility_from_rotation(self):
        for row in self.vectors["token_responses"]:
            with self.subTest(row["id"]):
                response = row["response"]
                inherited = row.get("previous_granted", row["requested"]) if row.get("exchange") == "refresh" else row["requested"]
                granted = set(response.get("scope", " ".join(inherited)).split())
                accepted = granted <= set(row["allowed"])
                self.assertEqual(accepted, row["accepted"])
                self.assertEqual(accepted and set(row["required"]) <= granted, row["usable"])
                self.assertTrue(row["refresh_preserved"])
                if accepted:
                    self.assertEqual("known" if "expires_in" in response else "unknown", row["expiry"])
                    self.assertEqual(granted, set(row["granted"]))

    def test_lifecycle_coverage_manifest_is_well_formed(self):
        ids = [row["id"] for row in self.vectors["lifecycle"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(row["expect"] for row in self.vectors["lifecycle"]))


if __name__ == "__main__":
    unittest.main()
