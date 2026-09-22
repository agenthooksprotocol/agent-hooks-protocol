#!/usr/bin/env python3
"""Generate/check deterministic, language-neutral intercept application fixtures."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
INPUT = {"task": 1, "options": {"recursive": True, "limit": 20}, "keep": "yes"}
CAPS = {"effects": ["deny", "allow", "ask", "modify", "message", "return"],
        "modify": {"input": {"replace": True, "merge": True}}}
CANDIDATE = {"value": {"cached": 1}, "provenance": {"subscriptionId": "earlier"}}


def effect(kind, **fields):
    return {"type": kind, **fields}


def modify(value, operation="merge"):
    return effect("modify", target="input", operation=operation, value=value)


def build():
    scenarios = []

    def add(name, effects, expected=None, *, permission="none", candidate=None, caps=None,
            tags=(), error=None, response=None):
        request = {"jsonrpc": "2.0", "id": name, "method": "hooks/intercept", "params": {
            "protocolVersion": "draft",
            "event": {"id": name, "source": "urn:ahp:interop", "type": "tool.before",
                      "time": "2026-01-01T00:00:00Z", "session": {"id": "interop"},
                      "call": {"id": "call-1"}, "path": "native",
                      "tool": {"origin": "native", "name": "task", "kind": "task",
                               "input": deepcopy(INPUT)}},
            "capabilities": deepcopy(CAPS if caps is None else caps),
            "state": {"permission": permission, "candidate": deepcopy(candidate)}}}
        wire = {"jsonrpc": "2.0", "id": name,
                "result": {"protocolVersion": "draft", "effects": deepcopy(effects)}}
        row = {"id": name, "request": request, "response": wire if response is None else response,
               "tags": list(tags)}
        if error:
            row["expectError"] = True
            row["tags"] += ["negative", error, "atomic-rejection"]
        else:
            row["expected"] = {"decision": "allow", "executed": True, "input": deepcopy(INPUT),
                               "messages": [], **(expected or {})}
        scenarios.append(row)
        return row

    allow, ask = effect("allow"), effect("ask")
    deny = effect("deny", reason="Blocked by test policy")
    msg = effect("message", text="Visible only after acceptance")
    ret = effect("return", value={"cached": 2})
    changed = {**INPUT, "task": 2}
    blocked = {"decision": "deny", "executed": False}
    pending = {"decision": "ask", "executed": False}
    cached = {"executed": False, "result": {"cached": 2}}

    add("empty-response-preserves-input", [], tags=["no-effect"])
    # State is optional; permission and candidate are required only when present.
    for name, effects, expected in [
        ("omitted-state-empty-response", [], {}),
        ("omitted-state-ask-defers-execution", [ask], pending),
        ("omitted-state-return-skips-execution", [ret], cached),
    ]:
        row = add(name, effects, expected, tags=["state", "omitted-state"])
        del row["request"]["params"]["state"]
    add("allow-executes", [allow], tags=["allow"])
    add("deny-blocks", [deny], blocked, tags=["deny"])
    add("ask-defers-execution", [ask], pending, tags=["ask"])
    add("message-accumulates-in-order", [msg, effect("message", text="Second message")],
        {"messages": [msg["text"], "Second message"]}, tags=["message"])
    add("return-skips-original-execution", [ret], cached, tags=["return"])
    for label, value in [("null", None), ("false", False), ("zero", 0), ("empty-string", ""),
                         ("array", [1, {"ok": True}])]:
        add(f"return-{label}-preserved", [effect("return", value=value)],
            {"executed": False, "result": value}, tags=["return"])
    add("modify-merge-shallow-null-literal", [modify({"options": {"limit": 3}, "keep": None})],
        {"input": {"task": 1, "options": {"limit": 3}, "keep": None}}, tags=["modify", "merge"])
    add("modify-replace-removes-omitted-keys", [modify({"task": 2}, "replace")],
        {"input": {"task": 2}}, tags=["modify", "replace"])
    add("modify-sequence-is-ordered", [modify({"task": 2}, "replace"), modify({"label": "final"})],
        {"input": {"task": 2, "label": "final"}}, tags=["modify", "atomic"])
    add("modify-message", [modify({"task": 2}), msg],
        {"input": changed, "messages": [msg["text"]]}, tags=["modify", "message", "atomic"])
    add("modify-return", [modify({"task": 2}), ret],
        {**cached, "input": changed}, tags=["modify", "return", "atomic"])
    add("return-before-modify", [ret, modify({"task": 2})],
        {**cached, "input": changed}, tags=["modify", "return", "atomic"])
    add("all-six-effects-atomic", [allow, ret, msg, modify({"task": 2}), ask, deny],
        {**blocked, "input": changed, "messages": [msg["text"]]}, tags=["atomic", "precedence"])
    for name, effects, expected in [
        ("ask-then-allow", [ask, allow], pending),
        ("allow-then-ask", [allow, ask], pending),
        ("deny-then-allow", [deny, allow], blocked),
        ("allow-then-deny", [allow, deny], blocked),
        ("deny-then-ask", [deny, ask], blocked),
        ("ask-then-deny", [ask, deny], blocked),
        ("deny-then-return", [deny, ret], blocked),
        ("return-then-deny", [ret, deny], blocked),
        ("ask-then-return", [ask, ret], pending),
        ("return-then-ask", [ret, ask], pending),
    ]:
        add(name, effects, expected, tags=["precedence"])
    add("empty-preserves-candidate", [], {"executed": False, "result": CANDIDATE["value"]},
        candidate=CANDIDATE, permission="allow", tags=["state", "candidate"])
    add("return-replaces-earlier-candidate", [ret], cached, candidate=CANDIDATE, tags=["state", "candidate"])
    add("deny-discards-earlier-candidate", [deny], blocked, candidate=CANDIDATE, tags=["state", "candidate"])
    add("changed-input-invalidates-candidate", [modify({"task": 2})], {"input": changed},
        candidate=CANDIDATE, permission="allow", tags=["state", "candidate", "permission-invalidation"])
    add("unchanged-input-preserves-candidate", [modify({"task": 1})],
        {"executed": False, "result": CANDIDATE["value"]}, candidate=CANDIDATE, permission="allow",
        tags=["state", "candidate"])
    add("reordered-equal-input-preserves-candidate",
        [modify({"keep": "yes", "options": {"limit": 20, "recursive": True}, "task": 1}, "replace")],
        {"executed": False, "result": CANDIDATE["value"]}, candidate=CANDIDATE, tags=["state", "candidate"])
    add("changed-input-binds-new-candidate", [ret, modify({"task": 2})], {**cached, "input": changed},
        candidate=CANDIDATE, tags=["state", "candidate"])
    add("incoming-ask-survives-allow", [allow], pending, permission="ask", tags=["state", "permission"])
    add("incoming-ask-blocks-candidate", [], pending, permission="ask", candidate=CANDIDATE,
        tags=["state", "permission", "candidate"])
    add("incoming-ask-survives-rewrite-and-allow", [modify({"task": 2}), allow, ret],
        {**pending, "input": changed}, permission="ask", tags=["state", "permission"])
    add("incoming-deny-survives-allow-return", [allow, ret], blocked, permission="deny",
        tags=["state", "permission"])
    add("empty-preserves-incoming-ask", [], pending, permission="ask", tags=["state", "permission"])
    add("empty-preserves-incoming-deny", [], blocked, permission="deny", tags=["state", "permission"])

    unknown = effect("not-a-supported-effect")
    for prefix, good in [("modify", modify({"task": 2})), ("message", msg), ("return", ret),
                         ("deny", deny), ("allow", allow), ("ask", ask)]:
        add(f"{prefix}-then-unknown-rejects-entire-response", [good, unknown], error="schema-invalid",
            candidate=CANDIDATE, permission="allow", tags=[prefix])
    for name, bad in [
        ("deny-missing-reason", effect("deny")),
        ("message-non-string", effect("message", text=17)),
        ("return-missing-value", effect("return")),
        ("modify-missing-operation", effect("modify", target="input", value={"task": 2})),
        ("modify-unsupported-operation", modify({"task": 2}, "patch")),
        ("modify-array-value", modify([])),
    ]:
        add(name, [msg, bad], error="schema-invalid")
    for name, bad in [("zero", modify({"task": 0})), ("fraction", modify({"task": 1.5})),
                      ("boolean", modify({"task": True})), ("missing", modify({}, "replace"))]:
        add(f"invalid-task-{name}-rejects-staged-message", [msg, bad],
            error="application-invalid", candidate=CANDIDATE, permission="allow", tags=["state", "modify"])
    add("later-invalid-modify-rolls-back-earlier-modify",
        [modify({"task": 2}), msg, modify({"task": -1})], error="application-invalid",
        candidate=CANDIDATE, permission="allow", tags=["state", "modify"])
    add("modify-unadvertised-target", [msg, effect("modify", target="output", operation="replace", value={})],
        error="capability-invalid")
    for kind, returned in [("deny", deny), ("allow", allow), ("ask", ask), ("message", msg), ("return", ret),
                            ("modify", modify({"task": 2}))]:
        caps = deepcopy(CAPS)
        caps["effects"].remove(kind)
        if kind == "modify": caps.pop("modify")
        add(f"unadvertised-{kind}-rejected", [returned], caps=caps, error="capability-invalid")
    for operation in ["merge", "replace"]:
        caps = deepcopy(CAPS)
        caps["modify"]["input"][operation] = False
        add(f"unadvertised-{operation}-rejects-message", [msg, modify({"task": 2}, operation)],
            caps=caps, error="capability-invalid")
    add("absent-modify-capability-rejected", [msg, modify({"task": 2})],
        caps={"effects": [e for e in CAPS["effects"] if e != "modify"]}, error="capability-invalid")
    row = add("wrong-response-id", [msg], error="correlation-invalid")
    row["response"]["id"] = "not-the-request-id"
    row = add("wrong-jsonrpc-version", [msg], error="schema-invalid")
    row["response"]["jsonrpc"] = "1.0"
    row = add("wrong-protocol-version", [msg], error="schema-invalid")
    row["response"]["result"]["protocolVersion"] = "unsupported"
    row = add("missing-effects-array", [], error="schema-invalid")
    del row["response"]["result"]["effects"]
    row = add("non-array-effects", [], error="schema-invalid")
    row["response"]["result"]["effects"] = msg
    row = add("null-result-envelope", [], error="schema-invalid")
    row["response"]["result"] = None
    row = add("extensible-response-envelope", [], tags=["extensions"])
    row["response"]["futureMetadata"] = {"ignored": True}
    row["response"]["result"]["futureMetadata"] = {"ignored": True}
    # Flow continue belongs at a finish decision, never tool.before.
    stop = effect("flow", operation="stop", reason="Stop this turn")
    cont = effect("flow", operation="continue", instruction="Check the result")
    stop_caps = {**deepcopy(CAPS), "effects": CAPS["effects"] + ["flow"],
                 "flow": {"operations": ["stop"]}}
    for suffix,effects in [('empty',[]),('allow',[allow]),('return',[ret])]:
        row=add('prior-accepted-stop-'+suffix,effects,{'executed':False,'flow':'stop'},caps=stop_caps,tags=['flow','state','serial-pipeline'])
        row['request']['params']['state']['flow']='stop'
    add("flow-stop-prevents-tool-execution", [stop], {"executed": False, "flow": "stop"},
        caps=stop_caps, tags=["flow"])
    add("flow-stop-wins-over-return", [ret, stop], {"executed": False, "flow": "stop"},
        caps=stop_caps, tags=["flow", "return"])
    add("flow-stop-does-not-authorize-denied-tool", [deny, stop], {**blocked, "flow": "stop"},
        caps=stop_caps, tags=["flow", "deny"])
    add("unadvertised-flow-rejects-message", [msg, stop], error="capability-invalid", tags=["flow"])
    add("unadvertised-flow-operation", [msg, cont], caps=stop_caps, error="capability-invalid", tags=["flow"])
    add("flow-stop-then-unknown-is-atomic", [stop, unknown], caps=stop_caps,
        error="schema-invalid", tags=["flow"])
    add("flow-stop-missing-reason", [msg, effect("flow", operation="stop")],
        caps=stop_caps, error="schema-invalid", tags=["flow"])

    def finish(name, effects, *, remaining=2, error=None, operations=None, flow="continue"):
        caps = {"effects": ["flow", "message"], "flow": {
            "operations": operations or ["stop", "continue"], "remainingContinuations": remaining,
            "continuationCount": 0, "maxContinuations": 2}}
        row = add(name, effects, {"executed": False, "flow": flow}, caps=caps, error=error, tags=["flow", "finish-boundary"])
        event = row["request"]["params"]["event"]
        del event["tool"]
        event.pop("call"); event.pop("path")
        event.update(type="turn.finish.before", turn={"id": "turn-1"}, continuationCount=0, outcome="completed", items=[])
        if "expected" in row:
            del row["expected"]["input"]
            instructions = [e["instruction"] for e in effects
                            if e["type"] == "flow" and e["operation"] == "continue"]
            row["expected"]["continuationInstructions"] = instructions
            # Stop suppresses continuation, but preserves accepted instructions.
            row["expected"]["continuationRemaining"] = remaining - (1 if flow == "continue" and instructions else 0)
        return row

    finish("flow-continue-at-finish", [cont])
    finish("flow-repeated-continue-is-one-continuation", [cont, effect("flow", operation="continue", instruction="Also check logs")])
    finish("flow-repeated-continue-uses-last-single-allowance", [cont, effect("flow", operation="continue", instruction="Also check logs")], remaining=1)
    finish("flow-stop-only-preserves-continuation-allowance", [stop], flow="stop")
    finish("flow-stop-wins-over-continue", [cont, stop], flow="stop")
    finish("flow-stop-before-continue-still-wins", [stop, cont], flow="stop")
    finish("flow-exhausted-allowance-rejects-message", [msg, cont], remaining=0, error="application-invalid")
    finish("flow-unadvertised-stop-rejected", [cont, stop], operations=["continue"], error="capability-invalid")
    finish("flow-continue-missing-instruction", [msg, effect("flow", operation="continue")], error="schema-invalid")

    now = effect("inject", target="context", operation="append", deliverAt="now", value={"text": "Context now"})
    later = effect("inject", target="context", operation="append", deliverAt="next_turn", value={"text": "Context later"})
    inject_caps = {**deepcopy(CAPS), "effects": CAPS["effects"] + ["inject"],
                   "inject": {"context": {"append": True, "deliverAt": ["now", "next_turn"]}}}
    add("inject-now", [now], {"injections": [now]}, caps=inject_caps, tags=["inject"])
    add("inject-next-turn-scheduled", [later], {"injections": [later]}, caps=inject_caps, tags=["inject"])
    add("inject-accumulates-without-user-message", [now, later, msg],
        {"injections": [now, later], "messages": [msg["text"]]}, caps=inject_caps, tags=["inject", "message"])
    add("unadvertised-inject-rejected", [msg, now], error="capability-invalid", tags=["inject"])
    now_only = deepcopy(inject_caps)
    now_only["inject"]["context"]["deliverAt"] = ["now"]
    add("unadvertised-injection-delivery-rejects-all", [now, later], caps=now_only,
        error="capability-invalid", tags=["inject"])
    no_append = deepcopy(inject_caps)
    no_append.pop("inject")
    no_append["effects"].remove("inject")
    add("unadvertised-injection-append", [now], caps=no_append, error="capability-invalid", tags=["inject"])
    add("inject-then-unknown-rejects-entire-response", [later, unknown], caps=inject_caps,
        error="schema-invalid", tags=["inject"])
    add("inject-missing-delivery", [msg, effect("inject", target="context", operation="append", value="bad")],
        caps=inject_caps, error="schema-invalid", tags=["inject"])
    return {"version": 1, "scenarios": scenarios}


def validate(document):
    # Reuse the protocol's dependency-free canonical-schema validator.
    sys.path.insert(0, str(ROOT / "tools"))
    from check_conformance import SchemaStore, Snapshot, SubsetValidator
    store = SchemaStore(Snapshot.resolve(ROOT))
    validator = SubsetValidator(store)
    assert document["version"] == 1
    ids = set()
    for row in document["scenarios"]:
        name = row["id"]
        assert name not in ids, name
        ids.add(name)
        assert row["request"]["id"] == row["request"]["params"]["event"]["id"] == name
        assert ("expected" in row) != (row.get("expectError") is True), name
        assert all(isinstance(tag, str) for tag in row["tags"]), name
        if "expected" in row:
            expected = row["expected"]
            assert set(expected) <= {"decision", "executed", "input", "messages", "result", "flow", "injections",
                                     "continuationInstructions", "continuationRemaining"}, name
            assert expected["decision"] in ("allow", "deny", "ask"), name
            assert isinstance(expected["executed"], bool), name
            assert "input" not in expected or isinstance(expected["input"], dict), name
            assert isinstance(expected["messages"], list), name
            if "continuationInstructions" in expected:
                instructions = expected["continuationInstructions"]
                assert isinstance(instructions, list) and all(isinstance(item, str) for item in instructions), name
            if "continuationRemaining" in expected:
                remaining = expected["continuationRemaining"]
                assert type(remaining) is int and remaining >= 0, name
        for field in ("request", "response"):
            path = ROOT / "schema" / "draft" / f"intercept-{field}.schema.json"
            errors = validator.validate(row[field], store.load(path), path)
            invalid = field == "response" and "schema-invalid" in row["tags"]
            assert bool(errors) == invalid, f"{name} {field}: {errors or 'expected schema rejection'}"
    return len(ids)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate and require checked-in fixtures to match")
    args = parser.parse_args()
    document = build()
    count = validate(document)
    rendered = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    path = HERE / "scenarios.json"
    if args.check:
        assert path.read_text() == rendered, "scenarios.json is stale; regenerate it"
    else:
        path.write_text(rendered)
    errors = sum(row.get("expectError", False) for row in document["scenarios"])
    print(f"Validated {count} scenarios: {count - errors} accepted, {errors} rejected")


if __name__ == "__main__":
    main()
