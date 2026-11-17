# SDK authentication lifecycle contract

This contract defines interoperable SDK host behavior for endpoint authentication.
It adds no AHP wire fields, JSON-RPC methods or portable credential values, and
no replay beyond the HTTP binding's [challenged replay](base/transports/http.md#challenged-replay).
The [endpoint authentication requirements](capability-auth.md)
and their HTTP failure/deadline/isolation rules continue to apply. Public API
spelling follows each language; the semantic recovery values and storage-key
material below are shared. [Golden vectors](../../conformance/draft/sdk-auth-lifecycle.json)
cover these SDK-only decisions.

## Recovery values

SDKs expose `configure`, `login`, `retry` and `none`. Native enums/constants may
use language conventions, but their string representations use these values.
Recovery is advice to the host, not automatic replay permission. Cancellation
and deadlines retain the language's native cancellation identity.

| Safe error code | Recovery | Meaning |
| --- | --- | --- |
| `invalid_configuration` | `configure` | Repair local configuration. |
| `unsupported_binding` | `configure` | Install/configure a supported provider. |
| `credential_unavailable` | `configure` | Supply or repair host credentials. |
| `interaction_required` | `login` | Explicit host consent is required. |
| `access_denied` | `none` | Do not switch identities or retry automatically. |
| `refresh_rejected` | `login` | An interactive grant's refresh was definitively rejected. |
| `discovery_failed` | `retry` | Retry under host budget/backoff policy; if it persists (e.g. a changed issuer), the host repairs the binding with `prepare`. |
| `untrusted_destination` | `configure` | Repair local destination/trust policy. |
| `provider_failed` | `retry` | An operational failure, not proof of lost authorization. |

A client-credentials provider never requests human login: invalid client
credentials/configuration use `credential_unavailable`/`invalid_configuration`;
transient errors use `provider_failed`. An authorization-code exchange rejected
with `invalid_grant` consumes that valid pending operation and returns
`interaction_required` (a fresh host login), not `retry` for the consumed code.
Errors expose only allowlisted codes/recovery, not tokens, response bodies,
callback URLs, client secrets or arbitrary provider exception chains.
`interaction_required` MAY carry the validated required scope set (allowlisted
RFC 6749 scope tokens only, never secret); a host passes it back as explicit
required scopes to `prepare`. `access_denied` MAY report such a set for
diagnostics only. `prepare` fails with `access_denied` when its explicit required
scopes are not a subset of the caller's allowed ceiling.

## Token response acceptance and persistence

`expires_in` is recommended, not required (RFC 6749 section 5.1). An absent expiry
means **unknown**, not expired or invalid. Such a token may be used until a
rejection or stricter explicit host policy; do not invent a short expiry or
refresh it on every call. A present `expires_in` is valid only as a positive
JSON integer or a string of ASCII digits with a positive value; zero, negative,
non-integer values and other strings are malformed. Explicit invalid/expired
expiry values remain distinct from absence.

Keep requested scopes, locally allowed scopes and granted scopes separate.
The allowed ceiling defaults to requested scopes unless the host supplies a
broader ceiling. The response scope set is the provided `scope`; when omitted it
is the requested set for an initial response and the previously granted set for
a refresh. An acquisition (PKCE consent or client credentials, including
step-up or re-acquisition for an existing grant key) requests
`previously granted ∪ ((requested ∪ required) ∩ caller ceiling)` and records
`granted = response scopes ∩ (previously granted ∪ acquisition ceiling)`, where
the acquisition ceiling is the one stored on the pending login (PKCE) or the
acquiring caller's (client credentials); previously granted is empty for a new
grant. A refresh records `granted = response scopes ∩ previously granted`. The current caller's ceiling applies only at use
(`effective = granted ∩ current ceiling`), so narrowing a ceiling never shrinks a
stored grant. Scopes outside the recorded set (authorization
servers routinely add `openid`/`offline_access` or return every scope a client
holds) are **ignored**, not an error, and never recorded. A refresh therefore
cannot broaden a grant: broader permitted requirements need a new PKCE consent
or a new client-credentials grant. Scope grammar is validated and sets are
deduplicated. A grant need not equal the requested set. Only explicit host
requirements and validated challenge requirements are mandatory for an
operation; an exchange fails (`access_denied`, operation-level, grant still
persisted) only when such a required scope is missing from the effective grant.
The ceiling governs what the SDK requests and records; it does not constrain what
an issued token can do. Hosts needing enforcement validate tokens in their own
policy hook.

