#!/usr/bin/env python3
"""Smoke tests for a generated AHP Python SDK module."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from decimal import Decimal
from types import ModuleType
from typing import Any


def fail(message: str) -> None:
    raise RuntimeError(message)


def load_module(path: pathlib.Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("generated_ahp_sdk", path)
    if spec is None or spec.loader is None:
        fail(f"cannot load generated module at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fixture(repository: pathlib.Path, relative: str) -> Any:
    return json.loads((repository / relative).read_text(encoding="utf-8"))


def diagnostic_codes(result: dict[str, Any]) -> set[str]:
    return {item["code"] for item in result["diagnostics"]}


def main() -> None:
    if sys.version_info < (3, 11):
        fail("python-smoke.py requires Python 3.11+")
    if len(sys.argv) != 3:
        fail("usage: python-smoke.py <generated.py> <repository>")

    sdk = load_module(pathlib.Path(sys.argv[1]).resolve())
    repository = pathlib.Path(sys.argv[2]).resolve()

    registration = fixture(
        repository, "fixtures/draft/registration/portable.valid.json"
    )
    registration["futureRoot"] = {"nested": True}
    registration["hooks"][0]["transport"]["futureNested"] = 7
    result = sdk.parse_registration(registration)
    if not result["ok"]:
        fail(str(result["diagnostics"]))
    encoded = json.loads(sdk.encode_registration(result["value"]))
    if encoded != registration:
        fail("registration did not round-trip as semantic JSON")
    if not encoded["futureRoot"]["nested"] or encoded["hooks"][0]["transport"]["futureNested"] != 7:
        fail("recursive unknown properties were not preserved")

    precise_number = "0.123456789012345678901234567890123456789"
    registration["futurePrecision"] = "__AHP_PRECISE_NUMBER__"
    precise_input = json.dumps(registration, separators=(",", ":")).replace(
        '"__AHP_PRECISE_NUMBER__"', precise_number
    )
    result = sdk.parse_registration(precise_input)
    if not result["ok"]:
        fail(str(result["diagnostics"]))
    if result["value"]["futurePrecision"] != Decimal(precise_number):
        fail("JSON decimal precision was lost while parsing")
    precise_encoded = sdk.encode_registration(result["value"])
    if precise_number not in precise_encoded:
        fail("JSON decimal precision was lost while encoding")
    if json.loads(precise_encoded, parse_float=Decimal)["futurePrecision"] != Decimal(precise_number):
        fail("precise JSON number did not round-trip exactly")
    del registration["futurePrecision"]

    integer_diagnostics: list[dict[str, Any]] = []
    sdk._check_node({"kind": "integer"}, Decimal("1.0"), "", integer_diagnostics)
    if integer_diagnostics:
        fail("mathematically integral JSON number 1.0 was rejected as an integer")
    sdk._check_node(
        {"kind": "integer"},
        Decimal("9007199254740992.0"),
        "",
        integer_diagnostics,
    )
    if "invalid_type" not in {item["code"] for item in integer_diagnostics}:
        fail("integer safe-range limit was not enforced for an integral decimal")

    lone_surrogate = "\ud800"
    registration["futureSurrogate"] = lone_surrogate
    result = sdk.parse_registration(registration)
    if not result["ok"]:
        fail(str(result["diagnostics"]))
    surrogate_json = sdk.encode_registration(result["value"])
    surrogate_json.encode("utf-8")
    if "\\ud800" not in surrogate_json or json.loads(surrogate_json)["futureSurrogate"] != lone_surrogate:
        fail("lone surrogate was not safely escaped in encoded JSON")

    registration["hooks"][0]["transport"] = {
        "type": "future",
        "deeply": {"preserved": True},
    }
    result = sdk.parse_registration(registration)
    if not result["ok"] or "unknown_variant" not in diagnostic_codes(result):
        fail("unknown discriminator variant was not preserved")
    encoded = json.loads(sdk.encode_registration(result["value"]))
    if encoded != registration:
        fail("unknown variant registration did not round-trip as semantic JSON")
    if not encoded["hooks"][0]["transport"]["deeply"]["preserved"]:
        fail("unknown variant payload was lost")

    no_default = fixture(
        repository, "fixtures/draft/registration/portable.valid.json"
    )
    del no_default["hooks"][1]["subscriptions"][1]["includeNative"]
    result = sdk.parse_registration(no_default)
    if not result["ok"]:
        fail(str(result["diagnostics"]))
    encoded = json.loads(sdk.encode_registration(result["value"]))
    if encoded != no_default:
        fail("registration with an absent default did not round-trip as semantic JSON")
    if "includeNative" in encoded["hooks"][1]["subscriptions"][1]:
        fail("decoder fabricated an absent default")

    request = fixture(
        repository, "fixtures/draft/http/intercept-request.valid.json"
    )
    integral_request = json.dumps(request, separators=(",", ":")).replace(
        json.dumps(request["id"]), "1.0", 1
    )
    integral_result = sdk.parse_json_rpc_message(integral_request)
    if not integral_result["ok"] or integral_result["value"]["id"] != Decimal("1.0"):
        fail(f"JSON-RPC integer 1.0 was not accepted: {integral_result['diagnostics']}")
    if '"id":1.0' not in sdk.encode_json_rpc_message(integral_result["value"]):
        fail("JSON-RPC integer 1.0 did not encode exactly")

    result = sdk.parse_json_rpc_message(request)
    if not result["ok"]:
        fail(f"request envelope did not select exactly one branch: {result['diagnostics']}")
    request["params"]["event"]["tool"]["kind"] = "future_tool"
    result = sdk.parse_intercept_request(request)
    if not result["ok"] or "unknown_enum" not in diagnostic_codes(result):
        fail(f"unknown enum value was not preserved: {result['diagnostics']}")

    deny = fixture(
        repository, "fixtures/draft/http/deny-response.valid.json"
    )
    deny["result"]["effects"][0]["code"] = None
    result = sdk.parse_intercept_deny_response(deny)
    if result["ok"] or "raw" not in result or result["raw"]["result"]["effects"][0]["code"] is not None:
        fail("invalid explicit null was not retained separately from absence")

    invalid_json = sdk.parse_registration("{not json")
    if invalid_json["ok"] or "raw" in invalid_json or "invalid_json" not in diagnostic_codes(invalid_json):
        fail("invalid JSON did not produce a raw-less structural diagnostic")

    ambiguous = {
        "jsonrpc": "2.0",
        "id": "event-1",
        "result": {},
        "error": {"code": -32600, "message": "bad"},
    }
    result = sdk.parse_json_rpc_message(ambiguous)
    if result["ok"] or "no_union_match" not in diagnostic_codes(result):
        fail("invalid result/error envelope selected a union branch")

    ambiguous_diagnostics: list[dict[str, Any]] = []
    sdk._check_node(
        {
            "kind": "union",
            "mode": "oneOf",
            "variants": [{"kind": "any"}, {"kind": "any"}],
        },
        {},
        "",
        ambiguous_diagnostics,
    )
    if "ambiguous_union" not in {item["code"] for item in ambiguous_diagnostics}:
        fail("oneOf ambiguity did not produce ambiguous_union")

    malformed = fixture(
        repository, "fixtures/draft/registration/portable.valid.json"
    )
    malformed["hooks"][0]["transport"] = {"type": "http"}
    result = sdk.parse_registration(malformed)
    if result["ok"] or "invalid_known_variant" not in diagnostic_codes(result):
        fail("malformed known variant fell back instead of failing")

    print("generated Python codec smoke tests passed")


if __name__ == "__main__":
    main()
