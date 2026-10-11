#!/usr/bin/env python3
"""Check generated public annotations against a read-only sibling Python runtime.

Usage: python-public-api-smoke.py <generated-facade-directory> <runtime-package-directory>
The generated directory must also contain generated.py. All overlays are temporary.
"""
from __future__ import annotations

from copy import copy, deepcopy
import importlib
import inspect
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import typing


def check(package_dir: Path, first: str) -> None:
    sys.path.insert(0, str(package_dir.parent))
    package = package_dir.name
    # The root initializer is runtime-owned, not a generator artifact. Bypass it
    # to exercise both true submodule import orders, rather than its eager order.
    root = types.ModuleType(package)
    root.__path__ = [str(package_dir)]
    sys.modules[package] = root
    importlib.import_module(f"{package}.{first}")
    boundaries = importlib.import_module(f"{package}._boundaries")
    hooks = importlib.import_module(f"{package}._hooks")
    models = importlib.import_module(f"{package}._models")
    content = importlib.import_module(f"{package}._models.content")
    contract = importlib.import_module(f"{package}.contract")
    wire = importlib.import_module(f"{package}.generated")
    assert typing.is_typeddict(wire.ParseDiagnostic)
    assert wire.ToolCandidate is not models.ToolCandidate
    assert not typing.is_typeddict(models.ToolCandidate)
    assert content.OwnedAttachment is models.OwnedAttachment
    assert "OwnedAttachment" not in models.__all__
    assert not hasattr(contract, "dataclass") and not hasattr(contract, "field")
    public: dict[str, object] = {}
    exec(f"from {package}.contract import *", public)
    assert set(public) - {"__builtins__"} == set(contract.__all__)
    assert [p.__name__ for p in contract.Candidate.__parameters__] == ["T", "P"]
    assert [p.__name__ for p in contract.ElicitResult.__parameters__] == ["T"]

    # Omitted projection fields survive runtime copies without becoming wire data.
    assert copy(models._UNSET) is models._UNSET
    assert deepcopy(models._UNSET) is models._UNSET
    state = importlib.import_module(f"{package}.state")
    assert state._UNSET is models._UNSET
    assert deepcopy(contract.Candidate(value={"opaque": [None]})).to_dict() == {
        "value": {"opaque": [None]}
    }
    assert deepcopy(contract.ElicitResult(action="decline")).to_dict() == {
        "action": "decline"
    }

    # Reading effects returns a detached plain typed list, never inserts absence.
    response = importlib.import_module(f"{package}.response")
    for result in (
        {"protocolVersion": "draft"},
        {"protocolVersion": "draft", "effects": []},
        {"protocolVersion": "draft", "effects": [{"type": "allow"}]},
    ):
        raw = {"jsonrpc": "2.0", "id": "smoke", "result": result}
        parsed = response.response_for_request("tool.before", raw)
        assert parsed["ok"], parsed
        model = parsed["value"]
        before = deepcopy(model)
        effects = model.result.effects
        assert type(effects) is list
        assert len(effects) == len(result.get("effects", []))
        if effects:
            assert isinstance(effects[0], models.AllowEffect)
        effects.clear()
        effects.append(models.AllowEffect())
        assert model == before
        encoded = wire.encode_intercept_response(model)
        assert wire.parse_intercept_response(encoded)["value"] == raw

    capability = importlib.import_module(f"{package}.capability")
    builder = capability.intercept()
    for field in ("continuation_count", "remaining_continuations", "max_continuations"):
        for invalid in (-1, True, False, 0.5, 1.0):
            try:
                builder.flow(operations=["stop"], **{field: invalid})
            except ValueError:
                pass
            else:
                raise AssertionError((field, invalid))
    for value in (0, 2**53, 10**100):
        grant = builder.flow(
            operations=["continue"],
            continuation_count=value,
            remaining_continuations=value,
            max_continuations=value,
        ).to_wire()["capabilities"]["flow"]
        assert grant["continuationCount"] == value
        assert grant["remainingContinuations"] == value
        assert grant["maxContinuations"] == value

    dispatch = inspect.signature(hooks.Hooks.dispatch)
    methods = inspect.getmembers(boundaries.BoundaryMixin, inspect.iscoroutinefunction)
    assert methods
    for name, method in methods:
        hints = typing.get_type_hints(method)  # No caller-supplied globals or locals.
        assert typing.get_origin(hints["return"]) is hooks.HookResult, name
        parameters = typing.get_args(hints["return"])
        assert parameters == (typing.Any, typing.Any, typing.Any)
        overloads = typing.get_overloads(method)
        assert len(overloads) == 8
        default = typing.get_type_hints(overloads[0])["return"]
        result_type = {"model_request_before": models.CanonicalMessages, "context_compact_before": models.TextParts, "user_elicitation_request": models.McpElicitationElicitResult}.get(name, object)
        assert typing.get_args(default) == (object, result_type, object)
        for codec_name, parameter in zip(
            ("input_codec", "result_codec", "provenance_codec"), parameters
        ):
            codec, none = typing.get_args(hints[codec_name])
            assert none is type(None)
            assert typing.get_args(codec) == (parameter,), (name, codec_name)
        signature = inspect.signature(method)
        assert set(signature.parameters) == set(dispatch.parameters) - {"event_name"}
        for key, parameter in signature.parameters.items():
            assert parameter.kind == dispatch.parameters[key].kind, (name, key)
            assert parameter.default == dispatch.parameters[key].default, (name, key)
    print(f"Python public API: {len(methods)} resolvable codec-correlated boundaries ({first} first)")


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == "--check":
        check(Path(sys.argv[2]), sys.argv[3])
        return
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    generated, runtime = map(lambda p: Path(p).resolve(), sys.argv[1:])
    with tempfile.TemporaryDirectory(prefix="ahp-python-public-api-") as temporary:
        package = Path(temporary) / runtime.name
        shutil.copytree(runtime, package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(generated, package, dirs_exist_ok=True)
        for first in ("_boundaries", "_hooks"):
            subprocess.run([sys.executable, __file__, "--check", str(package), first], check=True)


if __name__ == "__main__":
    main()
