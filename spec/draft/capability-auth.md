# Capability discovery, registration, and endpoint authentication

This document defines capability discovery, registration, and endpoint authentication
in the current draft. JSON schemas define the wire grammar; the runtime obligations
below apply even where JSON Schema cannot prove enforcement.

## Discovery and honest capabilities

A harness MUST make its manifest available independently of `session.start`.
`hooks/capabilities` is a JSON-RPC request with `params.protocolVersion`; no session
or event is required. The correlated success result contains `protocolVersion`
and `manifest`. Discovery MUST NOT start a session or execute a model/tool call.
An authenticated discovery endpoint uses its configured transport authentication.
Discovery and interception success payloads cannot be combined in one response.

`manifest.events` declares exact events, delivery modes, and capabilities;
`gaps` identifies uncovered paths and reasons. `transports`, `authentication`,
`toolPaths`, `contentCategories`, and `limits` describe supported deployment
features, coverage, and nonnegative limits. Implementations MUST expose applicable
coverage and limits and MUST NOT interpret omitted coverage as universal support.
The manifest requires `events`, `gaps`, `transports`, `authentication`,
`toolPaths`, `contentCategories`, `limits`, `managedPolicy`, and
`correlationIdentityFields`. Unknown limits remain absent and MUST be explained
in `gaps`; absence never means unlimited. The closed `limits` object names
`maxUploadBytes`, `maxContinuations`, `minTimeoutMs`, and `maxTimeoutMs`.
`managedPolicy` has `scopes` (`user`, `project`, `managed`) and `disableable`;
managed scope requires false. Correlation fields are string field paths, not
verified identity. Intercept entries require `capabilities`; observation-only
entries MUST NOT claim effects or an interception-only mode. Capability discovery is not an
identity assertion; a manifest MUST NOT contain an `identity` block.

Per-request advertisements MUST narrow manifest capabilities to the actual
boundary, path, permissions, and remaining allowance. Unsupported targets and
operations MUST fail explicitly; they MUST NOT be replaced by another operation.
The existing draft encoding uses `modify.<target>.replace` and `.merge` booleans;
at least one MUST be true for an advertised target. `modify`, `flow`, and `inject`
option blocks require their matching effect and vice versa. Empty target sets,
empty injection delivery choices, and unknown operation fields are invalid.
The receiver MUST enforce shallow object merge only on object values. The
manifest is not proof that an effect was actually applied.

## Registration

`hooks[].transport` configures event delivery; `hooks[].authentication` configures
that endpoint only. Subscriptions select `intercept` or `observe`. Interception
requires a timeout and failure policy. Observation MUST NOT specify them.
Matching subscriptions in both modes MAY coexist and MUST NOT be deduplicated
across modes. Wildcards are entire families (`tool.*`) or `*`, not arbitrary
patterns. An intercept wildcard selects only boundaries supporting interception.

Every intercept and observe subscription MUST specify `content` with a required
`default` and optional category-name keys using `body`, `metadata`, or `omit`.
The default MUST cover unfamiliar categories; no implicit content policy applies. `includeNative` defaults to false. Optional
`filters.toolKinds` and `filters.paths` are optimization hints, never grounds for
dropping calls of unknown kind. Managed scope MUST set `disableable: false`.
Implementations MUST advertise whether they honor scope and fail unsupported
mandatory policy rather than silently degrade.

`hooks[].subscriptions[].upload` has `endpoint`, `timeoutMs`, `maxBytes`, and an
optional independent `auth` object. It configures body uploads, not retrieval.
There is no backend `contentReceiver` compatibility alias. Absent upload
credentials MUST NOT inherit event credentials. A credential MAY be configured
for both endpoints only when its resource and permissions cover both. See
[the binary upload binding](content-upload.md) for framing and confirmation.

## Authentication bindings

Remote HTTP MUST use TLS with receiver validation. Plain HTTP is reserved for an
explicitly trusted local test/process boundary, never an implicit downgrade.
Unsupported authentication MUST fail before event delivery or body transfer.
Secret values MUST NOT occur in registration, events, native metadata, or reports.
Credential references name deployment-managed secrets, not portable secret values.

* `bearer`: configure exactly one of `tokenEnv` or `tokenRef`. Resolution failure
  MUST fail closed. Send the resolved token in exactly one Authorization header.
