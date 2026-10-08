#!/usr/bin/env python3
"""Runtime contract checks for all schema-generated Python facade artifacts.

Pass --typing to run actual generated consumer checks with mypy on PATH.
"""
from __future__ import annotations

import asyncio
import ast
import importlib
import inspect
import json
import re
from pathlib import Path
import sys
import types
import typing
import subprocess
import tempfile


def audit_public_names(directory: Path) -> None:
    """Audit actual emitted names, not only hand-picked generator hints.

    Private low-level TypedDict names deliberately retain FieldN path hints.
    Those are implementation details, not public models or constructors.
    """
    count = 0
    forbidden = re.compile(r"(?:Variant|Field)\d|_\d+$|[0-9a-fA-F]{12}|ObjectWith|Required[A-Z].*Optional[A-Z]")

    def check(name: str, source: Path) -> None:
        nonlocal count
        if name.startswith("_"):
            return
        count += 1
        assert len(name) <= 100, (source, "unbounded public name", name)
        assert not forbidden.search(name), (source, "nonsemantic public name", name)

    def scope(nodes: list[ast.stmt], source: Path) -> None:
        declarations: set[str] = set()
        for node in nodes:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                check(node.name, source)
                assert node.name not in declarations, (source, "overwritten declaration", node.name)
                declarations.add(node.name)
                if isinstance(node, ast.ClassDef):
                    scope(node.body, source)
                else:
                    for arg in ast.walk(node.args):
                        if isinstance(arg, ast.arg):
                            check(arg.arg, source)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        check(target.id, source)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    check(alias.asname or alias.name, source)
            elif isinstance(node, ast.If):
                scope(node.body, source)
                scope(node.orelse, source)

    for source in sorted(directory.rglob("*.py")):
        scope(ast.parse(source.read_text()).body, source)
    assert count > 2000, count


