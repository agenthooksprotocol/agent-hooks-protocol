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
in `gaps`; absence never means unlimited. The `limits` object recognizes
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
empty injection delivery choices, and unsupported operation values are invalid.
Unknown object fields are ignored; they do not advertise or authorize new effects
or operations. Unsupported effect types and operation values in responses MUST
reject the entire response atomically, without applying any member.
The receiver MUST apply ordered append without deduplication for message-list
and text-part-list merge, and shallow object merge only when the actual target
and supplied value are objects. Text and attachment bytes are not object merge
targets. Replace uses the target’s canonical shape. The
manifest is not proof that an effect was actually applied.

## Registration

Subscriptions and their identifiers remain harness-local configuration. JSON-RPC
IDs correlate pending requests; neither those IDs nor caller-supplied metadata
authorize uploads. Upload authorization derives from independently configured
credentials and receiver policy.

Receivers MUST ignore unknown registration and configuration fields, including
fields in capability advertisements and control-state objects. They MUST validate
recognized fields and MAY warn about ignored fields. Ignoring an unfamiliar field
does not activate its semantics or relax validation of recognized fields, effect
types, or operation values.

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
optional independent `auth` object. It configures non-text, non-JSON media attachment uploads, not retrieval or
ordinary text delivery.
There is no backend `contentReceiver` compatibility alias. Absent upload
credentials MUST NOT inherit event credentials. A credential MAY be configured
for both endpoints only when its resource and permissions cover both. See
[the binary upload binding](content-upload.md) for framing and confirmation.

## Authentication bindings

Servers advertise authentication requirements; clients select identities and enforce
local trust policy. A server advertisement MUST NOT select a local secret, client
certificate, workload platform, or trusted issuer on the client's behalf.
`manifest.authentication` lists implementation support, not per-endpoint requirements
or a negotiation protocol. `hooks/capabilities` is not an authentication bootstrap.

Remote HTTP MUST use TLS with receiver validation. Plain HTTP is reserved for an
explicitly trusted local test/process boundary, never an implicit downgrade.
Secret values MUST NOT occur in registration, events, native metadata, logs, denial
reasons, or JSON-RPC error data. Credential references name deployment-managed
secrets, not portable secret values. Payload identifiers and upload references
MUST NOT grant authority.

### HTTP without an explicit binding

