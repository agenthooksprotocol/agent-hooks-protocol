#!/usr/bin/env python3
"""Typed capability runtime smoke. Accept a generated directory or package name."""
from __future__ import annotations

import dataclasses
import importlib
from pathlib import Path
import sys
import os
import re
import shutil
import subprocess
import tempfile
import types
from typing import Callable


def check(package: str = "facade_contract") -> None:
    c = importlib.import_module(f"{package}.capability")
    raw = importlib.import_module(f"{package}._models.capability")
    assert c.ToolBeforeCapabilities is not raw.ToolBeforeCapabilities
    for removed in ("Declaration", "Builder", "Mode", "Allow", "Deny", "ModifyInput", "intercept", "observe", "Capabilities"):
        assert not hasattr(c, removed), removed
    assert not hasattr(c.ToolBeforeCapabilities, "from_dict")
    assert not hasattr(c.ToolBeforeCapabilities, "build")
    assert c.HostCapabilities().to_wire() == {}
    before = c.ToolBeforeCapabilities(intercept=c.ToolBeforeIntercept(
        allow=True, deny=True, return_result=True, modify_input=c.Modify.REPLACE_AND_MERGE), observe=True)
    host = c.HostCapabilities(tool_before=before)
    expected = {"tool.before": {"modes": ["intercept", "observe"], "capabilities": {
        "effects": ["allow", "deny", "modify", "return"], "modify": {"input": {"replace": True, "merge": True}}}}}
    assert host.to_wire() == expected
    wire = host.to_wire()
    wire["tool.before"]["modes"].clear()
    wire["tool.before"]["capabilities"]["effects"].clear()
    assert host.to_wire() == expected
    assert c.HostCapabilities(tool_before=c.ToolBeforeCapabilities(observe=True)).to_wire() == {
        "tool.before": {"modes": ["observe"]}}
    assert c.HostCapabilities(tool_before=c.ToolBeforeCapabilities(intercept=c.ToolBeforeIntercept(deny=True))).to_wire()["tool.before"]["modes"] == ["intercept"]
    assert not hasattr(c, "Observe")
    assert c.HostCapabilities(
        session_start=c.SessionStartCapabilities(observe=True),
        tool_before=c.ToolBeforeCapabilities(observe=True),
        session_end=c.SessionEndCapabilities(observe=True),
        turn_end=c.TurnEndCapabilities(observe=True),
    ).to_wire() == {
        "session.start": {"modes": ["observe"]}, "tool.before": {"modes": ["observe"]},
        "session.end": {"modes": ["observe"]}, "turn.end": {"modes": ["observe"]}}
    assert not hasattr(c, "SessionEndIntercept")
    assert [f.name for f in dataclasses.fields(c.SessionEndCapabilities)] == ["observe"]
    for field in dataclasses.fields(c.HostCapabilities):
        if field.name == "tool_before":
            continue
        try:
            c.HostCapabilities(**{field.name: before})
        except TypeError:
            pass
        else:
            raise AssertionError(("wrong event accepted", field.name))
    invalid: list[Callable[[], object]] = [
        lambda: c.ToolBeforeIntercept(),
        lambda: c.ToolBeforeIntercept(allow=False),
        lambda: c.UserElicitationRequestIntercept(),
        lambda: c.ToolBeforeIntercept(allow=1),
        lambda: c.ToolBeforeIntercept(allow="true"),
        lambda: c.ToolBeforeIntercept(modify_input={"replace": True}),
        lambda: c.ToolBeforeIntercept(modify_output=c.Modify.REPLACE),
        lambda: c.ToolBeforeIntercept(flow=c.FlowContinue(remaining_continuations=0, continuation_count=0)),
        lambda: c.ToolBeforeIntercept(inject_context={"append": True}),
        lambda: c.ToolBeforeCapabilities(),
        lambda: c.ToolBeforeCapabilities(observe=1),
        lambda: c.SessionEndCapabilities(),
        lambda: c.SessionEndCapabilities(observe=False),
        lambda: c.SessionEndCapabilities(observe=1),
        lambda: c.HostCapabilities(session_end=c.TurnEndCapabilities(observe=True)),
        lambda: c.HostCapabilities(session_end=c.ToolBeforeCapabilities(observe=True)),
        lambda: c.ToolBeforeCapabilities(intercept=c.ToolAfterIntercept(deny=True)),
        lambda: c.HostCapabilities(tool_before={"modes": ["observe"]}),
        lambda: c.HostCapabilities(tool_before=raw.ToolBeforeCapabilities(effects=["deny"])),
        lambda: c.InjectContext(deliver_at=()),
        lambda: c.InjectContext(deliver_at=(c.Delivery.NOW, c.Delivery.NOW)),
        lambda: c.InjectContext(deliver_at=("now",)),
        lambda: c.InjectContext(deliver_at=[c.Delivery.NOW]),
        lambda: c.FlowContinue(remaining_continuations=0),
    ]
    for field in ("remaining_continuations", "continuation_count", "max_continuations"):
        for value in (-1, True, False, 0.5, "0"):
            invalid.append(lambda field=field, value=value: c.FlowContinue(**({"remaining_continuations": 0, "continuation_count": 0} | {field: value})))
    for construct in invalid:
        try:
            construct()
        except (ValueError, TypeError):
            pass
        else:
            raise AssertionError(("invalid construction accepted", construct))
    for count in (0, 2**53, 10**100):
        flow = c.FlowContinue(remaining_continuations=count, continuation_count=count, max_continuations=count, include_stop=True)
        finish = c.HostCapabilities(turn_finish_before=c.TurnFinishBeforeCapabilities(intercept=c.TurnFinishBeforeIntercept(flow=flow)))
        value = finish.to_wire()["turn.finish.before"]["capabilities"]["flow"]
        assert value == {"operations": ["continue", "stop"], "remainingContinuations": count,
                         "continuationCount": count, "maxContinuations": count}
    injection = c.InjectContext(deliver_at=(c.Delivery.NOW, c.Delivery.NEXT_TURN))
    injected = c.HostCapabilities(tool_before=c.ToolBeforeCapabilities(intercept=c.ToolBeforeIntercept(inject_context=injection))).to_wire()
    assert injected["tool.before"]["capabilities"] == {"effects": ["inject"], "inject": {
        "context": {"append": True, "deliverAt": ["now", "next_turn"]}}}
    # Elicitation is an independent supported facility, not an actual effect.
    facility = c.HostCapabilities(user_elicitation_request=c.UserElicitationRequestCapabilities(
        intercept=c.UserElicitationRequestIntercept(elicitation=c.Elicitation.FORM)))
    elicited = facility.to_wire()
    assert elicited == {"user.elicitation.request": {"modes": ["intercept"], "capabilities": {
        "effects": [], "elicitation": {"form": {}}}}}
    assert c.HostCapabilities(tool_before=c.ToolBeforeCapabilities(
        intercept=c.ToolBeforeIntercept(elicitation=c.Elicitation.FORM))).to_wire()[
            "tool.before"]["capabilities"] == {"effects": [], "elicitation": {"form": {}}}
    wire_codec = importlib.import_module(f"{package}.generated")
    for rendered in (host.to_wire(), finish.to_wire(), injected, elicited):
        for declaration in rendered.values():
            if "capabilities" in declaration:
                parsed = wire_codec.parse_capabilities(declaration["capabilities"])
                assert parsed["ok"], parsed
    for value in (host, before, before.intercept, c.SessionEndCapabilities(observe=True), injection, c.FlowStop(), flow):
        name = dataclasses.fields(value)[0].name if dataclasses.fields(value) else "anything"
        try:
            setattr(value, name, None)
        except (AttributeError, TypeError):
            pass
        else:
            raise AssertionError("mutable capability")
    # Rendering must not use raw-model hydration or any JSON encode/decode path.
    import json
    originals = json.loads, json.dumps
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("JSON reparse during rendering")
    json.loads = forbidden
    json.dumps = forbidden
    try:
        assert host.to_wire() == expected
    finally:
        json.loads, json.dumps = originals


