"""Canonical validation of captured observation messages; no state interpreter."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import check_conformance as checker


class ObservationValidator:
    def __init__(self):
        self.snapshot = checker.Snapshot.resolve(ROOT)
        self.store = checker.SchemaStore(self.snapshot)
        self.validator = checker.SubsetValidator(self.store)

    def schema_errors(self, value, name):
        path = self.snapshot.schema_dir / (name + '.schema.json')
        return self.validator.validate(value, self.store.load(path), path)

    def errors(self, message):
        # Both are canonical files, never patched/overlaid schemas. Direct
        # component validation also fails closed before coordinator wiring.
        errors = self.schema_errors(message, 'observe-notification')
        params = message.get('params') if isinstance(message, dict) else None
        if not isinstance(params, dict):
            return errors + ['observation params missing']
        sub = params.get('subscriptionId')
        if not isinstance(sub, str) or not sub:
            errors.append('observation subscriptionId missing')
        return errors


def same_boundary_errors(intercept, observations):
    """Compare actual wire identities, never hard-coded or expected event IDs."""
    original = intercept['params']['event']
    errors = []
    for message in observations:
        event = message['params']['event']
        if any(event.get(k) != original.get(k) for k in ('source', 'id', 'type')):
            errors.append('observation changed logical boundary identity')
    return errors