def check_structured_consumers(directory: Path, models, wire) -> None:
    """Exercise actual emitted constructors/accessors and unchanged wire boundaries."""
    for name in models.__all__:
        constructor = getattr(models, name)
        if isinstance(constructor, type) and issubclass(constructor, dict):
            # An annotation pointing to an undeclared inline model is not typed.
            typing.get_type_hints(constructor.__init__)
            for member_name, member in vars(constructor).items():
                if isinstance(member, property) and member_name != "content_sources":
                    typing.get_type_hints(member.fget)

    for suffix, tag, facts in [
        ("Http", "http", {"url": "https://example.test/mcp"}),
        ("Sse", "sse", {"url": "https://example.test/sse"}),
        ("Stdio", "stdio", {"command": "mcp", "args": ["--stdio"], "cwd": "/tmp"}),
        ("CustomTransport", "vendor.pipe", {"address": "pipe", "address_form": "vendor.pipe"}),
    ]:
        connection_type = getattr(models, "ExecutionEventMcpConnection" + suffix)
        gap_type = getattr(models, "ExecutionEventMcpConnection" + suffix + "GapsItem")
        gap = gap_type(path="url", reason="unavailable")
        connection = connection_type(transport=tag, gaps=[gap], **facts)
        decoded = connection_type.from_dict(dict(connection))
        assert isinstance(decoded.gaps[0], gap_type)
        assert decoded.gaps[0].reason == "unavailable"
        assert decoded.gaps[0].path == "url"
        assert decoded == connection
        assert connection.gaps[0].reason == "unavailable"
        assert connection.gaps[0].path == "url"
        assert connection.transport == tag
        assert connection_type(transport=tag, **facts).gaps is None
        assert callable(connection.items)  # Mapping API remains available.
        mcp = models.ExecutionEventMcp(
            connection=connection, provenance="runtime",
            server=models.ExecutionEventMcpServer(id="server"), tool_name="read",
        )
        event = models.ToolBeforeEvent(
            id="event", source="test", time="2026-01-01T00:00:00Z", path="native",
            call=models.ToolBeforeEventCall(id="call"),
            tool=models.ExecutionEventTool(name="read", origin="mcp", input={}, mcp=mcp),
        )
        # Full event hydration selects the matching composed connection variant,
        # and shared validation memoization checks each descriptor/path once.
        checks: dict[tuple[str, str], int] = {}
        original_check = wire._check_node_impl
        def counted(schema, value, path, diagnostics, cache):
            key = (wire._schema_key(schema), path)
            checks[key] = checks.get(key, 0) + 1
            return original_check(schema, value, path, diagnostics, cache)
        wire._check_node_impl = counted
        try:
            hydrated = models.ToolBeforeEvent.from_dict(dict(event))
        finally:
            wire._check_node_impl = original_check
        assert hydrated.tool.mcp.connection.gaps[0].reason == "unavailable"
        assert hydrated.tool.mcp.connection.transport == tag
        assert max(checks.values()) == 1, checks
        result = wire.parse_tool_before_event(event)
        assert result["ok"], result["diagnostics"]
        assert result["value"]["tool"]["mcp"]["connection"] == connection
        if tag != "vendor.pipe":
            # SDK constructors now reject the same malformed combinations as Parse.
            for invalid in ({}, {"gaps": [{"path": 7, "reason": False}]}):
                try:
                    connection_type(**invalid)
                except ValueError:
                    pass
                else:
                    raise AssertionError("Malformed connection constructed")
                event["tool"]["mcp"]["connection"] = {"transport": tag, **invalid}
                assert not wire.parse_tool_before_event(event)["ok"]
        else:
            # Custom transport remains the existing unknown-variant extension
            # path; adding types must not silently tighten that boundary.
            assert any(d["code"] == "unknown_variant" for d in result["diagnostics"])

    visible = models.ModelVisibleItemMetadata(
        id="item", kind="text", media_type="text/plain", role="user", future="preserved",
    )
    compact = models.ExecutionEventContextCompactBefore(
        id="event", source="test", time="2026-01-01T00:00:00Z", trigger="auto", items=[visible],
    )
    assert compact.items_[0].role == "user"
    assert wire.parse_execution_event(compact)["ok"]
    assert wire.parse_execution_event(compact)["value"]["items"][0]["future"] == "preserved"
    del visible["role"]
    assert not wire.parse_execution_event(compact)["ok"]
    visible["role"] = "user"
    visible["selection"] = "body"  # body selection requires body OR gap evidence.
    assert not wire.parse_execution_event(compact)["ok"]
    visible["gap"] = {"reason": "unavailable"}
    assert wire.parse_execution_event(compact)["ok"]
    visible["body"] = {"ref": "urn:test:content"}
    assert not wire.parse_execution_event(compact)["ok"]  # mutually exclusive evidence
    assert typing.get_type_hints(models.ExecutionEventTool.__init__)["input"] == dict[str, typing.Any]

    assert callable(models.SessionStartEvent.items)
    assert isinstance(models.SessionStartEvent.items_, property)

    if "--typing" not in sys.argv:
        return
    with tempfile.TemporaryDirectory(prefix="ahp-python-typing-") as temporary:
        root = Path(temporary)
        (root / "consumer_sdk").symlink_to(directory, target_is_directory=True)
        consumer = root / "consumer.py"
        lines = ["from typing import assert_type", "from consumer_sdk import _models as m"]
        for suffix in ["Http", "Sse", "Stdio", "CustomTransport"]:
            owner = "ExecutionEventMcpConnection" + suffix
            extra = ', transport="vendor.pipe"' if suffix == "CustomTransport" else ""
            lines.extend([
                f'g_{suffix} = m.{owner}GapsItem(path="url", reason="unavailable")',
                # Distinct names avoid mypy reassigning nominal facade classes.
                f'c_{suffix} = m.{owner}(gaps=[g_{suffix}]{extra})',
                f'assert c_{suffix}.gaps is not None',
                f'assert_type(c_{suffix}.gaps[0].reason, str)',
                f'd_{suffix} = m.{owner}.from_dict(dict(c_{suffix}))',
                f'assert_type(d_{suffix}, m.{owner})',
                f'assert d_{suffix}.gaps is not None',
                f'assert_type(d_{suffix}.gaps[0].reason, str)',
                f'assert_type(c_{suffix}.gaps[0].path, str)',
                f'm.{owner}(gaps=[42]{extra})  # type: ignore[list-item]',
                f'm.{owner}GapsItem(path="url", reason=42)  # type: ignore[arg-type]',
            ])
        lines.extend([
            's = m.ExecutionEventMcpConnectionStdio(command="mcp", args=["--stdio"], cwd="/tmp")',
            'assert s.args is not None',
            'assert_type(s.args[0], str)',
            'assert_type(s.command, str | None)',
            'assert_type(s.cwd, str | None)',
            's_bad = m.ExecutionEventMcpConnectionStdio(args=[42])  # type: ignore[list-item]',
            'h = m.ExecutionEventMcpConnectionHttp(url="https://example.test")',
            'assert_type(h.url, str | None)',
            'sse = m.ExecutionEventMcpConnectionSse(url="https://example.test")',
            'assert_type(sse.url, str | None)',
            'custom = m.ExecutionEventMcpConnectionCustomTransport(transport="vendor.pipe", address="pipe", address_form="vendor.pipe")',
            'assert_type(custom.address, str | None)',
            'assert_type(custom.address_form, str | None)',
            'assert_type(custom.transport, str)',
            'visible = m.ModelVisibleItemMetadata(id="item", kind="text", media_type="text/plain", role="user")',
            'assert_type(visible.role, str)',
            'compact = m.ExecutionEventContextCompactBefore(id="event", source="test", time="now", trigger="auto", items=[visible])',
            'assert_type(compact.items_[0].role, str)',
            'm.ModelVisibleItemMetadata(id="item", kind="text", media_type="text/plain", role=42)  # type: ignore[arg-type]',
            'm.ModelVisibleItemMetadata(id="item", kind="text", media_type="text/plain")  # type: ignore[call-arg]',
            'm.ExecutionEventTool(name="read", origin="native", input={"genuinely": ["arbitrary", 42, None]})',

        ])
        consumer.write_text("\n".join(lines) + "\n")
        checked = subprocess.run(
            ["mypy", "--follow-imports=silent", "--ignore-missing-imports", "--warn-unused-ignores", str(consumer)],
            cwd=root, text=True, capture_output=True,
        )
        assert checked.returncode == 0, checked.stdout + checked.stderr


