#!/usr/bin/env python3
"""Test actual emitted constructors for original composed synthetic descriptors.

Generate using AHP_PYTHON_STRUCTURAL_OUTPUT=<directory> cargo test ...
original_composed_descriptors_and_union_ambiguity_are_cached, then pass directory.
"""
import importlib
from pathlib import Path
import sys
import types

package = types.ModuleType("structural_consumer")
package.__path__ = [str(Path(sys.argv[1]).resolve())]
sys.modules[package.__name__] = package
models = importlib.import_module("structural_consumer._models")
wire = importlib.import_module("structural_consumer.generated")
model = models.StructuralComposed
for value, accepted, code in [
    ({"choice": "text", "left": None}, True, None),
    ({"choice": 1}, False, "ambiguous_union"),
    ({"choice": True}, False, "no_union_match"),
    ({"choice": "text", "left": None, "right": None}, False, "forbidden_property"),
    ({}, False, "missing_required"),
]:
    parsed = wire.parse_structural_composed(value)
    assert parsed["ok"] == accepted, parsed
    for decode in (model.from_dict, lambda value: model(**value)):
        try:
            decoded = decode(value)
        except TypeError:
            assert value == {} and not accepted
        except ValueError as error:
            assert not accepted, error
            assert error.result == parsed
        else:
            assert accepted
            assert decoded == value
            assert wire.parse_structural_composed(decoded) == parsed
    if code is not None and code in {"ambiguous_union", "no_union_match"}:
        assert any(d["code"] == code for d in parsed["diagnostics"])
print("Python original composed descriptor/ambiguous union consumer passed")

for field in ("left", "right"):
    raw = {"items": [{field: "value"}]}
    decoded = models.StructuralHydration.from_dict(raw)
    assert getattr(decoded.items_[0], field) == "value"
    assert decoded == raw
print("Python union-of-array private hydration passed")

# Numeric annotation matching must follow the shared wire validator, not
# isinstance(value, float/int), or mixed arrays silently lose model hydration.
from decimal import Decimal
for model, parse, accepted, rejected in (
    (models.HydrationNumbers, wire.parse_hydration_numbers,
     [0, 1, 1.0, 1.25, Decimal("1"), Decimal("1.0000000000000000001")],
     [True, float("nan"), Decimal("Infinity")]),
    (models.HydrationIntegers, wire.parse_hydration_integers,
     [0, 1, 1.0, Decimal("1.0"), 2**53 - 1, Decimal(2**53 - 1)],
     [True, 1.25, Decimal("1.0000000000000000001"), 2**53, Decimal(2**53)]),
):
    for number in accepted:
        raw = {"items": [number, {"left": "ok"}]}
        result = parse(raw)
        assert result["ok"], result
        decoded = model.from_dict(raw)
        assert decoded.items_[1].left == "ok", (model, number)
        assert type(decoded.items_[0]) is type(number)
        assert decoded == raw
        assert parse(decoded) == result
    for number in rejected:
        raw = {"items": [number, {"left": "ok"}]}
        assert not parse(raw)["ok"], (model, number)
        try:
            model.from_dict(raw)
        except ValueError:
            pass
        else:
            raise AssertionError((model, number))
print("Python mixed numeric/model array hydration passed")
