# Lifecycle integration adapter contract (test control v1)

This extends [CONTRACT.md](CONTRACT.md) for lifecycle tests. Four SDK-local pending/staging/acceptance implementations use canonical draft intercept/observe envelopes, generated codecs, canonical validation, and immutable receivers. Deployment test-control HTTP modes are none/bearer/oauth/workload/mtls; stdio uses process trust only. These HTTP/TLS/credential-provider labels are not portable AHP authentication mechanisms; see [CONTRACT.md](CONTRACT.md) for bearer references and out-of-band TLS configuration. Content authorization is independent.

## Executables
SDK interop/adapter.json adds lifecycleClient and lifecycleServer command arrays, SDK cwd, append --config ABS_PATH. Server config {transport,readinessFile,scenarioFile,auth:{mode,...}}. Client config {transport,endpoint?,controlEndpoint?,serverCommand?,serverCwd?,serverConfig?,scenarioFile,reportFile,auth:{mode,...}}. HTTP runner starts server; stdio client spawns and reaps server in every exit path. Readiness file {endpoint,controlEndpoint,uploadEndpoint,pid}. Separate loopback control HTTP listener for BOTH transports. Stdio must read requests concurrently while previous request held. No race sleeps; bounded watchdogs only. Startup readiness polling may use bounded waits, never race ordering.

## Fixtures / control
Central fixture {version:1,scenarios:[{id,requests:{KEY:canonicalRequest},responses:{KEY:canonicalResponse},steps:[...],expected:{...}}]}. IDs globally unique except deliberate retries. Servers index responses by request.id; all fixture intercepts initially HELD until release. Record receipt BEFORE waiting. Release sticky for retries.

JSON POST controls except GET /receipts,/health:
- /wait {id,count:1}: block until N intercept receipts for id.
- /release {id}: release response barrier; {ok:true}.
- /mark {scenario,kind,id}: append client milestone; {ok:true}.
Raw octets go to readiness `uploadEndpoint` using independently configured upload credentials. Authorization is credential-scoped (denied credentials yield 403); local `body`/`metadata` labels select test policy, not wire scope. POST raw bytes with Content-Length and AHP-Content-SHA256; verify size/hash (400). Store immutable bytes BEFORE returning 201 JSON `{ref,size,sha256}`. The receiver assigns ref; changed bytes require a different ref. Upload receipts retain size/hash for verification; event body references contain only `{ref}`. Outer body-selected items with an available body omit size/hash; metadata selections and unavailable-body gaps preserve permitted metadata. Do not send caller ref or subscription headers.
- /wait-observed {eventId,count}: block until count observe receipts for event.
- /receipts -> {entries:[...]} ordered list under lock.
- /shutdown -> {ok:true}, terminate listeners and worker waits.

Receipt shapes: {kind:"received",id,message}; before writing response {kind:"replied",id}; /mark {kind:<kind>,id,scenario}; upload {kind:"upload",ref?,status,size,sha256}; observe {kind:"observed",eventId,event,message}. Receiver validates canonical envelopes and content items and resolves each event.items body `{ref}` against immutable stored bytes and upload metadata in the authorized credential scope (missing refs fail HTTP409/stdio fatal; event receipt metadata fails canonical validation). No subscription identifier is carried on wire. JSON-RPC request IDs correlate replies; event source/id retain logical identity. Observe uses canonical hooks/observe notification, HTTP POST /observe or actual stdio. HTTP observe maliciously returns canonical intercept response id `unsolicited-observer`, effects [{type:"deny",reason:"observer must not decide"}]; stdio writes same unsolicited response after receipt. Client must ignore. No observer response may reopen boundary.

Runtime duties: correlation, interruption dominance, staging/publication, settled subscription views, ignored observer effects, immutable credential-scoped content. Adapter-only: scripts, barrier/marks, fixed content permission policy, upload framing, unsolicited replies, receipts. No durable cancellation/native integration/persistence/physical-kill claim. Required selected uploads precede notification dispatch; gaps use canonical content items.

## Receipt export
Client final report includes top-level `receipts:{entries:[...]}` fetched from server GET /receipts BEFORE shutdown, for both transports. Runner independently checks this proof against central fixture. Canonical content field is event.items; observe step override is items (not content). Replies with mismatched request IDs are intentional adversarial fixtures; validate schema but do not rewrite or reject the fixture response at server.

## Acceptance invariants

