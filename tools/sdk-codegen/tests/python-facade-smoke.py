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

    # The package initializer uses this same star export. Check every emitted
    # public constructor, including generated input projections absent from IR.
    root_exports: dict[str, object] = {}
    exec("from facade_contract._models import *", root_exports)
    for name, constructor in vars(models).items():
        if inspect.isclass(constructor) and issubclass(constructor, dict) and constructor.__module__ == models.__name__ and not name.startswith("_"):
            assert root_exports.get(name) is constructor, f"Missing root constructor: {name}"
    inputs = {name for name in vars(event) if name.endswith("Input")}
    assert len(inputs) == 32, inputs
    assert inputs <= root_exports.keys(), inputs - root_exports.keys()

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

    # session.start inputs contain host facts only; the SDK supplies its manifest.
    fixture = Path(__file__).resolve().parents[3] / "fixtures/draft/http/observe-session-start.valid.json"
    session_event = json.loads(fixture.read_text())["params"]["event"]
    host_facts = {
        "session": session_event["session"],
        "trigger": session_event["trigger"],
        "harness": session_event["harness"],
        "permission_mode": session_event["permissionMode"],
        "items": session_event["items"],
    }
    session_input = event.SessionStartInput(**host_facts)
    assert session_input == {
        "session": host_facts["session"], "trigger": host_facts["trigger"],
        "harness": host_facts["harness"], "permissionMode": host_facts["permission_mode"],
        "items": host_facts["items"],
    }
    sdk_fields = {"manifest", "id", "type", "time", "source", "protocol_version"}
    assert sdk_fields.isdisjoint(inspect.signature(event.SessionStartInput).parameters)
    assert inspect.signature(event.SessionStart).parameters["manifest"].default is inspect.Parameter.empty
    envelope = {key: session_event[key] for key in ("id", "source", "time")}
    try:
        event.SessionStart(**envelope, **host_facts)
    except TypeError:
        pass
    else:
        raise AssertionError("Canonical session.start constructor must require manifest")
    full_session = event.SessionStart(**envelope, **host_facts, manifest=session_event["manifest"])
    assert wire.parse_session_start_event(full_session)["ok"]
    assert json.loads(wire.encode_session_start_event(full_session)) == full_session
    missing_manifest = dict(full_session)
    del missing_manifest["manifest"]
    assert not wire.parse_session_start_event(missing_manifest)["ok"]

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
    # Exercise the real constructed payload, including nested StrEnum values;
    # comparing enum values to strings alone misses parser preflight rejection.
    occurrence = event.ToolBefore(
        id="enum-event", source="urn:example:facade", time="2026-08-24T08:51:14Z",
        call=tool.Call(id="enum-call"), tool=input["tool"], path=tool.Path.NATIVE,
    )
    request = models.InterceptRequest(
        id="enum-event",
        params=models.InterceptRequestParams(
            event=occurrence, capabilities=declaration["capabilities"],
            state={"permission": "allow", "candidate": None},
        ),
    )
    parsed_request = wire.parse_intercept_request(request)
    assert parsed_request["ok"], parsed_request
    assert wire.parse_tool_before_event(occurrence)["ok"]
    assert wire._to_safe_json(declaration)["modes"] == ["intercept"]
    assert json.loads(wire.encode_intercept_request(request))["params"]["event"]["path"] == "native"
    response = json.loads((Path(__file__).resolve().parents[3] / "fixtures/draft/http/capabilities-response.valid.json").read_text())
    response["result"]["manifest"]["events"] = [{"event": "tool.before", **declaration}]
    parsed_declaration = wire.parse_capabilities_response(response)
    assert parsed_declaration["ok"], parsed_declaration
    if (directory / "runtime.py").is_file():
        # SDK consumer execution additionally uses its actual canonical Validator.
        validator = importlib.import_module("facade_contract.runtime").Validator()
        validator.validate("intercept-request", request)
        validator.validators["tool-before.schema.json"].validate(occurrence)
        validator.validate("capabilities", declaration["capabilities"])
        validator.validate("capabilities-response", response)

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
        for name, method in methods:
            assert inspect.signature(method).return_annotation == "HookResult"
            tag, returned, kwargs = await getattr(Hooks(), name)(input, marker="kept")
            assert returned is input and kwargs == {"marker": "kept"}
            assert tag.replace(".", "_") == name
            seen.add(tag)
        assert len(seen) == 32

    asyncio.run(verify_boundaries())
    print(f"Python facade: {constructors} constructors, 32 async boundaries, strict parser passed")


if __name__ == "__main__":
    main()
