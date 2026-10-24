"""Server-driven configuration wire grammar and precedence inputs."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import check_conformance as checker


class ServerConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = checker.Snapshot.resolve(ROOT)
        self.store = checker.SchemaStore(self.snapshot)
        self.validator = checker.SubsetValidator(self.store)
        self.response = json.loads((ROOT / "fixtures/draft/http/configuration-response.valid.json").read_text())
        self.registration = json.loads((ROOT / "fixtures/draft/registration/configuration-discovery.valid.json").read_text())

    def errors(self, value, name):
        filename = "schema.json" if name == "schema" else name + ".schema.json"
        path = self.snapshot.schema_dir / filename
        return self.validator.validate(value, self.store.load(path), path)

    def test_omission_and_explicit_empty_are_distinct(self):
        self.assertFalse(self.errors(self.registration, "registration"))
        backend = self.registration["hooks"][0]
        for value in ([], None, {}, "discover"):
            with self.subTest(value=value):
                backend["subscriptions"] = value
                self.assertTrue(self.errors(self.registration, "registration"))
        backend["subscriptions"] = self.response["result"]["configuration"]["subscriptions"]
        self.assertFalse(self.errors(self.registration, "registration"))

    def test_configuration_reuses_registration_subscription_constraints(self):
        config = self.response["result"]["configuration"]
        self.registration["hooks"][0]["subscriptions"] = config["subscriptions"]
        for field, invalid in (("timeoutMs", 0), ("failurePolicy", "ignore"), ("mode", "observe"), ("content", {}), ("events", [])):
            with self.subTest(field=field):
                altered = copy.deepcopy(config)
                altered["subscriptions"][0][field] = invalid
                self.assertTrue(self.errors(altered, "configuration"))
                self.registration["hooks"][0]["subscriptions"] = altered["subscriptions"]
                self.assertTrue(self.errors(self.registration, "registration"))

    def test_configuration_response_is_not_an_interception_response(self):
        self.assertFalse(self.errors(self.response, "configuration-response"))
        self.assertFalse(self.errors(self.response, "schema"))
        for name in ("intercept-response", "intercept-no-effect-response", "intercept-deny-response"):
            with self.subTest(name=name):
                self.assertTrue(self.errors(self.response, name))
        for field in ("effects", "manifest"):
            mixed = copy.deepcopy(self.response)
            mixed["result"][field] = [] if field == "effects" else {}
            self.assertTrue(self.errors(mixed, "configuration-response"))
            self.assertTrue(self.errors(mixed, "schema"))

    def test_discovery_requires_version_and_configuration(self):
        for field in ("protocolVersion", "configuration"):
            value = copy.deepcopy(self.response)
            del value["result"][field]
            self.assertTrue(self.errors(value, "configuration-response"))
        value = copy.deepcopy(self.response)
        value["result"]["protocolVersion"] = "unknown"
        self.assertTrue(self.errors(value, "configuration-response"))

    def test_unknown_configuration_fields_are_not_connection_authority(self):
        value = copy.deepcopy(self.response)
        config = value["result"]["configuration"]
        config["transport"] = {"url": "https://untrusted.example"}
        config["authentication"] = {"tokenRef": "untrusted"}
        # Base unknown-field tolerance is preserved. Clients only consume subscriptions.
        self.assertFalse(self.errors(value, "configuration-response"))


if __name__ == "__main__":
    unittest.main()