Observation notifications carry only the event identity and effective
permission-filtered boundary payload, not a disposition or decision summary.
Short-circuiting downgrades remaining uncalled intercept subscriptions to
`hooks/observe`; called interceptors receive no automatic second copy. Explicit
observers remain independent. Best-effort dispatch never delays interruption;
selected uploads precede each notification.

Cancellation tests discard a privately retained validated response before atomic SDK evaluation/publication; semantic evaluation occurs only at acceptance. The central verifier checks exact receiver ordering and payloads.

## Raw upload configuration and evidence

Follow the [content upload specification](../spec/draft/content-upload.md).
Upload steps carry `bodyBase64`: decode once and send raw octets, including
arbitrary binary and empty bodies. There is no JSON upload control.
Server configuration uses `uploadAuth:{token}` as
independent synthetic trust and advertises an absolute `uploadEndpoint`.
Client configuration is
`upload:{auth:{type:"bearer",tokenEnv:"AHP_INTEROP_UPLOAD_TOKEN"},timeoutMs:5000,maxBytes:1048576,endpoint?}`;
the runner supplies the synthetic token through the client environment.
Preserve an explicit upload endpoint; child readiness supplies a fallback only.
Uploads remain HTTP even when event transport is stdio. Never inherit event
credentials. Gate referenced events on a confirmed upload 201 and validated JSON content-reference, and retain actual
receiver-assigned ref/size/SHA-256/status receipts, not raw upload bytes.
Receiver-negative fixtures must send their malformed declared size/hash and
record the actual receiver status; local validation errors and transport failures
cannot satisfy receiver rejection assertions.

Public-stream adapters can use client `contentSources`, a list of
`{descriptor:{ref,size,sha256},bytes:<base64>}` retaining successful fixture
preparation bytes. These are local sources, not authorization to publish a
reference without an upload. Preliminary upload steps, including malformed and
unauthorized probes, still produce their own receiver receipts.

For each additional SDK-driven positive upload, include a top-level report
`contentUploads` entry:
`{sourceRef,descriptor:{ref,size,sha256},method,eventId}`. `method` is
`hooks/intercept` or `hooks/observe`; `sourceRef` identifies the fixture alias or
its preliminary receiver-confirmed reference. The runner binds the confirmation
to the exact selected body in that delivery and an unclaimed successful receiver
upload receipt before the delivery. Hash and size must match the original bytes.
All remaining upload receipts must match the preliminary fixture steps, including
negative statuses, in their original order. Unclaimed extras, unknown sources,
ambiguous deliveries, and late uploads fail; actual receipts are never rewritten.

## Catalogue adapter extension

Use `lifecycleClient`/`lifecycleServer` with `suite:"catalogue"`, authenticated real
transports, `generate_catalogue_scenarios.py`, and `catalogue-scenarios.json`.
The fixture shape is `{version:1,scenarios:[{id,steps,expected,requests:{},responses:{}}]}`;
empty request/response maps support lifecycle loaders, not fabricated interceptions.
Use actual discovery and SDK-local registration evaluation. Expected outcomes
belong only to the central verifier; no semantic evaluation HTTP control, shared
Python evaluator, or expected-field copying is permitted. Unknown suites and
operations fail rather than pass or skip. The lifecycle auth, cancellation, upload,
framing, and cleanup invariants also apply.

Report exactly one result per scenario:
`{language,results:[{id,actual:{sent:[/* full canonical messages */],registrations:[{accepted:boolean}]}}],receipts:{entries:[...]}}`.
Receiver observation entries retain
`{kind:"observed",eventId,event,message}`. Source-local lineage
lifetime is one connection across scenarios. Logical item identity and role must
remain consistent; independent examples must not reuse an ID for different roles.
Registration fixtures use explicit content defaults and canonical validation.
These synthetic capabilities/trust inputs do not prove production policy
provisioning or native harness occurrence of catalogue events.

Scenario upload `ref` fields are local aliases. Adapters bind each alias to the receiver-returned reference and substitute it in subsequent event bodies before dispatch; aliases are never sent as upload headers. A local `subscription` label selects test configuration only, never wire identity or authorization.

Only the second `send` in `duplicate-reply-ignored` and
`first-staged-response-wins` carries `bypassSDK:true`. These are explicit adverse
wire replays after the first response has been staged (or accepted), not normal
SDK dispatches. A repeated request ID alone never authorizes bypassing the SDK.
The replay must still reach the SDK reader and retain the exact attempt and
control-proof receipts checked by the lifecycle verifier.
