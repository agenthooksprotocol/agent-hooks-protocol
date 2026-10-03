# Compaction runtime and cross-language matrix

The four SDKs expose an optional **host-side text compaction adapter**:

- Python: `agent_hooks_protocol.compaction.run_compaction` and `compaction_capabilities`.
- TypeScript: `runCompaction`, `compactionCapabilities`, `CompactionHook` from the SDK entrypoint.
- Go: `interop.RunCompaction`, `interop.CompactionCapabilities`, `interop.CompactionHook`.
- Rust: `compaction::run_compaction`, `compaction::run_compaction_observed`, `compaction::capabilities`, `compaction::CompactionHook`, `compaction::CompactionObserver`.

These APIs run host-owned callbacks serially. Callbacks receive detached snapshots;
Rust callbacks borrow an immutable snapshot. A callback returns an array of draft
effects (or an error). Generated effect codecs validate structure; the runtime
validates the current boundary, target, operation, value, and enforcement ability.
The host controls subscription supplier identities and failure policies. Never
populate those from an unauthenticated hook response.

## Contract

This adapter supports text replacement only, not object merge or injection. It
advertises `instructions.replace` before compaction and `summary.replace` after
compaction. Before supports `return` and reason-bearing `deny`; both intercept
boundaries support messages. Unsupported effects fail explicitly.

Each response stages all changes, including messages, candidate invalidation, and
new summary bytes. A rejected compound response commits none of these. Fail-open
keeps the earlier accepted state; fail-closed prevents generation or downstream
application. A failed after response never undoes generation that already ran.

Accepted instructions are visible to later callbacks and the generator. Effective
input changes invalidate an earlier supplied summary. Within a compound response,
input changes precede binding its returned summary, independent of effect order.
A return skips only generation: after callbacks still run, including redaction and
failure controls. Later after callbacks see the modified summary. Provenance
continues to identify the original generator or supplying subscription.

Only a result with **`applied: true`** is eligible for downstream application. The
runtime leaves model invocation and summary consumption to the host.
The default generator returns `summary:` plus the effective instructions. Hosts
can supply another generator callback; it must generate, not apply the result.

`observeOnly` / `observe_only` configures the **after** boundary as a notification,
not an interception. Settlement sets `applied` before scheduling observers, and
never waits for their callbacks or returned promises. Each observer gets its own
snapshot of the settled state with no effects or modification capabilities.
Callback return values, errors, and panics have no effect authority and never
mutate the settled result or its failure diagnostics. The before boundary remains
separately enforceable. `seen` records prepared snapshots, not delivery receipts.

Python uses daemon threads, Go uses detached goroutines with panic recovery, and
Rust uses owned callbacks on detached threads with unwind containment. These tasks
do not keep the process alive during shutdown. Rust's borrowed interception
callbacks cannot safely escape their stack lifetime: use
`run_compaction_observed` with `Vec<CompactionObserver>` containing owned
`Arc<dyn Fn + Send + Sync + 'static>` callbacks. Passing borrowed after hooks to
`run_compaction(..., observe_only=true)` is an explicit configuration error,
never a synchronous fallback or unsafe lifetime extension.

TypeScript accepts async `CompactionObserver` callbacks through `options.observers`
(and retains observe-only after-hook support). It schedules invocation in an
unref'd task, contains promise rejections, and never awaits observation delivery.
As with other JavaScript callbacks, observers must yield for I/O; blocking native
work belongs in a host worker. The scheduler does not serialize an arbitrary
closure into a worker or promise isolation from a blocked JavaScript event loop.

Barrier-based tests hold observer work incomplete while the runtime returns
`applied: true` and a downstream sink receives the summary. They also prove that
one blocked observer does not serialize other observers, exceptions do not change
settlement, and snapshot mutation/returned effects cannot change the result.
Watchdog timeouts detect hangs; correctness does not depend on sleep timing.

The local immutable body store uses injective UTF-8-derived reference identifiers.
Changing summary bytes changes its reference while retaining the supplied logical
item ID. Earlier referenced bytes remain available unchanged. The returned
`{id, ref}` is an internal handle, **not a canonical AHP content item**. A production
host must upload bodies through the existing authenticated content-upload layer
and create complete item descriptors before exposing AHP wire events.

## Host-orchestration transport harness

`compaction_matrix.py` runs every TypeScript/Python/Go/Rust sender against every
receiver over both loopback HTTP and NDJSON stdin/stdout: **32 pairs**, with 29
scenarios per pair. All requests pass through the sender's native HTTP/subprocess
stack and the receiver's independent public SDK runtime. No shared evaluator or
expected outcome is sent to receivers. HTTP uses a fresh bearer token.

