# Base Protocol

The Base Protocol is divided across:

- [Protocol model and versioning](versioning.md)
- [Messages and events](events.md)
- [Capabilities](capabilities.md)
- [Composition](composition.md)
- [Failure semantics and retries](failure.md)
- [JSON-RPC errors](errors.md)
- [Portable registration](registration.md)
- [Transport bindings](transports/index.md)

## Terminology and roles
<table>
<colgroup>
<col>
<col>
</colgroup>
<tr>
<td>Term</td>
<td>Definition</td>
</tr>
<tr>
<td>**Harness**</td>
<td>The agent runtime that is about to perform an operation. It is the AHP client.</td>
</tr>
<tr>
<td>**Backend**</td>
<td>The external policy, security, approval, or middleware component receiving AHP messages. It is the AHP server.</td>
</tr>
<tr>
<td>**Subscription**</td>
<td>An entry in a backend's registration that selects exact event names and a delivery mode, together with any mode-specific timeout and failure behavior.</td>
</tr>
<tr>
<td>**Interceptor**</td>
<td>A backend subscription using `intercept` mode. The harness waits for its response.</td>
</tr>
<tr>
<td>**Observer**</td>
<td>A backend subscription using `observe` mode. The harness does not wait for a decision.</td>
</tr>
<tr>
<td>**Operation**</td>
<td>The harness action represented by an event, such as a tool call.</td>
</tr>
<tr>
<td>**Effect**</td>
<td>A semantic instruction returned by an interceptor, such as `deny`.</td>
</tr>
<tr>
<td>**Operational failure**</td>
<td>A timeout, transport error, process failure, JSON-RPC error, malformed message, or invalid effect.</td>
</tr>
<tr>
<td>**Explicit denial**</td>
<td>A valid `deny` effect returned by a backend. It is not an operational failure.</td>
</tr>
<tr>
<td>**Native payload**</td>
<td>Optional provider-specific data retained for fidelity or diagnostics.</td>
</tr>
</table>
## Conformance profiles

<a id="AHP-CORE-001"></a>
**AHP-CORE-001 — MUST.** A conformance claim identifies the implementation role, each implemented transport binding, and each implemented capability profile.
<a id="AHP-CORE-002"></a>
**AHP-CORE-002 — MUST.** A conforming implementation implements the Base Protocol profile and at least one capability profile.

