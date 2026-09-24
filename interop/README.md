# Shared intercept application scenarios

`scenarios.json` is the language-neutral input for every SDK adapter. It follows
[CONTRACT.md](CONTRACT.md), the canonical `schema/draft` schemas, and the composition
rules in [the composition specification](../spec/draft/base/composition.md). The six-effect cases cover the TypeScript atomic scenarios and `draft-atomic.ts`
using a synthetic pending-boundary evaluator.

## Wire and control contract

Both `request` and `response` are **full canonical JSON-RPC envelopes**:

- `request.jsonrpc` is `"2.0"`; `request.method` is `"hooks/intercept"`.
- `request.id` and `request.params.event.id` both equal the scenario `id`.
- Capabilities and incoming state are `request.params.capabilities` and
  `request.params.state`; effects are `response.result.effects`.
- HTTP sends the full request envelope as the `/intercept` POST body.
- Stdio writes that same envelope as one NDJSON line. Do **not** wrap it inside
  another `intercept` request.
- Responses preserve the fixture envelope, including deliberately invalid
  responses. Servers validate incoming requests, but must not repair or discard
  scripted invalid responses before clients can test their rejection.

The scenarios include accepted responses and expected rejections. Requests
are always schema-valid. Negative cases are classified with tags:
`schema-invalid`, `correlation-invalid`, `capability-invalid`, or
`application-invalid`. Tags describe coverage; clients must derive behavior from
the request and response, not branch on IDs, tags, or expected answers.

## Deterministic application semantics

Each row is independent. No prior row changes another row's state.

1. Start with `event.tool.input` for `tool.before`, no emitted messages or
   scheduled injections, and incoming `params.state` when present. If the whole
   optional state object is omitted, initialize permission to `none` and candidate
   to null; do not reject the request. When state is present, both permission and
   candidate are required by the canonical schema. A non-null candidate is bound
   to this incoming input. The fixture's optional `candidate.provenance.requestId`
   identifies the earlier JSON-RPC request that supplied it. JSON `null` as a *candidate value* is distinct
   from a null *candidate*.
2. This synthetic host's managed policy allows the operation and its ordinary
   native policy does not require a prompt. Consequently `permission: "none"`
   resolves to `decision: "allow"` here, not in every real host. `allow` is not a
   managed-policy bypass. An applicable `ask` remains pending: no test fabricates
   user approval. Incoming `deny` is sticky.
3. Validate the entire response envelope, protocol version, response/request ID
   correlation, every effect, and all advertised capability details before
   committing anything. Unknown effect types fail even when other effects are
   valid. Unsupported targets/operations are failures, never no-ops.
4. Stage input modifications in response order **before** binding any new return
   value, regardless of where `return` appears in the array. Merge is shallow;
   nested objects replace whole nested objects, and null is a literal value, not
   deletion. Replace removes omitted keys. Every staged tool input must be an
   object with a positive integer `task` (booleans are not integers). This tool
   constraint is local fixture behavior, not a general AHP schema restriction.
5. A changed effective input invalidates an incoming candidate and any incoming
   `allow` for the old input; a deeply equal input preserves them. Object-key
   order is irrelevant. A new return in this response binds to the final input.
   New permission effects then compose: deny wins over ask, ask wins over allow.
   Denial discards a candidate. Return alone is not permission and cannot bypass
   an incoming or newly requested ask.
6. Message texts accumulate in response order only after acceptance. Deny reasons
   and flow reasons are not implicitly appended to this explicit-message list.
7. At `tool.before`, `flow(stop)` prevents original execution without changing an
   otherwise allowed authorization decision. Continuation cases use
   `turn.finish.before`, **not** `tool.before`. Stop wins over continue in either
   order. Accepted continuation instructions accumulate in response order,
   including when stop wins, matching `stageBoundary`'s retained instructions.
   Repeated continuations resolve to one continuation, subject to a positive
   advertised remaining allowance. Deduct exactly one from that allowance when
   the accepted response resolves to continue, regardless of the number of
   continue effects. When stop wins, no continuation occurs and no allowance is
   consumed; retained instructions do not imply execution. All operations still
   must be supported; stop does not salvage another invalid effect or an
   exhausted continuation request.