The fixture's `compaction/run` JSON-RPC method provides test-only host
orchestration. It registers fixed callback responses and invokes compaction. Receiver-side SDK code validates and
settles those effects. This harness tests runtime interoperability and transport execution.
The canonical matrix below covers the event-envelope and
preupload path end to end.

Cases cover instructions flowing into generation, serial result edits, return
followed by after controls, candidate invalidation, compound rollback at either
boundary, earlier accepted messages, fail-open/fail-closed, crossed targets,
unsupported merge, wrong value types, missing denial reasons, malformed response
arrays, callback errors, observation-only capability restrictions, and Unicode /
empty summary bytes. The driver independently checks provenance, serial snapshots,
application gates, identity, immutable bytes, and absence of rejected staged bytes.

## Reproduce

From the workspace root (four SDK repositories and protocol repository siblings):

```sh
(cd typescript-sdk && pnpm --filter @agenthooksprotocol/sdk build)
(cd rust-sdk && cargo build --bin compaction)
python-sdk/.venv/bin/python agent-hooks-protocol/interop/compaction_matrix.py
python-sdk/.venv/bin/python -m unittest discover -s agent-hooks-protocol/interop -p test_compaction_matrix.py

(cd python-sdk && PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_compaction.py)
(cd typescript-sdk && node --test interop/compaction.test.mjs)
(cd go-sdk && go test ./interop -run TestCompaction -count=1)
(cd rust-sdk && cargo test --test compaction)
```

Matrix flags `--sender`, `--receiver`, and `--transport` select a smaller slice.
`--output` selects a report path. No LLM, external network, credentials, or real
context mutation is required. The fixture processes are local test utilities,
not hardened production HTTP services.

## Canonical AHP wire matrix

`compaction_wire_matrix.py` exercises all **16 sender/receiver combinations** over
both **HTTP and stdio**. The only event-wire method is `hooks/intercept`, with
canonical `context.compact.before` and `context.compact.after` events. Test
scheduling and subscription callbacks are configured locally before starting
the native SDK processes.

Each sender runs its public SDK compaction pipeline. Each hook callback:

1. Constructs complete canonical content items from the *current* runtime state.
2. Uploads UTF-8 instructions, source-context items, or summary bytes over the
   specified HTTP raw-octet upload binding, scoped to the receiving credential scope.
   Upload and event credentials are independent. A confirmed 201 with a receiver-assigned JSON content-reference precedes the
   event, including when the event transport is stdio.
3. Sends a canonical `hooks/intercept` request through the sender's native
   HTTP/subprocess stack to the selected receiver SDK.
4. Validates the correlated canonical response and returns its effects to the
   public runtime. Accepted effects therefore determine the next hook's actual
   content descriptors and referenced bytes, not merely expected test values.

Receivers apply their SDK's full canonical request/response validators, resolve
only preuploaded credential-scoped bodies, and verify their size and SHA-256.
Append callbacks derive replies from the received body bytes. Fixed compound
callbacks exercise atomic failures. Receivers do not know expected outcomes.
They record exact canonical requests/responses and resolved body bytes; the oracle
compares those receipts with the sender trace and settled downstream content.

The stdio fixture starts a native receiver subprocess for each subscription call.
Its stdin/stdout contain only canonical JSON-RPC. A native receiver HTTP process
accepts the independently authenticated uploads; the stdio process reads that
credential-scoped immutable store. Filesystem sharing is local receiver state,
not a shared semantic evaluator or an upload bypass.

Each pair executes 8 lifecycle cases, 27 accepted canonical hook exchanges,
41 confirmed credential-scoped content uploads, and 4 rejected requests.
Across 32 pairs this is 256 lifecycle executions, 864 accepted hook exchanges,
1,312 uploaded bodies, and 128 receiver rejections.

Eight lifecycle cases cover serial input/output edits, supplied-summary after
controls, atomic rollback at both boundaries, and fail-open/fail-closed crossed
targets. Four additional requests per pair test actual receiver rejection of a
missing required event field, an invalid boundary capability, a missing upload,
and a mismatched content digest. These negative requests bypass sender validation
and traverse the native sender transport, so rejection is proven at the receiver.
After controls settle, a synthetic downstream sink receives only applied summaries.
Denied/fail-closed cases produce no downstream content.

Build and run from the workspace root:

```sh
(cd typescript-sdk && pnpm --filter @agenthooksprotocol/sdk build)
(cd rust-sdk && cargo build --bin compaction_wire --bin compaction)
python-sdk/.venv/bin/python agent-hooks-protocol/interop/compaction_wire_matrix.py
python-sdk/.venv/bin/python -m unittest discover -s agent-hooks-protocol/interop -p test_compaction_wire_matrix.py
```

The report is `compaction-wire-matrix-results.json`. The same sender, receiver,
transport, and output filters as the original harness are supported.
