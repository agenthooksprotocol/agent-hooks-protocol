# Cross-language lifecycle integration

`lifecycle_matrix.py` discovers **actual SDK executables** through the four
`interop/adapter.json` files. It runs every Python/Go/Rust/TypeScript client
against every server, over HTTP with configured authentication modes and
persistent stdio with process trust only.
The runner does not apply effects. Each client owns pending request state,
staged responses, cancellation, acceptance, and settled observation construction.
Each receiver owns uploaded bytes and credential-scoped reference validation.

## Observation contract

Observation notifications carry only the event identity and effective
permission-filtered boundary payload, not a disposition or decision summary.
Short-circuiting downgrades remaining uncalled intercept subscriptions to
`hooks/observe`; called interceptors receive no automatic second copy. Explicit
observers remain independent. Best-effort dispatch never delays interruption;
selected uploads precede each notification.

## Run

From the workspace containing the protocol and four SDK repositories:

```sh
python3 agent-hooks-protocol/interop/generate_lifecycle_scenarios.py
python3 -m unittest discover -s agent-hooks-protocol/interop -p test_lifecycle_matrix.py -v
python3 agent-hooks-protocol/interop/lifecycle_matrix.py --workers 4
```

Use `--client python --server go --transport stdio` to isolate a group. Commands
are discovered, not replaced with an in-runner oracle. SDK dependencies and the
normal SDK build setup must already be available. Server readiness polling is
startup-only. Lifecycle ordering uses explicit blocking receipt barriers and
request-specific releases, never sleeps. Watchdogs bound failures. Processes
run in separate groups; finally paths terminate and reap them. Stdio clients
also own and reap their server children.

## Coverage

The shared scenarios, including stdio-only unsolicited-frame cases, exercise:

* cancellation before a reply, and after acquisition but before acceptance;
* discard of privately retained modify/message/allow/return/continue/inject responses, including
  attempted fail-open fallback after cancellation;
* late old response while a different request is pending;
* duplicate transport attempts/responses without second publication, including
  first-valid-response retention before acceptance with different duplicate effects;
* reverse-order replies, two distinct staged payloads and reverse acceptance;
* unsolicited stale/unknown stdio frames discarded without consuming a live request;
* acceptance correlated to the requested ID, not whichever reply arrived last;
* interception settlement before both observation subscription views, with the
  same logical event ID and effective input;
* denied and cancelled boundaries remaining observable, plus post-publication
  interruption preserving completed effects and effective input;
* separate test-control decision views for each event/subscription (not a
  standardized decision field on the canonical observation wire message);
* malicious effects returned to best-effort observers being ignored;
* actual upload readiness before canonical `event.items` body references;
* immutable size/SHA-256/reference bindings, repeated uploads, changed bytes/new ref;
* fixed content authorization distinct from endpoint authentication, authorized
  body views and explicit canonical content-gap views;
* reasoning/skill/native-labelled content items taking the normal content path.

HTTP groups additionally run raw receiver probes including valid upload, new
receiver-assigned references for changed bytes, retained original body, rejected
missing/wrong upload credentials, missing upload, wrong size, wrong hash, and
retained bytes after failed reads.
These receiver probes are supplementary HTTP checks, not claimed separate
cross-language client scenarios. Stdio exercises the same receiver implementation
through canonical messages in the main matrix.

The verifier checks exact application results and receiver receipt multiplicity,
canonical IDs, cancellation/reply partial orders, old-vs-next pending order,
acquisition and acceptance milestones, settled observation payloads, separate
control-side decision/interruption views, subscription scope, and
successful exact uploads before dispatch. Negative self-tests reject success-
shaped reports without those proofs, as well as wrong IDs, duplicate records,
content permission/integrity mistakes, and race-order false passes.

## Scope and limits

HTTP receipt evidence is fetched directly from the receiver control endpoint.
For stdio, each SDK client fetches the receiver's `/receipts` endpoint before
shutdown and forwards those records in `report.receipts`. Receiver handlers
create the received/replied/observed records; the client does not synthesize
successful deliveries. The verifier checks those receiver-origin records for
exact payloads, multiplicity and ordering. Missing delivery records fail even
when the client's application report says passed. Client-issued `/mark` records
are test-control milestones, not proof that a wire delivery occurred.

This trusts the SDK test adapters, not an adversarial sender; forwarding a
receiver's records does not require a second evidence transport.


See [LIFECYCLE.md](LIFECYCLE.md) for the **test-control** contract. Upload HTTP
framing, fixed credential permission, scenario instructions, marks,
receipts, and barriers are adapter machinery. Raw uploads follow the
[canonical upload binding](../spec/draft/content-upload.md).
Subscription identifiers are local harness configuration only; request correlation uses JSON-RPC IDs and events retain their own identity.

Logical cancellation closes the local acceptance path immediately. Test adapters
retain drain futures to deliver late responses. Coverage excludes physical
termination of remote operations or sockets, native host integration, durable
cancellation, rollback of completed effects, and crash recovery.
Published application states represent the SDK test runtime, not actual external
tool execution or model consumption. Observation receipt rendezvous is proof
instrumentation only; it does not introduce reliable delivery or a requirement
to await backend observer processing. No host/task lineage integration claim is
made from content labels alone.

Lifecycle HTTP tests use synthetic local trust with `none`, bearer, OAuth, mTLS,
and workload authentication; stdio uses process trust only. Content read/upload
permission is independently enforced even when endpoint connection is allowed.
Independent bearer upload authorization is not a full upload-auth-method
Cartesian product. The dedicated authentication suite covers endpoint negatives.

## Acceptance and routing invariants

The adapters require exact-ID stdio routing, first-response retention, distinct
per-request payloads, acquisition evidence, post-publication interruption handling,
and child-PID registration/cleanup. The receive step
retains a canonical validated response; semantic SDK evaluation and publication
happen atomically at acceptance.

## Supplementary checks

Reproduction of supplementary checks from the workspace root:

```sh
(cd python-sdk && .venv/bin/python -m unittest discover -s tests -p test_lifecycle.py -v)
(cd go-sdk && go test -race ./interop -run Lifecycle -count=1 -v)
(cd rust-sdk && cargo test --quiet --bin lifecycle_client --bin lifecycle_server)
(cd typescript-sdk && node --test interop/lifecycle.test.mjs interop/evaluator.test.mjs)
python3 agent-hooks-protocol/interop/matrix.py --jobs 4 --output /tmp/ahp-lifecycle-core-regression.json
python-sdk/.venv/bin/python agent-hooks-protocol/interop/test_auth_matrix.py --report /tmp/ahp-lifecycle-auth-regression.json
```

Build Rust lifecycle executables before the matrix:
`cargo build --manifest-path rust-sdk/Cargo.toml --bin lifecycle_client --bin lifecycle_server`.
The manifest launches the resulting binaries directly, separating compilation
from startup readiness. Startup failures are failures, never semantic passes.