def check_structural_acceptance(models, wire) -> None:
    matrix = json.loads(Path(__file__).with_name("structural-acceptance.json").read_text())
    for case in matrix["cases"]:
        constructor = getattr(models, "".join(part.title() for part in case["root"].split("_")))
        parse = getattr(wire, "parse_" + case["root"])
        result = parse(case["value"])
        assert result["ok"] == case["accepted"], (case["id"], result)
        assert any(d["severity"] == "warning" for d in result["diagnostics"]) == case["warning"], case["id"]
        try:
            value = constructor.from_dict(case["value"])
        except ValueError as error:
            assert not case["accepted"], (case["id"], error)
            assert error.result == result, case["id"]
        else:
            assert case["accepted"], case["id"]
            assert value == case["value"], case["id"]
            assert parse(value) == result, case["id"]
            assert parse(json.dumps(value))["value"] == result["value"], case["id"]
    # Constructors and dictionary decode share validation but never fill missing
    # wire literals/defaults during decode.
    for invalid in (lambda: models.DenyEffect(type="allow", reason="test"),
                    lambda: models.InterceptRequestParamsState(permission=42, candidate=None),
                    lambda: models.InterceptRequestParamsState.from_dict({"permission": "allow"}),
                    lambda: models.DenyEffect.from_dict({})):
        try:
            invalid()
        except ValueError:
            pass
        else:
            raise AssertionError("Malformed facade model accepted")
    for candidate in (None, {"value": None}, {"value": 0}):
        value = {"permission": "allow", "candidate": candidate}
        assert models.InterceptRequestParamsState.from_dict(value) == value
    from decimal import Decimal
    precise = {"ref": "opaque", "extension": Decimal("1.00000000000000000000001")}
    assert models.ContentReference.from_dict(precise)["extension"] == precise["extension"]
    for nonfinite in (float("inf"), float("nan"), Decimal("NaN")):
        try:
            models.ContentReference(ref="opaque", extension=nonfinite)
        except ValueError:
            pass
        else:
            raise AssertionError("Non-JSON numeric extension accepted")