A token response failing basic validity (non-Bearer `token_type`, malformed
`expires_in`, invalid token grammar) is not dispatched. Once a code was consumed
or a refresh token presented, no outcome leaves in-flight or refreshing state
behind: clear in-flight markers and report `provider_failed` (`retry`). A failed
initial exchange persists nothing; a failed refresh response persists its
rotated refresh token only if that token is valid, else retains the previous one.
Persist a rotated refresh token before reporting operation-level scope
insufficiency; never discard it because expiry is unknown or granted scopes
differ from requested scopes.
No secret client configuration belongs in persisted metadata/token records.
Resolve current client secrets through host infrastructure when a grant needs
one; secret rotation does not change the grant's identity.

## Two storage identities

A binding authorizes one destination. A grant represents one OAuth principal
and scope-policy partition. Keeping these identities separate supports cached
unbound authentication without implicitly inheriting event credentials for an
upload. The allowed ceiling, client secrets, secret values/references, client
registration source and metadata, the
discovered token-endpoint authentication method, metadata capability arrays,
expiry and granted/requested scope sets are **not** part of either key, and
token, metadata and authorization-server endpoint URLs are not part of the grant key.

The namespace identifies the host account/credential domain and is required.
Issuer, resource and destination are exact validated identifiers: do not erase
trailing slashes, queries or meaningful path escapes. A syntactically valid
trailing slash is accepted when the corresponding identities match; it is not
made equivalent to an identifier without that slash.

Define `frame(prefix, fields)` as the ASCII prefix followed by each UTF-8 string
field encoded as its decimal **UTF-8 byte length**, `:`, then its bytes. There
is no delimiter after a field and no schema-version field. This is an opaque
host-store key, not a wire field. Scope strings use RFC 6749's ASCII grammar.

```text
binding_key = frame("ahp.oauth.binding:", [
  namespace, backend_id, purpose, exact_destination
])

partition = "binding:" + binding_key              # default isolation
partition = "shared:" + host_grant_partition       # explicit host opt-in

grant_key = frame("ahp.oauth.grant:", [
  namespace, exact_issuer, exact_resource, client_id, flow, partition
])
```

Ceiling compatibility is checked at use: a stored grant's effective scopes are
its recorded granted scopes ∩ the current allowed ceiling. Changing the ceiling,
including widening it, never orphans an existing grant.

Binding metadata stores the validated discovery source and non-secret principal
material needed to find a grant, and MUST persist the authorization-server
metadata the grant needs for refresh/acquisition: issuer, token endpoint,
supported token-endpoint authentication methods and any other endpoint or
capability the grant's flow uses. Revalidate current local policy and any explicit
binding constraints before use. A client secret rotation, a newly advertised
grant type, or other non-identity metadata change does not permanently invalidate
a stored grant. Invalidate/rebuild obsolete binding metadata as needed, outside
the credential-store lock; do not leave a record permanently unusable merely
because a metadata/configuration snapshot differs.

By default event/upload/backend/destination bindings have separate grant
partitions; PKCE can therefore require separate consent. A host may explicitly
assign the same nonempty grant partition to independently authorized bindings
with the same namespace, issuer, resource, client and flow.
They may then reuse one grant/consent. This never authorizes a new destination:
each binding still needs independent local destination/purpose policy approval.
No upload binding or absent upload policy implicitly inherits event auth.

## Client registration