def check_typing(directory: Path) -> None:
    """Check positives and require an actual diagnostic on every negative line."""
    checker = os.environ.get("MYPY", "mypy")
    with tempfile.TemporaryDirectory(prefix="ahp-typed-capability-") as temporary:
        root = Path(temporary)
        (root / "facade_contract").symlink_to(directory.resolve(), target_is_directory=True)
        for kind in ("positive", "negative"):
            source = Path(__file__).with_name(f"python-typed-capability-{kind}.py")
            consumer = root / f"{kind}.py"
            shutil.copyfile(source, consumer)
            result = subprocess.run([checker, "--follow-imports=silent", "--ignore-missing-imports",
                                     "--strict", "--show-error-codes", str(consumer)],
                                    cwd=root, text=True, capture_output=True)
            if kind == "positive":
                assert result.returncode == 0, result.stdout + result.stderr
            else:
                expected = {line for line, text in enumerate(source.read_text().splitlines(), 1)
                            if "  # " in text}
                actual = {int(line) for line in re.findall(r"negative.py:(\d+): error:", result.stdout)}
                assert result.returncode != 0 and actual == expected, (expected, actual, result.stdout, result.stderr)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "facade_contract"
    if Path(target).is_dir():
        module = types.ModuleType("facade_contract")
        module.__path__ = [str(Path(target).resolve())]
        sys.modules["facade_contract"] = module
        target = "facade_contract"
    check(target)
    if "--typing" in sys.argv:
        check_typing(Path(sys.argv[1]))
    print("Python typed capability runtime smoke passed")
