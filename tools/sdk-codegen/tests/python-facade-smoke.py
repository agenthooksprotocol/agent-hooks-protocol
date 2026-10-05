#!/usr/bin/env python3
"""Runtime contract checks for all schema-generated Python facade artifacts."""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
from pathlib import Path
import sys
import types


def main() -> None:
    directory = Path(sys.argv[1]).resolve()
    package = types.ModuleType("facade_contract")
    package.__path__ = [str(directory)]
    sys.modules[package.__name__] = package
    models = importlib.import_module("facade_contract._models")
    wire = importlib.import_module("facade_contract.generated")
    event = importlib.import_module("facade_contract.event")
    effect = importlib.import_module("facade_contract.effect")
    capability = importlib.import_module("facade_contract.capability")
    tool = importlib.import_module("facade_contract.tool")
    registration = importlib.import_module("facade_contract._models.registration")
    boundaries = importlib.import_module("facade_contract._boundaries")

    constructors = 0
    for name in models.__all__:
        constructor = getattr(models, name)
        if not inspect.isclass(constructor) or not issubclass(constructor, dict):
            continue
        constructors += 1
        signature = inspect.signature(constructor)
        required = {
            name: "sentinel"
            for name, p in signature.parameters.items()
            if p.kind == inspect.Parameter.KEYWORD_ONLY
            and p.default == inspect.Parameter.empty
        }
        value = constructor(**required, vendor_field={"future": [1, None]})
        assert value["vendor_field"] == {"future": [1, None]}, constructor
        if required:
            try:
                constructor()
            except TypeError:
                pass
            else:
                raise AssertionError(f"{name} lost required arguments")
    assert constructors > 250, constructors

    assert event.Path.NATIVE == "native"
    assert tool.Origin.MCP == "mcp"
    input = event.ToolBeforeInput(
        call=tool.Call(id="call"),
        tool=tool.Input(name="read", origin=tool.Origin.NATIVE, input={}),
        path=event.Path.NATIVE,
        parent_event_id="parent",
    )
    assert input["parentEventId"] == "parent"
    assert "id" not in input and "type" not in input
    assert effect.Deny(reason="blocked") == {"type": "deny", "reason": "blocked"}
    value = registration.Registration(hooks=[])
    assert value["protocolVersion"] == wire.PROTOCOL_VERSION
    explicit = registration.Registration(hooks=[], protocol_version="future")
    assert explicit["protocolVersion"] == "future"
    missing = wire.parse_registration({"hooks": []})
    assert not missing["ok"], missing
    parsed = wire.parse_registration(value)
    assert parsed["ok"], parsed
    assert value == json.loads(wire.encode_registration(value))

    declaration = capability.Declaration(
        modes=[capability.Mode.INTERCEPT],
        grants=[capability.Allow(), capability.ModifyInput(replace=True), capability.ModifyInput(merge=True)],
    )
    assert declaration == {
        "modes": ["intercept"],
        "capabilities": {
            "effects": ["allow", "modify"],
            "modify": {"input": {"replace": True, "merge": True}},
        },
    }
    assert wire.parse_capabilities(declaration["capabilities"])["ok"]
    explicit_form = capability.Declaration(modes=["observe"], grants=[capability.ElicitationForm()])
    assert explicit_form == {"modes": ["observe"], "capabilities": {"effects": [], "elicitation": {"form": {}}}}
    assert "url" not in explicit_form["capabilities"]["elicitation"]
    assert capability.Declaration(modes=[], grants=[]) == {"modes": [], "capabilities": {"effects": []}}
    assert capability.Allow() == {"effects": ["allow"]}

    class Hooks(boundaries.BoundaryMixin):
        async def dispatch(self, event_name, input, **kwargs):
            return event_name, input, kwargs

    async def verify_boundaries():
        methods = inspect.getmembers(boundaries.BoundaryMixin, inspect.iscoroutinefunction)
        assert len(methods) == 32, len(methods)
        seen = set()
        for name, _ in methods:
            tag, returned, kwargs = await getattr(Hooks(), name)(input, marker="kept")
            assert returned is input and kwargs == {"marker": "kept"}
            assert tag.replace(".", "_") == name
            seen.add(tag)
        assert len(seen) == 32

    asyncio.run(verify_boundaries())
    print(f"Python facade: {constructors} constructors, 32 async boundaries, strict parser passed")


if __name__ == "__main__":
    main()