An SDK obtains the `client_id` for a grant from exactly one source, selected per
exact issuer and flow in this order, mirroring
[MCP 2025-11-25 client registration](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization#client-registration-approaches):

1. **Pre-registered**: an explicit OAuth preset's `clientId`, or a client the host
   supplies for that namespace, issuer and flow. It always wins; CIMD and DCR are
   never attempted when one exists.
2. **Client ID Metadata Document (CIMD)**: only for `authorization_code_pkce`,
   when the host configures a client ID metadata document URL and the
   authorization-server metadata has `client_id_metadata_document_supported`
   equal to JSON `true`.
3. **Dynamic Client Registration (DCR,
   [RFC 7591](https://www.rfc-editor.org/rfc/rfc7591.html))**: only when host
   policy explicitly permits registration at that exact issuer and the
   authorization-server metadata advertises `registration_endpoint`.
4. Otherwise no client is available: `credential_unavailable` (the host supplies
   client information; this replaces MCP's "prompt the user").

Only the resulting `client_id` enters the grant key. Binding metadata records
the selected `client_id`, its source and, for DCR, the `registration_key`. A
recorded selection stays in use when only authorization-server metadata changes (newly advertised CIMD support or a removed
`registration_endpoint` does not move a working grant). It is obsolete when
current host policy changes it: a pre-registered client now exists, or the
recorded CIMD URL or DCR permission was removed or changed. Then the SDK
rebuilds binding metadata and reselects. A different `client_id` is a different
grant key: the old grant's tokens are never presented under another client, are
not migrated, and SHOULD be removed by compare-and-delete. PKCE then needs new
consent (`interaction_required`); client credentials re-acquire
non-interactively. A pending login records its `client_id` and, for DCR, the
registration generation it was created under, and completes with that client.
An `invalid_client` at completion compare-and-deletes only that generation, so a
stale callback after a forced re-registration never deletes a newer
registration, even one with the same reissued `client_id`.

### Client ID Metadata Documents

The CIMD URL is host or adapter configuration (one published document per client
identity), not an AHP wire field. It is the `client_id` verbatim. Selection
checks CIMD applicability (PKCE, a configured URL, metadata support equal to
`true`) first; only then are the URL and redirect validated, so an unused CIMD
configuration never blocks DCR. A selected CIMD URL MUST use `https`, contain a
path, and contain no userinfo, fragment, or single- or double-dot path segments;
otherwise `invalid_configuration`. The host also declares the document's
`redirect_uris`. Before creating a pending login, the redirect it will use
(stable per grant key; see
[interaction lifetime](#refresh-failures-and-interaction-lifetime)) MUST exactly
equal one of them; otherwise `invalid_configuration` and no login is created.
The SDK MUST NOT fetch the CIMD URL or any URL derived from it: retrieval,
caching, and SSRF protection for that fetch are the authorization server's
concern. A CIMD client is public: token endpoint authentication is `none`, there
is no client secret and no stored registration record. CIMD is not used for
`client_credentials`, which needs a pre-registered client or DCR with a
confidential method. CIMD with `private_key_jwt` is deployment-specific and
outside this contract. An authorization or token endpoint `invalid_client` for a
CIMD client is `invalid_configuration`; the SDK never falls back to DCR for that
operation.

### Dynamic Client Registration

DCR eligibility is checked in this order, without network access:

1. Host policy permits registration at the exact issuer and the metadata
   advertises `registration_endpoint`; otherwise `credential_unavailable`.
2. For client credentials, a host secret-storage hook exists and
   `token_endpoint_auth_methods_supported` (default `client_secret_basic`)
   includes `client_secret_basic` or `client_secret_post`; otherwise
   `credential_unavailable`.
3. The `registration_endpoint` is HTTPS and passes local destination policy;
   otherwise `untrusted_destination`. The request does not follow redirects.

A PKCE client is registered only by an explicit host `prepare`, before it
creates a pending login; the up-front lookup and other delivery paths never
register one and return `interaction_required` where a usable registration is
missing. For client credentials, `prepare` and any non-interactive acquisition,
including the up-front lookup's mandatory re-acquisition and challenge
handling, MAY register when the registration is missing, expired or obsolete,
subject to the failed-marker backoff below. During a successful
client-credentials acquisition, an SDK SHOULD re-register proactively when a
nonzero `client_secret_expires_at` is within the renewal margin, by default
`min(0.1 × (client_secret_expires_at − registered_at), 1 hour)`, where
`registered_at` is the SDK's local time when the record was stored (the optional
`client_id_issued_at` is not relied on). Under the registration-key lock it
reloads the record and re-checks the margin before registering, so only one
process renews. A failed proactive renewal updates the failed marker but does
not affect the operation: the SDK keeps using the current record until it is
actually unusable.
A host MAY supply an initial access token by secret
reference for a specific permitted exact issuer; it is sent as a Bearer
credential only to that issuer's `registration_endpoint`, never to another
issuer's, and never persisted. RFC 7592 client management is out of scope: a
`registration_access_token` or `registration_client_uri` in the response is
ignored and not persisted, and the SDK does not delete registrations at the
authorization server.

The request is a JSON `POST` with:

| Member | `authorization_code_pkce` | `client_credentials` |
| --- | --- | --- |
| `redirect_uris` | `[redirect]`, the stable redirect | omitted |
| `grant_types` | `["authorization_code", "refresh_token"]` | `["client_credentials"]` |
| `response_types` | `["code"]` | omitted |
| `token_endpoint_auth_method` | `none` | `client_secret_basic`, or `client_secret_post` when only that one is supported |

`client_name` (required) and optional `software_id`/`software_version` come from
the host policy that permits registration. `scope` is sent only when that policy
supplies a registration scope set; the SDK never derives it from one
operation's requested scopes.

A 200 or 201 response whose body is a JSON object is accepted only with a
non-empty string `client_id` and:

* echoed `redirect_uris`, when present, exactly equal the requested set;
* echoed `grant_types`, when present, include `authorization_code` (PKCE) or
  `client_credentials`;
* echoed `token_endpoint_auth_method`, when present, equals the requested one;
* PKCE: no `client_secret`. Client credentials: a non-empty `client_secret`;
* `client_secret_expires_at`, when present, is a non-negative JSON integer and,
  when nonzero, in the future. `0` or absence means no known expiry.

Echoed `scope` and unknown members are ignored. A rejected response persists
nothing. Outcomes are mapped in table order:

| Registration outcome | Code |
| --- | --- |
| 3xx redirect | `untrusted_destination` |
| 200/201 JSON object failing the checks above | `invalid_configuration` |
| Other 2xx, or 200/201 without a JSON object body | `provider_failed` |
| HTTP 401/403 (missing or rejected initial access token) | `credential_unavailable` |
| HTTP 408 or 429 | `provider_failed` |
| Any other 4xx, including OAuth errors such as `invalid_redirect_uri`, `invalid_client_metadata` or `invalid_request`, non-OAuth bodies and 404 | `invalid_configuration` |
| Network failure, timeout, or 5xx | `provider_failed` |

There is no SDK-driven immediate retry of a failed registration.

### Registration records and secrets

A DCR registration is stored separately from bindings and grants:

```text
registration_key = frame("ahp.oauth.registration:", [
  namespace, exact_issuer, flow, exact_redirect   # "" for client_credentials
])

request_digest = lowercase hex SHA-256 of frame("ahp.oauth.registration-request:", [
  client_name, software_id, software_version,
  registration_scope,          # deduplicated, sorted, space-joined
  initial_access_token_ref     # the reference, never the token
])                             # absent optional values are ""
```

The registration endpoint, request digest inputs, and issued values are not key
material. Registrations are reused by every grant with the same key, across
bindings and partitions in the namespace. Hosts SHOULD use stable redirects
(stable loopback ports). A host that uses ephemeral loopback ports with DCR and
a shared store MUST give each process a distinct grant partition, as above, and
should expect one registration per port.

The record holds `client_id`, `client_secret_expires_at`, the local
`registered_at` time, the accepted issued metadata, the `request_digest` it was
registered with, a generation, and, for a
confidential client, an opaque secret reference. The `client_secret` itself is
written only through a host secret-storage hook (store, resolve, delete by
reference), never into the registration, binding, grant or pending-login record;
the SDK has no implicit secret store or fallback. The SDK stores the secret under
a fresh reference before the record's compare-and-set, and deletes that
reference if the compare-and-set loses. Whenever a record is replaced or
compare-and-deleted, the SDK deletes the old record's secret reference through
the hook after the compare-and-set succeeds.

A record is unusable when its nonzero `client_secret_expires_at` has passed, its
secret cannot be resolved, or its `request_digest` differs from the digest of
current host registration policy. A digest mismatch compare-and-deletes the
record, so policy changes (name, scope, initial access token) take effect on the
next registration. Concurrent processes register once: under the registration
key's coordination lock, the SDK reloads and registers only when no usable
record exists, then persists by compare-and-set on the generation; a loser uses
the winner's record.

An `invalid_client` token response for a DCR client (an OAuth error, so not a
metadata refetch) compare-and-deletes that registration generation. Where
registration is allowed, client credentials then re-register at most once per
operation and re-acquire once with the new client. A PKCE code or refresh token
bound to the old client is never reused: the operation returns
`interaction_required`, and the next `prepare` registers a new client.

Every failed registration outcome (any row of the outcome table, including
`provider_failed` transport errors, 5xx, 408/429 and 401/403), and a repeated
`invalid_client` for a replacement client, updates a persisted **failed
marker** for the registration key; the operation returns the mapped error
(`credential_unavailable` for a repeated `invalid_client`). The marker is a
backoff, not a permanent block: it holds the current `request_digest`, the last
failure time, an attempt count, the next permitted attempt time and a marker
generation. The next attempt time is the exponential backoff (host-configurable;
default 1 minute doubling per attempt, capped at 1 hour) or, when the response
carried `Retry-After`, the later of that and the backoff, capped at a host
maximum (default 1 hour). Until then, paths other than host `prepare` send no
registration request and return `credential_unavailable` where one would be
needed. Marker reads and writes happen under the registration-key lock: after
reloading, the SDK checks the marker before any registration request and updates
it by compare-and-set on the marker generation, so attempt counts are not lost
and concurrent processes do not all pass the check. A successful registration,
a host `prepare`, or a changed `request_digest` (host policy change) clears it. Unattended client-credentials hosts therefore recover
automatically after the backoff; host `prepare` repairs immediately.

The marker blocks only registration requests. A still-valid stored token for
the registration's client keeps working, and PKCE recovery stays
`interaction_required`.

An authorization server that deletes a PKCE client sends no callback (RFC 6749
section 4.1.2.1), so the SDK cannot detect it. `prepare` therefore accepts an
explicit host option to force re-registration: it compare-and-deletes the
current registration generation (and its secret reference), clears any failed
marker and registers anew. The SDK never infers this from a pending login
expiring without a callback, since abandoned consent is indistinguishable and
would orphan a client and change the grant key on every abandoned login.

## Cached acquisition and metadata availability

Check a locally authorized stored token before performing online discovery.
Persist or otherwise retain trusted binding metadata so a recreated provider
can find a valid grant without an available metadata endpoint. Local policy is
still checked; a cache does not override issuer/resource/destination constraints.
Full discovery (protected-resource, then authorization-server metadata) runs
only on a cache miss, explicit invalidation, challenge handling, or a host
`prepare` that must perform an exchange or create a login, outside the
credential-store coordination lock. A `prepare` that finds a usable cached token
serves it without any metadata network call; otherwise it rebuilds binding
metadata, so stale persisted metadata always has a repair path.
Reload and recheck the grant after acquiring its lock to avoid refresh storms.

An explicit OAuth preset already fixes issuer/resource/client/flow. All SDKs
use its issuer metadata without requiring protected-resource metadata first.
An unbound endpoint learns its binding through standard protected-resource
discovery. Before that first learning, anonymous delivery is allowed under local
policy. Once a validated, locally approved binding is stored for the
destination, the client sends its stored credential on the first attempt,
including for notifications and uploads that are never replayed.
That lookup never creates an interactive login and never performs
protected-resource discovery. When it has the material and persisted
authorization-server metadata to do so, it MUST refresh an expired stored token,
or re-acquire a client-credentials grant, non-interactively; if that metadata is
absent it MAY fetch authorization-server metadata for the stored issuer only.
When a renewal using persisted metadata fails where the token endpoint provably
did not process the request (DNS resolution, TCP connect or TLS handshake
failure, or HTTP 404/410 from the token endpoint), it refetches the stored
issuer's authorization-server metadata once, persists it if the issuer identity
is unchanged, and retries the renewal once. An OAuth error response (such as
`invalid_grant` or `invalid_client`) never triggers a refetch, and an ambiguous
failure after the request was sent (read timeout, reset after send, 5xx,
non-OAuth body) is `provider_failed` with no SDK-driven immediate retry, so a
rotated refresh token is never replayed.
A failed fetch or changed issuer is `discovery_failed`. If a binding is known but its grant holds no usable token
and cannot be renewed this way (transient error, `invalid_grant`, metadata
failure, or interactive consent needed), the operation fails with that error;
there is no anonymous fallback. The client sends anonymously only on a store
miss or host policy refusal.
Hosts can explicitly prepare an unknown endpoint before its first
non-replayable call. A 401 or Bearer 403 `insufficient_scope` on a notification
or upload is not replayed, but still runs the provider's challenge handling:
discovery, refresh and client-credentials acquisition are allowed, login
creation is not, so the next operation sends a credential. Challenge handling
treats a rejected token as stale (if still current) even when its expiry is
unknown or in the future, so a revoked token is renewed rather than resent. An SDK represents a lookup miss as an
absent credential in its native optional result, never as an empty bearer.

## Refresh failures and interaction lifetime

A 5xx, timeout, non-OAuth response or transient OAuth error preserves the last
known refresh material and is operationally retryable under host policy. It is
not, by itself, `interaction_required`. Client credentials can reissue a grant
using current credentials and never enter a human-login state. A definitive
interactive refresh `invalid_grant` returns `refresh_rejected` (new consent is
required) and drops both access and refresh tokens only if the stored record
still holds the refresh token that was presented (compare-and-delete); a newer
record is kept. If an
exchange outcome is ambiguous, retain that fact without permanently wedging the grant; expose
retry/host repair, not a fabricated definitive authorization loss. Retrying an
ambiguous rotating refresh may ultimately yield `invalid_grant`; no SDK can
promise atomic rollback at an external authorization server. Hosts may impose
stricter explicit rotation policy, but it must not be the implicit default.

Dispatch/credential/challenge paths, including the up-front lookup, never create
pending logins; they return `interaction_required` when consent is necessary.
Only an explicit host `prepare` creates one. A pending login is a stored record
whose lifetime is fixed at creation (host-configured and bounded; default 10
minutes), independent of the creating `prepare` call's deadline or cancellation.
Completion MUST work from any process sharing the store; the creating context
need not still be active. The record stores its acquisition ceiling, which
bounds the grant recorded on completion. A later `prepare` reuses an
unexpired pending login, regardless of the new caller's deadline and without
extending its lifetime, when the grant key and exact redirect match and the new
requested plus required scopes are a subset of the pending scopes. Otherwise it
atomically supersedes it. When only the scopes differ (same grant key and
redirect), the new login requests old pending scopes ∪ ((new requested ∪ new
required) ∩ new caller's ceiling) and stores ceiling = old ceiling ∪ new caller's
ceiling; both sets only grow, so alternating callers converge, and effective
scopes stay filtered per caller at use. When the grant key or redirect differs,
the new login starts from the new caller's requested, required and ceiling alone. Reuse is not a confused-deputy risk: effective scopes are filtered by
each caller's ceiling at use, and bindings share grants only through explicitly
host-assigned partitions. Because reuse requires the exact redirect, hosts
sharing a store across processes MUST use a stable redirect per grant key; hosts
using per-process ephemeral loopback ports with a shared store MUST give each
process a distinct grant partition. A differing redirect supersedes the pending
login. A pending login ends exactly one way: completion
(a claimed valid callback, including consent denial), supersede, `cancel`, or
expiry; a callback for an ended login fails with `interaction_required`. `cancel`
never deletes a valid stored grant. All SDKs expose `cancel`.

Validate callback redirect, state and issuer before consuming a pending login.
A stray invalid callback cannot burn a valid pending operation. Atomically claim
a valid callback once before code exchange; valid consent denial consumes that
operation. Loopback desktop/CLI redirects may use HTTP only to literal loopback
IP addresses under RFC 8252; remote redirects remain HTTPS. No implicit listener
or browser is created.

## Insufficient scope and replay

Recognize a Bearer `403` challenge with `error="insufficient_scope"`, not every
403 as an auth retry. Validate required scopes against the local allowed ceiling;
a scope outside it fails with `access_denied` (the set MAY be reported for
diagnostics only). A permitted requirement applies to
the challenged operation and the acquisition it triggers; it is **not** persisted
as a binding requirement. Later operations use the stored grant's effective
scopes, which after step-up consent include the broader scopes. If the challenge
omits `scope`, do not invent a broader scope set: use an explicit host-provided
requirement, or report `access_denied`. PKCE returns `interaction_required`
carrying the required scopes; the host passes them to a subsequent `prepare` to
request consent. Client credentials can acquire a
new grant for permitted scopes without human login. Do not reuse a token known
to lack the operation's requirements.

The transport alone decides whether to replay, under
[challenged replay](base/transports/http.md#challenged-replay): at most once, with
the same request identity and body, and never for notifications or uploads.
Unknown authentication schemes and valid padded token68 payloads do not corrupt
Bearer challenge parsing; malformed or ambiguous Bearer auth-params fail closed.

## Executable coverage

The shared JSON contains recovery, key, response-policy, client-selection and
registration-response vectors and a **lifecycle coverage manifest**, not an
OAuth implementation or executable lifecycle runner.
The protocol fixture validator checks internal consistency only; it does not
prove any SDK's persistence, network behavior, cancellation or consent lifecycle.
Each SDK vendors this file byte-for-byte and supplies native tests at real
host-network/store/transport boundaries. Each SDK MUST have a test asserting
that every `lifecycle` id in the vendored fixture maps to an existing native
test. (Recommended, non-normative: a test or CI step checking that the vendored
fixture is byte-identical to this file at the SDK's pinned protocol ref.)

Native APIs and test names may differ; the semantic vectors and observed
behaviors may not.