When endpoint authentication is absent, the client MUST attempt the configured
endpoint without an HTTP credential, subject to local policy and TLS identity
selection below. Absence does not mean anonymous-only: a successful response may
be accepted, but an OAuth-protected endpoint MUST be handled using the protected
resource discovery procedure in [MCP 2025-11-25 Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization),
[RFC 9728](https://www.rfc-editor.org/rfc/rfc9728.html), and authorization-server
metadata ([RFC 8414](https://www.rfc-editor.org/rfc/rfc8414.html) or the OpenID
Connect Discovery fallback specified by MCP). This adopts that discovery procedure,
not MCP transport framing or arbitrary authentication negotiation.
OAuth-protected servers MUST publish RFC 9728 protected-resource metadata with at
least one authorization server and provide discovery through a 401 challenge's
`resource_metadata` parameter or the standard well-known locations, as specified
by MCP. These are OAuth requirements, not a generic AHP auth advertisement format.

On HTTP 401, clients MUST parse `WWW-Authenticate` and use its `resource_metadata`
URL when present. Otherwise they MUST try the RFC 9728 well-known locations in
MCP's order: endpoint-path-specific, then origin-root. Protected-resource metadata
identifies the resource and its authorization servers. Clients MUST validate the
resource identifier under RFC 9728 section 3.3, select an authorization server
per local trust policy, and discover its metadata using MCP's ordered OAuth/OIDC
well-known locations. AHP defines no additional challenge, metadata field, or
method to negotiate bearer, mTLS, or workload credentials.

Discovery is untrusted input, not permission to send credentials. Clients MUST
validate HTTPS URLs, server certificates, exact discovered issuer identity,
resource/audience, scope requirements, and token validity. Challenge scopes describe
requirements, not consent: clients MUST request only locally permitted scopes and
MUST NOT assume `scopes_supported` is the required set for a particular request. Clients MUST request
resource-bound tokens using RFC 8707 and MUST NOT use a token for another resource.
Clients MUST apply local destination/SSRF policy to metadata, registration,
authorization, and token endpoints, including DNS resolution and every redirect;
metadata MUST NOT authorize access to otherwise forbidden local or private targets.
Redirects require explicit endpoint policy and MUST NOT forward credentials to a
different origin or resource. Clients MUST NOT send secrets, workload assertions,
or client credentials to arbitrary advertised URLs. A cached identity or token
MUST remain bound to its trusted issuer, resource, client, and authorized scope.

Client registration and selection of a supported OAuth flow remain client policy.
Interactive authorization MUST use authorization code with PKCE and validated
redirect URIs/state; unattended client credentials require explicit issuer support.
Clients MAY expose host UI/callbacks to prepare or renew authorization, but these
are not AHP methods, hook events, or effects. If interaction is unavailable,
unsupported, denied, cancelled, or cannot complete within the remaining budget,
authorization fails operationally; clients MUST NOT silently switch identities.

### Explicit endpoint bindings

An explicit binding takes precedence over automatic HTTP discovery. Failure to
resolve or use it MUST NOT trigger an automatic switch to OAuth, another binding,
or unauthenticated delivery. Discovery within an explicit OAuth preset MUST obey
its issuer, resource, scope, and client constraints.

* `bearer`: configure exactly one of `tokenEnv` or `tokenRef`, bound to this endpoint.
  If resolution fails, do not send the request. Send the resolved token in exactly
  one Authorization header. A challenge cannot choose a different local secret.
* `oauth`: an optional preset configures HTTPS `issuer`, protected `resource`,
  `clientId`, `flow`, optional `scopes`, and optional `clientSecretRef`. It uses the
  standards-based discovery and validation above; it is not required merely to
  encounter an OAuth-protected endpoint.

### Deployment-specific authentication

<a id="AHP-AUTH-001"></a>
**AHP-AUTH-001 — MUST.** Portable endpoint authentication bindings MUST use `bearer` or `oauth`; workload identity, federation, and client-certificate configuration are deployment-specific, not portable AHP authentication variants.

Deployments MAY use workload identity, federation, or client certificates beneath
or alongside AHP through HTTP/TLS configuration or credential-provider extensions.
These mechanisms are outside portable AHP configuration and capability claims;
AHP defines no workload profile requirement, plugin protocol, or discovery format
for them. A credential provider MAY supply a token through a portable bearer
reference without making its acquisition mechanism part of AHP.

TLS client certificate selection uses preconfigured identity and local endpoint/trust
policy before HTTP; it MUST NOT depend on an HTTP challenge. Validate certificate
chains, validity, intended usage, and receiver names. A certificate copied into an
HTTP header does not establish a verified TLS identity. Resources MUST authorize
the verified client principal. Deployment-specific workload/federation credentials
MUST be validated for issuer proof, signature, audience, validity, and authorization
under local trust policy. Deployment-specific
authentication MUST preserve the credential isolation, trust validation, failure,
deadline, cancellation, and no-downgrade requirements in this section.

Event and upload endpoints apply these rules independently, including discovery,
explicit binding precedence, and token/cache resource boundaries. An absent upload
binding MUST NOT inherit event credentials. Reuse requires explicit client policy
and independently validated resource and permission coverage for both endpoints.

### Failure and timing

Authentication resolution, discovery, acquisition, refresh, and validation failures
are operational failures. The configured interception failure policy determines
whether the underlying harness operation continues or is denied; it does not permit
unauthenticated delivery or change the underlying operation. Observations remain
best effort. Unsupported authentication MUST prevent authenticated retries and
further delivery; an initial credential-free request is not a failure fallback.
Protected servers MUST reject unauthorized requests before processing the event or
storing uploaded bytes. Authentication retries MUST be bounded, occur only after
an authentication rejection (not an ambiguous successful delivery), and respect
cancellation.

The interception deadline starts at dispatch and includes any authentication work
needed for that dispatch, including discovery, acquisition, refresh, interaction,
and retries. No authentication step resets or extends the deadline. Cancellation
MUST cancel the operation's pending authentication wait and prevent late delivery. A harness-shared acquisition needed by other active callers need not be cancelled; the cancelled operation MUST NOT resume delivery when that acquisition completes. Authorization MAY be
prepared before dispatch under a separate bounded, cancellable preparation budget;
content upload authentication and transfer use the independent upload budget. Any enclosing hook-call budget also bounds preparation, authentication waits, and upload transfer.
Pre-dispatch preparation MUST NOT become an unbounded wait hidden from policy.
See [failure semantics](base/failure.md) and [uploads](content-upload.md).

Authentication establishes the verified workload/client principal, not a human
operator, organization, role, or delegation. Additional claims require trusted
proof. Credential rotation and acquisition MUST NOT create hook lifecycle events.

### Client authentication integration

An SDK MAY delegate credential acquisition and challenge handling to a harness-owned provider rather than implement an OAuth manager. Delegation does not relax the authentication requirements above. The provider receives the selected registration authentication binding, destination, backend identity, independent event/upload purpose, and operation cancellation context. Challenge handling includes the authentication response and an identity for the credential attempt so a stale rejection need not invalidate newer credentials. Secret values MUST NOT appear in diagnostics or credential-attempt identifiers.

The portable bearer and OAuth bindings both yield bearer credentials for the AHP HTTP endpoint. Environment variables and credential references are resolved by the configured provider; OAuth client secrets are used at the token endpoint, not sent as AHP endpoint credentials. Missing configured secrets and unsupported mechanisms fail explicitly, never by anonymous fallback. HTTP/TLS deployment extensions remain separate transport configuration.

The harness may own discovery policy, token exchange, refresh, coordination, persistence, and explicit authorization UI. SDK discovery/exchange helpers are optional conveniences; no particular token cache, refresh scheduler, browser implementation, or durable store interface is required by AHP. The protocol client retains responsibility for endpoint isolation, safe credential application, bounded challenged retry, and validation of the final response. Choosing an external provider does not authorize an otherwise unsafe replay.

## Extension policy

Existing protocol envelopes retain their extension behavior. Authentication,
capability, and control configuration objects MUST ignore unknown fields and
validate recognized fields; implementations MAY warn about ignored fields.
Unknown fields never authorize operations or credentials. Unknown effect types
and operation values are unsupported semantics, not ignorable fields: a response
containing them MUST be rejected atomically. Native payloads, tool arguments, and
task state retain their own extension behavior.

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
Elicitation modes and selection-aware inline text parts are defined by the pinned MCP
interaction binding. `user.elicitation.request` permits deny, return, and
message only; it does not advertise request modification. Its parsed MCP params
are not a model `request` message-list target. `user.elicitation.result` permits
`modify(content)` only over the parsed MCP answer object for an accepted form
result, not its action or whole result. `user.message.outbound` uses the same
`content` target name for canonical `message.messages` lists. Hosts MUST enforce
these boundary-specific types; the standalone effect schema's list-or-object
union does not grant cross-boundary authority. Object merge remains shallow;
ordinary model and user-message targets retain list append semantics.
`tool.after` `modify(output)` targets canonical `items` messages, with whole-list
replacement and ordered append merge, not an undeclared native JSON output
binding. Objects and bare parts are not output values.
No absent mapping permits invented operations or policy bypass.

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
