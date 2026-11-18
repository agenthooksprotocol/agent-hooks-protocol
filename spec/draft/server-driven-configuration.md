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

When subscriptions are omitted, the client retrieves configuration during initialization or initial admission, before admitting any event delivery to that backend. A synchronous constructor MAY defer the asynchronous part to a single shared admission operation before first use; concurrent callers MUST NOT observe partially resolved routes. No new public initialization API is required. Reinitialization may reload configuration; apart from the [skipped-backend retry](#skipped-backends) below, this revision requires neither periodic refresh nor live negotiation.

The client MUST validate the complete discovery response, including correlation and exact protocol version, before using it. Invalid configuration is not repaired. Unknown object fields are ignored according to the base protocol; they do not expand the recognized configuration surface.

For a valid advertisement, the client resolves each subscription's selectors against its declared supported events and the requested delivery mode. Exact selectors, whole-family wildcards, and `*` keep their existing meaning. Unsupported event/mode matches are excluded. A subscription with no remaining matches is removed. All other settings are retained unchanged; interception MUST NOT be converted into observation and effects MUST NOT be invented. The resolved subscriptions retain advertised order, and backends retain client registration order.

A failed, unavailable, unauthenticated, malformed, version-incompatible, or timed-out discovery skips that backend. A valid advertisement with no supported subscriptions also skips it. Skipping means no registration or event delivery to that backend. There is no legacy-versus-modern distinction. Cancellation of initialization propagates as cancellation rather than being swallowed as an ordinary skip, and emits no skip diagnostic.

This mechanism does not prescribe application policy about continuing without a backend. It has no required/optional hook categories and performs no server-needs check. An advertisement is a source of defaults, not a delivery or effect guarantee. Existing per-request capabilities remain authoritative and may narrow the capabilities available for an individual interception.

### Skipped backends

When discovery skips a backend, the SDK MUST emit a host-visible diagnostic carrying the backend identifier and exactly one code:

| Code | Cause | Retried |
| --- | --- | --- |
| `configuration_unavailable` | Transport failure, timeout, HTTP status other than 200, JSON-RPC error other than method-not-found, or an endpoint authentication failure | Yes |
| `configuration_invalid` | A transport that cannot be constructed from the registration, method-not-found, or an uncorrelated, malformed, or version-incompatible reply | No |
| `no_applicable_subscriptions` | A valid advertisement with no supported subscriptions | No |
| `configuration_local_secret` | A discovered local secret reference ([AHP-REG-005](#AHP-REG-005)) | No |

A reply is uncorrelated only when it is unambiguously the reply to the discovery request but its `id` does not match, such as the HTTP response body of that request. On a shared stream transport such as stdio, a frame whose `id` does not match the pending discovery request is not a reply to it: the SDK discards it, and if no correlated reply arrives before the discovery deadline, the skip is `configuration_unavailable`. Over HTTP, a status other than 200 is `configuration_unavailable`; a 401 or 403, or an authentication error from the provider, also carries `authError` and `recovery`. A 200 whose body is malformed, uncorrelated, version-incompatible, or a JSON-RPC method-not-found error is `configuration_invalid`. A transport that cannot be constructed from the backend's registration is a local configuration problem: `configuration_invalid`, never retried.

When the cause is an authentication error, the diagnostic also carries `authError`, the safe error code, and `recovery`, its paired recovery, both from the [SDK authentication lifecycle](sdk-auth-lifecycle.md) table. No other values are defined. The diagnostic MUST NOT contain credentials, tokens, response bodies, or event content.

A backend skipped with `configuration_unavailable` MUST be re-attempted at the next event admission or initialization after a successful host `prepare` for that backend's event endpoint. A successful `prepare` makes the backend eligible at once and resets its backoff. When the diagnostic has no `authError`, or its `recovery` is `retry`, the backend MUST also be re-attempted at the next event admission or initialization after a backoff ends. The backoff starts at 30 seconds, doubles after each failed attempt, and is capped at 1 hour. Hosts MAY configure these values. When `recovery` is `login`, `configure`, or `none`, the SDK MUST NOT re-attempt on backoff; only a successful `prepare` or reinitialization makes the backend eligible again. A retry is part of the single shared admission operation: concurrent callers wait for it or observe the previous routes, never a partial set, and a successful retry installs the backend's routes atomically. Events admitted before a retry succeeds are not replayed to that backend. Other codes are not retried until reinitialization.

A backend with omitted subscriptions has no failure policy until its configuration is valid, so it is advisory while its configuration is unavailable: no failure policy is inferred, and the SDK delivers events without it; whether to continue is host policy. Enforcement while a backend is unreachable or unauthenticated requires explicit subscriptions with `failurePolicy` `fail-closed`.

## Connection and trust boundaries

Discovery uses the configured event transport, endpoint authentication, configured headers, redirect policy, and transport ownership rules. HTTP uses an ordinary JSON-RPC POST; stdio uses its existing framing and persistent or per-event process lifecycle. A successful HTTP discovery response uses status 200. All discovery work, including authentication, MUST be bounded by a client-selected operation deadline and support the transport's existing cancellation rules. No timeout or failure policy is inferred from a subscription that has not yet been discovered.

The advertisement contains subscription configuration only. It MUST NOT replace the client-owned backend identifier, transport, event URL, authentication, or HTTP headers. Configuration is untrusted input, not authority to resolve arbitrary local secrets or broaden local capability grants. Explicit client configuration remains the way to replace server defaults.

<a id="AHP-REG-005"></a>
**AHP-REG-005 — MUST.** A client admits discovered upload settings as it admits any other discovered subscription setting, without a separate approval, except that discovered configuration whose upload `auth` names a local secret reference (`tokenEnv`, `tokenRef`, or `clientSecretRef`) skips the backend without resolving that reference.

A discovered upload is a modality of the content the backend already receives, not a new trust grant: the client has chosen to trust that backend with its events and its configured headers, and [those headers apply to the backend's uploads](base/registration.md#uploads-isolation-and-existing-transport-behavior) whichever way the upload was configured. A local secret reference is different, because it would make the client read a value the backend has never held. Such references can only be written in a client's registration. A client MUST NOT resolve a discovered reference, and MUST NOT remove it and admit the remainder; it MUST skip the entire backend.

Explicit client subscriptions remain locally authored configuration and retain their existing behavior, including local secret references. Clients MUST NOT invoke discovery as a prerequisite for explicit subscriptions.

Content selection and native-payload selection retain their subscription semantics. Adopting server defaults can increase disclosed content compared with a manually configured metadata-only subscription; clients MUST apply their existing local disclosure boundaries. Upload credentials remain independent of event credentials. Discovered configuration does not grant per-request effects.

## Shared SDK configuration

SDKs SHOULD expose one public subscription configuration model for client declarations, server advertisements, and JSON loading. Language-native constructors may use typed event selectors, durations, and content helpers, while serializing the same wire fields. The wire protocol does not prescribe builders, classes, callback names, or a particular programming language. Required timeout, failure policy, and content selections MUST NOT be silently supplied by an ergonomic helper.