8. Injection requires advertised `inject.context.append` and its exact
   `deliverAt`. Accepted injections accumulate in array order; `now` is staged
   model context and `next_turn` is a scheduled-context record. Neither is a user
   message. This evaluator records scheduling, not actual future model delivery.
9. Only after acceptance derive the application result. Original tool execution
   occurs once exactly when permission resolves to allow, flow is not stopped,
   and there is no applicable candidate. An ask remains unexecuted. A finish
   decision has no original tool to execute, so `executed` is false there even
   when a continuation is requested.

## Expected result and error assertions

`expected` is a **partial object**. Compare every present key deeply; do not
require equality of the entire result object. Expected arrays have exact length
and order. Missing keys impose no assertion; explicit JSON null is an assertion.

| Key | Meaning |
| --- | --- |
| `decision` | Resolved `allow`, `deny`, or pending `ask` under this synthetic host's policy. Independent of flow. |
| `executed` | Whether original tool execution was selected; never whether a hook returned successfully. |
| `input` | Effective tool input after atomic acceptance; omitted for finish boundaries. |
| `messages` | Explicit accepted `message.text` strings, in order. |
| `result` | Selected candidate value, including null/false/zero/empty string. A skipped candidate is not replaced with fabricated tool output. |
| `flow` | Resolved `stop` or `continue`, where asserted. |
| `continuationInstructions` | Ordered accepted continue instruction strings; retained even if stop wins. An empty array is asserted for stop without continue. |
| `continuationRemaining` | Integer remaining allowance: incoming `capabilities.flow.remainingContinuations` minus one if the accepted response resolves to continue, otherwise unchanged. Multiple continue effects never consume multiple units. |
| `injections` | Accepted full inject effect objects, including `type`, `target`, `operation`, `deliverAt`, and `value`, in order. |

`expectError: true` replaces `expected`. It requires the client to reject the
scripted response, not treat a connection error, missing report, server startup
failure, or unsupported adapter feature as a successful negative test. Report a
passed negative only after the actual scripted response reaches validation or
staging and is rejected there.

A negative response is one atomic failure boundary. Clients must snapshot their
pending state and staged side-effect counters and verify no partial state,
message, prompt, execution, or injection escaped. In particular, negative rows
with incoming candidates and `allow` must not erase that state after a failed
rewrite; valid deny/message/return effects before an invalid effect must not
leak. No failure policy is specified by this fixture contract: rejection stops
this row before fail-open/fail-closed policy or original execution is applied.
The shared result contract has no rollback-state or callback-trace fields, so an
`expectError` report alone does not prove rollback; adapter tests must also
assert these local invariants.

## Coverage and explicit limits

Covered: all six original effects, ordered/shallow modifications, null and other
false-like results, response-wide malformed-effect rejection, staging failures,
capability effect/target/operation rejection, envelope/version/correlation errors,
permission precedence, incoming and omitted state, candidate preservation/invalidation,
flow stop/continue/allowance, ordered continuation instructions and single-unit
consumption, and injection delivery capability checks.

The default native policy makes invalidating an incoming allow resolve to allow
again; candidate invalidation is externally visible through `executed`, but
permission invalidation itself needs a local state assertion. The contract has
no incoming native-approval or managed-policy field, so renewal of actual human
approval and managed-policy denial require separate tests, including the
TypeScript approval tests.

Not represented: multiple subscriptions dispatched serially, fail-open versus
fail-closed transport errors, raw malformed JSON bytes, HTTP status scripting,
barrier-driven races, actual prompt UI, actual model continuation or injection
delivery, post-result controls, execution provenance/audit delivery, or auth
coverage. Test these with their dedicated adapter/control harnesses.

## Regenerate and validate

From the protocol repository:

```sh
python3 interop/generate_scenarios.py
python3 interop/generate_scenarios.py --check
```

Generation uses only Python's standard library plus the repository's existing
`tools/check_conformance.py` validator. Checks
include unique IDs, envelope/event correlation, exclusive expected/error
contracts, expected result structure, canonical validity of every request,
canonical validity of positive and application-negative responses, deliberate
schema failure of schema-negative responses, and byte-for-byte reproducibility.
Schema validation is structural; running all language adapters remains necessary
to verify application behavior.

