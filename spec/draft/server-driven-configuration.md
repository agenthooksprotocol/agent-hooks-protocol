# Server-driven configuration

## Discovery

<a id="AHP-REG-004"></a>
**AHP-REG-004 — MUST.** Explicit client subscriptions take precedence in full; only omitted subscriptions trigger backend configuration discovery, and an unsuccessful discovery or an empty supported subscription set skips that backend.

A backend MAY expose `hooks/configuration` on its configured event endpoint. This JSON-RPC request has `params.protocolVersion` and no event or session. Its correlated success result contains `protocolVersion` and `configuration`. The configuration is the shared subscription configuration object defined by `configuration.schema.json`; its non-empty `subscriptions` array uses the same subscription shapes as portable registration.

```json
{"jsonrpc":"2.0","id":"configuration-1","method":"hooks/configuration","params":{"protocolVersion":"draft"}}
```

```json
{"jsonrpc":"2.0","id":"configuration-1","result":{"protocolVersion":"draft","configuration":{"subscriptions":[{"events":["tool.before"],"mode":"intercept","timeoutMs":750,"failurePolicy":"fail-open","content":{"default":"metadata"}}]}}}
```

Discovery is configuration retrieval, not capability negotiation, registration acknowledgement, or an authentication bootstrap. It MUST NOT create a session, execute a model/tool call, or apply effects. A backend without a configuration advertisement returns JSON-RPC method-not-found. Harness capability discovery remains the separate `hooks/capabilities` method.

## Client precedence and admission

A client MUST distinguish omitted subscriptions from an explicitly supplied array. A present array completely replaces backend defaults, uses existing explicit-configuration validation, and MUST NOT depend on discovery succeeding. Clients need not send discovery for that backend. An explicit empty array remains invalid; it is not an instruction to fetch defaults.

When subscriptions are omitted, the client retrieves configuration during initialization or initial admission, before admitting any event delivery to that backend. A synchronous constructor MAY defer the asynchronous part to a single shared admission operation before first use; concurrent callers MUST NOT observe partially resolved routes. No new public initialization API is required. Reinitialization may reload configuration; this revision requires neither periodic refresh nor live negotiation.

The client MUST validate the complete discovery response, including correlation and exact protocol version, before using it. Invalid configuration is not repaired. Unknown object fields are ignored according to the base protocol; they do not expand the recognized configuration surface.

For a valid advertisement, the client resolves each subscription's selectors against its declared supported events and the requested delivery mode. Exact selectors, whole-family wildcards, and `*` keep their existing meaning. Unsupported event/mode matches are excluded. A subscription with no remaining matches is removed. All other settings are retained unchanged; interception MUST NOT be converted into observation and effects MUST NOT be invented. The resolved subscriptions retain advertised order, and backends retain client registration order.

A failed, unavailable, unauthenticated, malformed, version-incompatible, or timed-out discovery skips that backend. A valid advertisement with no supported subscriptions also skips it. Skipping means no registration or event delivery to that backend; the client SHOULD make the reason available through a local diagnostic without leaking credentials or content. There is no legacy-versus-modern distinction. Cancellation of initialization propagates as cancellation rather than being swallowed as an ordinary skip.

This mechanism does not prescribe application policy about continuing without a backend. It has no required/optional hook categories and performs no server-needs check. An advertisement is a source of defaults, not a delivery or effect guarantee. Existing per-request capabilities remain authoritative and may narrow the capabilities available for an individual interception.

## Connection and trust boundaries

Discovery uses the configured event transport, endpoint authentication, configured headers, redirect policy, and transport ownership rules. HTTP uses an ordinary JSON-RPC POST; stdio uses its existing framing and persistent or per-event process lifecycle. A successful HTTP discovery response uses status 200. All discovery work, including authentication, MUST be bounded by a client-selected operation deadline and support the transport's existing cancellation rules. No timeout or failure policy is inferred from a subscription that has not yet been discovered.

The advertisement contains subscription configuration only. It MUST NOT replace the client-owned backend identifier, transport, event URL, authentication, or HTTP headers. Configuration is untrusted input, not authority to resolve arbitrary local secrets or broaden local capability grants. Explicit client configuration remains the way to replace server defaults.

<a id="AHP-REG-005"></a>
**AHP-REG-005 — MUST.** Before admitting discovered configuration containing any upload settings, the client obtains explicit local authorization for that configuration; absent, denied, or failed authorization skips the backend without resolving upload credentials or forwarding configured headers to the advertised upload destination.

A remote advertisement alone MUST NOT authorize an upload destination, local environment/secret references, or forwarding client-configured backend headers. This applies even when the advertised upload has no `auth` block: existing backend headers may contain credentials and otherwise apply to uploads. Before any upload credential resolution, upload request, or route admission, the client MUST present the complete detached discovered configuration to an explicit client-owned authorization mechanism. The mechanism MUST make its decision over an immutable snapshot, or over a detached copy whose mutation cannot change the configuration that is subsequently admitted. Authorization may be an SDK callback or an equivalent explicit application grant; server-provided fields cannot grant it. With no authorization mechanism, a denial, or an authorization failure, the client MUST skip the entire backend. Cancellation propagates. A client MUST NOT merely remove `auth`, remove the upload, or forward headers without authorization and then admit the remainder.

This is a credential and destination trust boundary, not a requirement that applications continue or stop after a backend is skipped. Explicit client subscriptions remain locally authored configuration and retain their existing behavior; the extra authorization applies only to discovered upload settings. Clients MUST NOT invoke authorization or discovery as a prerequisite for explicit subscriptions.

Content selection and native-payload selection retain their subscription semantics. Adopting server defaults can increase disclosed content compared with a manually configured metadata-only subscription; clients MUST apply their existing local disclosure boundaries. Upload credentials remain independent of event credentials. The authorization decision does not itself resolve secret references or grant per-request effects.

## Shared SDK configuration

SDKs SHOULD expose one public subscription configuration model for client declarations, server advertisements, and JSON loading. Language-native constructors may use typed event selectors, durations, and content helpers, while serializing the same wire fields. The wire protocol does not prescribe builders, classes, callback names, or a particular programming language. Required timeout, failure policy, and content selections MUST NOT be silently supplied by an ergonomic helper.