* `oauth`: configure HTTPS `issuer`, protected `resource`, `clientId`, `flow`,
  optional `scopes`, and optional `clientSecretRef`. Implementations MUST use
  standards-based protected-resource/authorization-server discovery and supported
  client registration. Interactive `authorization_code_pkce` MUST use PKCE;
  unattended `client_credentials` MUST be explicitly supported by the issuer.
  Validate issuer, resource/audience, expiry, and required scope. Token acquisition,
  refresh, and discovery are binding operations, not AHP methods or effects.
* `mtls`: configure `certificateRef`, `privateKeyRef`, and `trustRootsRef`.
  Validate the certificate chain, validity, intended usage, and receiver name.
  The resource maps the verified client certificate to authorized principals.
  A certificate copied into a header is not authentication.
* `workload`: configure `credentialRef`, trusted `issuer`, and target `audience`.
  Validate issuer proof, signature, audience, validity, and authorization using
  the configured platform's supported verification or exchange mechanism. A
  workload name, environment label, or self-signed untrusted assertion is not proof.

These mechanisms MAY compose at the transport layer (for example workload-to-OAuth
exchange or certificate-bound tokens). The current registration grammar selects
one mechanism per endpoint; composite configuration is not yet defined and MUST
NOT be claimed as portable support.

Authentication establishes the verified workload/client principal, not a human
operator, organization, role, or delegation. Additional claims require trusted
proof. Payload fields and correlation identifiers MUST NOT grant permissions.
Credential rotation and acquisition MUST NOT create hook lifecycle events.

## Extension policy

Existing protocol envelopes retain their extension behavior. Authentication
objects and operation-control objects are closed to catch misspellings and secret
injection; this does not close native payloads, tool arguments, or task state.

## Event-semantic capability bounds

Named definitions in capabilities.schema.json constrain both interception
advertisements and discovery entries by event. They bound effects, modification
targets, and flow operations; they do not create native capabilities. In particular,
model.response.after supports response modification and flow stop, return is not
a task-state mutation operation, and tool.before cannot request continuation.
Message is valid at any interceptable event. General flow rules permit stop on
supported turn/tool/permission/model boundaries even when an event-table row omits
it. Dynamic advertisements MUST narrow these bounds to actual enforceable support.

Compaction before permits only `modify(instructions)`; after permits only
`modify(summary)`. A before `return(summary)` skips generation but retains all
applicable after-boundary controls and downstream application. The after event
is a result boundary before downstream installation/consumption, not proof of a
confirmed context update. Only advertise controls the native harness can enforce.
Elicitation modes and selection-aware descriptors are defined by the pinned MCP
interaction binding. No absent mapping permits invented operations or policy
bypass.

`return` is bounded to `tool.before`, `model.request.before`,
`context.compact.before`, and `user.elicitation.request`. `continue` is bounded to
`turn.finish.before` and `tool.after`. Continuation advertisements include
nonnegative `remainingContinuations` and `continuationCount`; the static maximum
and dynamic allowance are enforceable runtime commitments. `inject.context`
advertises `append: true` and supported `deliverAt` values (`now`, `next_turn`).
Absent targets or false operations do not grant support. Optional request state
can carry flow, accumulated instructions, injections and candidate provenance,
but does not establish authenticated identity.

## Coverage and deliberate exclusions

There are no implicit event bundles or capability tiers. Each advertised event,
mode, effect, target, operation, path, content category, transport and authentication
mechanism needs evidence of its actual implementation. Before and after coverage
are independent. Synthesized observation coverage does not create interception
support. Unsupported native paths remain explicit gaps, not invented standard
capabilities. A legacy adapter’s manifest derives from its quirk registry, not
from a catalogue count or a hand-authored synthetic test manifest.

This draft adds no required MCP inventory, agent entity, context ledger, reasoning
lifecycle, durable deferred approval, retry effect, generic event collection,
response-cycle accounting entity, replay/cursor/acknowledgement, content
receipt-renewal protocol, or transactional rollback. HTTP, stdio and in-process
bindings share logical messages; evidence for one deployment binding does not
establish the others. External approval remains bounded by interception: there is
no defer or reverse-resolution channel, and mandatory approval must fail closed.
Late approval cannot authorize a later attempt.