## Verifier contract

Expected state is partial only for non-semantic metadata. Presence of an unasserted `result`, `flow`, `injections`, `continuationInstructions`, `continuationRemaining`, or `rejected` field fails a successful-case assertion, even if its value is null/empty. In particular deny/ask/stop cannot leak a supplied result. Native input placeholders on non-tool boundaries remain tolerated when input is unspecified; asserted inputs remain exact. Diagnostic metadata does not authorize new executable outputs.

Core and authentication receiver receipts must include the exact actual canonical request (direct envelope or mandatory `message` wrapper). Reduced ID/method-only evidence is rejected.

`sender_isolation_matrix.py --workers 4` adds 24 real-SDK sender cases: event auth is configured and a real interception succeeds, while independently captured raw HTTP uploads have no upload auth configured. Four authenticated HTTP event modes × four senders establish 16 credential-present cases; four unauthenticated HTTP and four process-trusted stdio cases are controls. Captured absence of sensitive headers is not credited without exactly one correct raw upload. HTTPS client-certificate noninheritance is not claimed by this HTTP-origin probe.

## Related contracts and reproduction

- [Core matrix](MATRIX.md) and [adapter contract](CONTRACT.md).
- [Lifecycle matrix](LIFECYCLE-MATRIX.md) and [lifecycle/catalogue contract](LIFECYCLE.md).
- [Elicitation](ELICITATION.md), [compaction](COMPACTION.md), and
  [public boundary APIs](../docs/accepted-boundary-apis.md).
- Authoritative [observation behavior](../spec/draft/observation-disposition.md),
  [registration](../spec/draft/base/registration.md), and
  [task/workspace lineage](../spec/draft/task-workspace-lineage.md).

From the workspace root, with SDK dependencies and executables built:

Use the Python SDK virtual environment below, not a bare system Python. Its
installed SDK dependencies include `jsonschema[format]>=4.23,<5`, required by the
authentication matrix's canonical capabilities validation. If needed, install
these dependencies with `python-sdk/.venv/bin/python -m pip install -e ./python-sdk`.
The authentication verifier regressions alone can be run with
`python-sdk/.venv/bin/python agent-hooks-protocol/interop/test_auth_matrix.py --unit-tests`.

```sh
PY=python-sdk/.venv/bin/python
I=agent-hooks-protocol/interop
$PY "$I/matrix.py" --jobs 4 --timeout 90 --output /tmp/core.json
$PY "$I/lifecycle_matrix.py" --workers 4 --timeout 90 --output /tmp/lifecycle.json
$PY "$I/catalogue_matrix.py" --workers 4 --timeout 90 --output /tmp/catalogue.json
$PY "$I/test_auth_matrix.py" --report /tmp/auth.json
$PY "$I/sender_isolation_matrix.py" --workers 4 --timeout 90 --output /tmp/isolation.json
$PY "$I/elicitation_matrix.py"
$PY "$I/compaction_wire_matrix.py"
(cd "$I" && ../../python-sdk/.venv/bin/python -m unittest test_matrix test_lifecycle_matrix test_auth_matrix test_content_upload test_task_lineage test_catalogue_matrix test_sender_isolation test_observation_chain -q)
(cd "$I" && ../../python-sdk/.venv/bin/python -m unittest test_elicitation_matrix test_compaction_matrix test_compaction_wire_matrix -q)
```

Use fresh reports for executed coverage; grid dimensions and checked-in artifacts
are not independent proof of current conformance. Elicitation-specific wire tests
cover HTTP bearer; compaction and observation chains cover HTTP/stdio. Separate
core/lifecycle/catalogue/auth suites exercise configured authentication modes.
Tests use synthetic local trust and deterministic compaction, without an LLM or
mutable network dependency. They do not prove production harness event occurrence,
browser flow completion, production identity federation, or durable storage.
Independent upload isolation uses an HTTP origin and does not prove HTTPS
client-certificate noninheritance. Reference content tests supplement, rather
than replace, cross-language receiver evidence.