AHP v0.1 separates base protocol conformance from event-specific capability profiles. A conformance claim MUST identify:
- The implementation role: harness, backend, or both
- The implemented transport bindings: stdio, HTTP, or both
- The implemented capability profiles
An implementation cannot claim AHP v0.1 conformance from the Base Protocol profile alone. It MUST also implement at least one capability profile.
### Base Protocol profile
Every conforming AHP v0.1 implementation MUST:
- Correctly encode every JSON-RPC message it sends and decode every JSON-RPC message it receives, as defined in [Protocol model and versioning](versioning.md).
- Apply the version and unknown-field rules defined in this Working Draft.
- Implement at least one v0.1 transport binding.
- Apply the requirements for every capability profile it claims.
A conforming harness MUST also:
- Validate registration before using it for agent operations.
- Reject invalid or ambiguous interceptor configuration rather than silently skipping it.
- Generate stable event identifiers and preserve them across retries.
- Validate backend responses before applying effects.
A conforming backend MUST also:
- Ignore unknown object fields in otherwise valid messages.
- Return the defined JSON-RPC error for a request whose protocol version, method, or event it cannot process; notifications never receive error responses.
- Avoid requiring optional native or extension data unless it explicitly declares a non-portable extension dependency.
### Tool Interception profile
The Tool Interception profile is the minimum control capability defined by v0.1. It standardizes `tool.before` interception and the `deny` effect; it does not define the conceptual limit of future AHP event profiles.
To claim this profile as a harness, an implementation MUST support registration of one or more `tool.before` intercept subscriptions and MUST advertise and enforce `deny`.
Given a valid configuration containing one or more intercept subscriptions for `tool.before`, when the harness is about to execute a tool call covered by those subscriptions, it MUST:
- Construct and send `tool.before` through `hooks/intercept`.
- Advertise `deny` in the event's `capabilities.effects` array, declaring that the harness accepts and enforces a valid `deny` effect.
- Enforce each interceptor's configured deadline and failure policy.
- Execute matching interceptors serially in deterministic configuration order.
- Preserve event, session, and call identifiers across retries.
- Stop tool execution after an explicit denial or fail-closed operational failure.
- Continue to apply its own permissions, sandboxing, and approval flow if the chain completes without denial.
The harness MUST send an event to a backend only when that backend's registration contains a subscription whose `events` array includes the exact event name and whose `mode` matches the delivery method. If no intercept subscription matches `tool.before`, AHP adds no decision step and the harness continues its normal authorization flow.
To claim this profile as a backend, given a syntactically valid `hooks/intercept` request with protocol version `0.1` and event type `tool.before`, an implementation MUST return either an empty effect list or one valid `deny` effect.
### Lifecycle Observation profile
The Lifecycle Observation profile is OPTIONAL. A conforming implementation of this profile supports `hooks/observe` and one or more events defined in the [event model](events.md#event-model).
Observation is non-blocking and best effort. It is intended for control-adjacent audit, compatibility, and correlation. It is not a replacement for OpenTelemetry or a durable event pipeline.
A conformance claim for this profile MUST identify each event type the implementation emits or accepts.
## Privacy and security
### Sensitive data
Tool input, output, paths, model identifiers, native payloads, and denial reasons can contain sensitive data. Harnesses and backends SHOULD minimize collection and SHOULD support redaction, size limits, and retention controls.
Native payload delivery MUST default to disabled. Tool output is not needed for Tool Interception profile conformance.
### Authorization boundary

<a id="AHP-SEC-001"></a>
**AHP-SEC-001 — MUST NOT.** AHP does not weaken host authorization, permissions, approval, or sandbox controls; successful no-effect processing only means no AHP denial occurred.

AHP is an additional restriction point, not an authority-escalation mechanism. No AHP response can grant a capability that the harness, user, sandbox, or operating system has not granted.
The host remains responsible for validating and applying effects. Backends MUST be treated as untrusted input even when authenticated.
### Availability
Fail-closed policy can make a backend outage stop agent work. Deployments should use bounded timeouts, health monitoring, local caching where safe, and carefully chosen failure policy.
Fail-open policy preserves availability but may weaken external enforcement. The registration must make that tradeoff explicit.
### Replay and duplicates
Bearer authentication does not prevent replay by an attacker who obtains a valid token and payload. Security-sensitive remote deployments SHOULD use short-lived credentials and MAY add an authenticated request-signing extension before such a mechanism becomes standardized.
Backend side effects should be idempotent by event ID.
### Extension trust
Unknown extensions and native payloads MUST NOT be interpreted as trusted authorization claims. A backend that uses an extension for policy must separately establish its producer and integrity assumptions.
## Conformance tests
The v0.1 project should publish JSON Schemas, fixtures, and a conformance runner. Test reports MUST identify the implementation role, transport binding, and capability profiles under test.
### Base Protocol tests
Every conforming implementation test suite should verify, as applicable to its role:
- Correct JSON-RPC envelope and version handling
- Unknown object fields are ignored
- Unsupported versions, methods, and events produce defined errors
- stdio framing and stdout discipline for the stdio binding
- HTTP status and content-type handling for the HTTP binding
- Literal credentials never enter payloads
- Native payload omission by default
Harness tests should additionally verify registration validation, response validation, and stable event IDs. Backend tests should additionally verify response ID correlation and portable behavior without `native` or `extensions`.
### Tool Interception harness tests
A harness claiming the Tool Interception profile should verify:
- No AHP decision step occurs when no subscription covers `tool.before`
- Correct `hooks/intercept` request shape and JSON-RPC/event ID equality
- Required `tool.before` fields
- Stable event, session, and call IDs across retries
- `input` is an object rather than encoded JSON
- `deny` prevents tool execution
- Empty effects do not bypass host permissions
- Serial interceptor order
- Explicit denial short-circuits the chain
- Fail-open continues after each failure class
- Fail-closed denies after each failure class
- Deadline enforcement and late-response rejection
- Unsupported effects are protocol failures
### Tool Interception backend tests
A backend claiming the Tool Interception profile should verify:
- Valid empty-effect response
- Valid denial response
- Response IDs match request IDs
- Duplicate IDs produce equivalent semantic results
- Denial reasons and errors do not leak secrets
### Lifecycle Observation tests
Implementations claiming the Lifecycle Observation profile should additionally verify:
- Notifications contain no JSON-RPC ID
- No response is emitted for notifications
- Observer failure never affects tool execution
- Supported event types are included in the conformance claim
- Retries reuse event IDs