def main() -> None:
    directory = Path(sys.argv[1]).resolve()
    audit_public_names(directory)
    package = types.ModuleType("facade_contract")
    package.__path__ = [str(directory)]
    sys.modules[package.__name__] = package
    models = importlib.import_module("facade_contract._models")
    wire = importlib.import_module("facade_contract.generated")
    assert typing.get_type_hints(models.InterceptRequestParams.__init__)["event"] == models.Event
    assert typing.get_type_hints(models.ObserveNotificationParams.__init__)["event"] == models.Event
    receipt = models.ContentUploadReceipt(ref="opaque", size=0, sha256="a" * 64)
    assert wire.parse_content_upload_receipt(receipt)["ok"]
    assert wire.parse_content_reference(models.ContentReference(ref="opaque"))["ok"]
    check_structural_acceptance(models, wire)
    check_structured_consumers(directory, models, wire)
    event = importlib.import_module("facade_contract.event")
    effect = importlib.import_module("facade_contract.effect")
    capability = importlib.import_module("facade_contract.capability")
    assert not hasattr(models.InterceptDenyResponseResult, "supports")
    assert not hasattr(models.InterceptNoEffectResponseResult, "supports")
    assert effect.EffectName is capability.EffectName
    for cls in (capability.Capabilities, models.InterceptRequestParamsCapabilities):
        value = cls.from_dict({"effects": ["deny", "vendor.custom"], "modify": {"input": {"replace": True, "merge": False}}})
        assert value.supports(capability.EffectName.DENY)
        assert value.supports("vendor.custom")
        assert not value.supports(capability.EffectName.MODIFY)
        assert not cls(effects=[]).supports(capability.EffectName.DENY)
    assert capability.Event is models.CapabilitiesResponseResultManifestEventsItemEvent
    assert capability.Event.TOOL_BEFORE == "tool.before"
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
        try:
            value = constructor(**required, vendor_field={"future": [1, None]})
        except ValueError as error:
            # Arbitrary placeholders are not valid schema values. Rejection is
            # now the contract, and the validation error retains raw extensions.
            assert error.raw["vendor_field"] == {"future": [1, None]}, constructor
        else:
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
    # Named sources remain out-of-band; binding neither copies nor reads streams.
    class UnreadSource:
        def __deepcopy__(self, memo):
            raise AssertionError("Source entered wire copy")

    source = UnreadSource()
    bound = session_input.bind_items_source(source, index=0)
    assert session_input.content_sources == {}
    assert bound.content_sources == {"session.start.items[0]": source}
    assert bound.to_wire() == session_input.to_wire()
    assert json.dumps(bound.to_wire()) == json.dumps(session_input.to_wire())
    bound.content_sources.clear()
    assert bound.content_sources["session.start.items[0]"] is source
    assert boundaries.CONTENT_SOURCE_SLOTS["session.start.items"] == ("session.start", ("items", "*"))
    assert boundaries.CONTENT_SOURCE_SLOTS["tool.after.file_changes_after"] == ("tool.after", ("fileChanges", "*", "after"))
    assert boundaries.CONTENT_SOURCE_SLOTS["context.compact.before.instructions"] == ("context.compact.before", ("instructions",))
    for index in [-1, True, "0"]:
        try:
            session_input.bind_items_source(source, index=index)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid source index accepted")
    sdk_fields = {"manifest", "type", "source", "protocol_version"}
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
        call_id="call", name="read", origin=tool.Origin.NATIVE, input={},
        path=event.Path.NATIVE,
        parent_event_id="parent",
    )
    assert input["parentEventId"] == "parent"
    assert input.to_wire() == {
        "call": {"id": "call"}, "tool": {"name": "read", "origin": "native", "input": {}},
        "path": "native", "parentEventId": "parent",
    }
    input.to_wire()["tool"]["input"]["changed"] = True
    assert input["input"] == {}
    assert effect.replace_input({"x": 1}) == effect.Modify(target="input", operation="replace", value={"x": 1})
    assert effect.merge_input({"x": 1})["operation"] == "merge"
    assert wire.parse_effect(effect.replace_input({"x": 1}))["ok"]
    assert effect.flow_continue() == {"type": "flow", "operation": "continue"}
    assert effect.inject_context_append(value=[], deliver_at="now") == {"type": "inject", "operation": "append", "target": "context", "value": [], "deliverAt": "now"}
    state = importlib.import_module("facade_contract.state")
    candidate = importlib.import_module("facade_contract.candidate")
    diagnostics = importlib.import_module("facade_contract.diagnostics")
    assert state.initial(state.Permission.NONE) == {"permission": "none", "candidate": None}
    assert state.initial(state.Permission.ALLOW, candidate=candidate.value(None))["candidate"] == {"value": None}
    # Omission, explicit null, and a present candidate carrying JSON null differ.
    try:
        state.State(permission="allow")
    except TypeError:
        pass
    else:
        raise AssertionError("Missing candidate is not explicit null")
    assert state.State(permission="allow", candidate=None) == {"permission": "allow", "candidate": None}
    assert state.State(permission="allow", candidate=candidate.value(None)) == {"permission": "allow", "candidate": {"value": None}}
    try:
        state.Candidate()
    except TypeError:
        pass
    else:
        raise AssertionError("A present candidate must supply its value, even when null")
    assert diagnostics.Code.REMOTE_RPC == "remote_rpc"
    base = capability.intercept()
    composed = base.deny().modify_input(replace=True).elicitation_form()
    assert base.allow().to_wire() == {"modes": ["intercept", "observe"], "capabilities": {"effects": ["allow"]}}
    assert base.elicitation_form().to_wire()["capabilities"] == {"effects": [], "elicitation": {"form": {}}}
    assert base.elicitation_url().to_wire()["capabilities"] == {"effects": [], "elicitation": {"url": {}}}
    composed_wire = composed.to_wire()
    assert composed_wire["capabilities"]["effects"] == ["deny", "modify"]
    assert composed_wire["capabilities"]["elicitation"] == {"form": {}}
    assert wire.parse_capabilities(composed_wire["capabilities"])["ok"]
    composed_wire["capabilities"]["effects"].clear()
    assert composed.to_wire()["capabilities"]["effects"] == ["deny", "modify"]
    assert capability.observe().to_wire()["modes"] == ["observe"]
    for invalid in [lambda: base.to_wire(), lambda: base.modify_input(), lambda: base.modify_input(replace="true"), lambda: capability.observe().deny(), lambda: base.flow(operations=[]), lambda: base.flow(operations=["unknown"])]:
        try:
            invalid()
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid ergonomic grant accepted")
    invalid_counts = [
        dict(remaining_continuations=-1, continuation_count=0),
        dict(remaining_continuations=0, continuation_count=-1),
        dict(remaining_continuations=True, continuation_count=0),
        dict(remaining_continuations=0.5, continuation_count=0),
        dict(remaining_continuations=2**53, continuation_count=0),
        dict(remaining_continuations=0, continuation_count=0, max_continuations=-1),
        {},
    ]
    for counts in invalid_counts:
        try:
            base.flow(operations=["continue"], **counts)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid continuation counts accepted")
    for count in [0, 2**53 - 1]:
        assert base.flow(operations=["continue"], remaining_continuations=count, continuation_count=count).to_wire()["capabilities"]["flow"]["remainingContinuations"] == count
    try:
        base._modes = ()
    except AttributeError:
        pass
    else:
        raise AssertionError("Mutable capability builder")
    assert "id" not in input and "type" not in input
    assert effect.Deny(reason="blocked") == {"type": "deny", "reason": "blocked"}
    value = registration.Registration(hooks=[])
    assert value["protocolVersion"] == wire.PROTOCOL_VERSION
    try:
        registration.Registration(hooks=[], protocol_version="future")
    except ValueError as error:
        assert any(d["code"] == "literal_mismatch" for d in error.diagnostics)
    else:
        raise AssertionError("Invalid protocolVersion literal constructed")
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
        call=tool.Call(id="enum-call"), tool=input.to_wire()["tool"], path=tool.Path.NATIVE,
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
