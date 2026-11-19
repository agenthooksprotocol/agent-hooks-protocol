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
Only `access_denied` and `interaction_required` carry a scope set; see
[scope sets](#scope-sets).

## Scope sets

An SDK keeps these sets distinct:

* **Requested scopes**: the client's configured scopes and an OAuth binding
  preset's optional `scopes`. They are requested, never required.
* **Ceiling**: the caller's allowed scopes. It defaults to the requested scopes.
* **Required set** `R = H ∪ E ∪ Ch`: the host-required scopes configured for the
  client (H), the explicit required scopes passed to `prepare` (E), and the
  validated scopes of the operation's Bearer 403 `insufficient_scope` challenge
  (Ch; see [challenges](#challenges-step-up-and-replay)). Only R is mandatory for
  an operation.

Requested scopes, including preset scopes, MUST NOT be added to R or to any
error's scope set. A grant that lacks them still satisfies an operation whose R
is covered.

A requested scope outside the ceiling is a configuration error: the SDK MUST
fail with `invalid_configuration`, raised as an SDK auth error, never as a
native exception or `provider_failed`, and MUST NOT drop the scope silently.

If R contains a scope outside the ceiling, the operation MUST fail with
`access_denied` carrying R. The SDK MUST make this decision locally, before any
network I/O, on every path: a fresh or cached binding, `prepare`, credential
lookup and delivery, challenge handling, and preemptive credential lookup. A
host-required scope outside the ceiling is this case too, never
`interaction_required`, `provider_failed` or a native exception.

The scope set on an error is deduplicated, sorted in ascending byte order, and
contains only RFC 6749 scope tokens; it is never secret. Every `access_denied`
and `interaction_required` raised by an SDK scope check (ceiling refusal,
step-up, a grant narrower than R, `complete()`) MUST carry the full R of that
operation, except where this contract says it carries no scopes. A host passes
the set of an `interaction_required` back to `prepare` as explicit required
scopes. Only the SDK's own scope checks attach scopes: an error raised by a
host callback (client selection, token validation, destination checks) MUST
have any scopes stripped.

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
a refresh. A `scope` that is absent, JSON `null`, or a string containing no
non-whitespace characters (only SP, HTAB, CR, LF) MUST be treated as omitted:
servers send `""` to mean "nothing to report", and RFC 6749 section 3.3 makes it
an invalid scope list. Treating it as omitted never widens a grant, because the
intersections below still apply. Any other string MUST match the RFC 6749
scope-list grammar: scope tokens separated by exactly one SP. Leading, trailing
or repeated separators, and HTAB, CR or LF inside a non-blank list, are
malformed. Any other JSON type (number, boolean, array, object) is malformed. A
malformed `scope` fails basic validity. This applies equally to JSON and
form-encoded token responses: an absent form field is absent, not `""`, and a
present empty form field `scope=` is blank and treated as omitted.
An acquisition (PKCE consent or client credentials, including
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
deduplicated. A grant need not equal the requested set. Only R is mandatory:
an authorization-code exchange, refresh or client-credentials grant fails
(`access_denied` carrying R, operation-level, grant still persisted) only when
its effective grant does not cover R.
The ceiling governs what the SDK requests and records; it does not constrain what
an issued token can do. Hosts needing enforcement validate tokens in their own
policy hook.

A token response failing basic validity (non-Bearer `token_type`, malformed
`expires_in`, malformed `scope`, invalid token grammar) is not dispatched. Once
a code was consumed or a refresh token presented, no outcome leaves in-flight or
refreshing state behind: clear in-flight markers and report `provider_failed`
(`retry`). A failed initial exchange persists nothing; a failed refresh response
persists its rotated refresh token only if that token is valid, else retains the
previous one. Persist a rotated refresh token before reporting operation-level
scope insufficiency; never discard it because expiry is unknown or granted
scopes differ from requested scopes.
No secret client configuration belongs in persisted metadata/token records.
Resolve current client secrets through host infrastructure when a grant needs
one; secret rotation does not change the grant's identity.

## Two storage identities

A binding authorizes one destination. A grant represents one OAuth principal
and scope-policy partition. Keeping these identities separate supports cached
unbound authentication without implicitly inheriting event credentials for an
upload. The allowed ceiling, client secrets, secret values/references, an
issued (DCR) `client_id` and registration metadata, the
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
  namespace, exact_issuer, exact_resource, client_identity, flow, partition
])

client_identity = "preregistered:" + client_id     # pre-registered client
                | "cimd:" + client_metadata_url     # Client ID Metadata Document
                | "dcr"                             # dynamic registration
```

The client identity is the host's configured client source, as in MCP clients:
an issued DCR `client_id` never enters a key, so re-registering a client does
not move a grant.

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
with the same namespace, issuer, resource, client identity and flow.
They may then reuse one grant/consent. This never authorizes a new destination:
each binding still needs independent local destination/purpose policy approval.
No upload binding or absent upload policy implicitly inherits event auth.

## Client registration

An SDK obtains the client for a grant from exactly one source, selected per
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

The selected source is the grant key's client identity. A recorded selection
stays in use when only authorization-server metadata changes (newly advertised
CIMD support or a removed `registration_endpoint` does not move a working
grant). It is obsolete when current host policy changes it: a pre-registered
client now exists, or the recorded CIMD URL or DCR permission was removed or
changed. Then the SDK rebuilds binding metadata and reselects. A different
client identity is a different grant key: the old grant's tokens are never
presented under another client, are not migrated, and SHOULD be removed by
compare-and-delete. PKCE then needs new consent (`interaction_required`); client
credentials re-acquire non-interactively.

### Loopback redirects

PKCE desktop/CLI redirects may use HTTP only to a literal loopback IP
(`127.0.0.1` or `[::1]`) under RFC 8252; remote redirects remain HTTPS. A host
MAY bind a new ephemeral port for each login, as MCP clients do. Each
authorization request, DCR request and token request uses the login's exact
redirect, including its port (RFC 6749 section 4.1.3). Only the CIMD check below
compares a loopback redirect without its port, as authorization servers do
([RFC 8252 section 7.3](https://www.rfc-editor.org/rfc/rfc8252.html#section-7.3)).
Unlike MCP 2025-11-25, `localhost` is not accepted: following
[RFC 8252 section 8.3](https://www.rfc-editor.org/rfc/rfc8252.html#section-8.3),
a name can resolve to or be bound on a non-loopback interface. A `localhost` or
other non-loopback `http` redirect is `invalid_configuration`, checked before
client selection, registration or any pending login.

### Client ID Metadata Documents

The CIMD URL is host or adapter configuration (one published document per client
identity), not an AHP wire field. It is the `client_id` verbatim. Selection
checks CIMD applicability (PKCE, a configured URL, metadata support equal to
`true`) first; only then are the URL and redirect validated, so an unused CIMD
configuration never blocks DCR. A selected CIMD URL MUST use `https`, contain a
path, and contain no userinfo, fragment, or single- or double-dot path segments;
otherwise `invalid_configuration`. The host also declares the document's
`redirect_uris`, which MAY omit the port of a loopback redirect. Before creating
a pending login, its redirect MUST equal one of them, where a loopback `http`
redirect without userinfo is compared without its port; otherwise
`invalid_configuration` and no login is created.
The SDK MUST NOT fetch the CIMD URL or any URL derived from it: retrieval,
caching, and SSRF protection for that fetch are the authorization server's
concern. A CIMD client is public: token endpoint authentication is `none` and
there is no client secret. CIMD is not used for `client_credentials`, which
needs a pre-registered client or DCR with a confidential method. CIMD with
`private_key_jwt` is deployment-specific and outside this contract. An
authorization or token endpoint `invalid_client` for a CIMD client is
`invalid_configuration`; the SDK never falls back to DCR for that operation.

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

A host MAY supply an initial access token by secret reference for a specific
permitted exact issuer; it is sent as a Bearer credential only to that issuer's
`registration_endpoint` and never persisted. RFC 7592 client management is out
of scope: a `registration_access_token` or `registration_client_uri` in the
response is ignored and not persisted, and the SDK does not delete
registrations at the authorization server.

The request is a JSON `POST` with:

| Member | `authorization_code_pkce` | `client_credentials` |
| --- | --- | --- |
| `redirect_uris` | `[redirect]`, the login's exact redirect | omitted |
| `application_type` | `"native"` for a loopback redirect, otherwise omitted | omitted |
| `grant_types` | `["authorization_code", "refresh_token"]` | `["client_credentials"]` |
| `response_types` | `["code"]` | omitted |
| `token_endpoint_auth_method` | `none` | `client_secret_basic`, or `client_secret_post` when only that one is supported |

`client_name` (required) and optional `software_id`/`software_version` come from
the host policy that permits registration. `scope` is sent only when that policy
supplies a registration scope set; the SDK never derives it from one
operation's requested scopes.

A 200 or 201 response whose body is a JSON object is accepted only with a
non-empty string `client_id` and:

* echoed `redirect_uris`, when present, contain the requested redirect;
* echoed `grant_types`, when present, include `authorization_code` (PKCE) or
  `client_credentials`;
* echoed `token_endpoint_auth_method`, when present, equals the requested one;
* client credentials: a non-empty `client_secret`. For PKCE (method `none`),
  any `client_secret` and its expiry are ignored and never persisted, as the MCP
  TypeScript SDK does;
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

**PKCE.** As in MCP clients, DCR is part of the login. Only an explicit host
`prepare` registers, inside the `prepare` that creates the pending login and
with that login's exact redirect; the up-front lookup and delivery paths never
register and return `interaction_required`. Each new login registers anew, and
there is no separate registration record. The pending login holds the issued
`client_id`; completion commits that client with the tokens into the grant
record, replacing the previous client and tokens under the grant lock or by
compare-and-set. Refresh uses the client stored in the grant record. An
`invalid_client` at completion ends only that pending login and returns
`interaction_required`; the stored grant, which may belong to an older valid
client, is kept. An `invalid_client` at refresh returns `interaction_required`
and deletes the grant only if the stored record still holds the presented
`client_id` and refresh token (compare-and-delete). The next `prepare`
registers again.

**Client credentials.** Without a login to carry it, a DCR client for client
credentials is a persisted registration record under
`registration_key = frame("ahp.oauth.registration:", [namespace, exact_issuer])`.
The record holds `client_id`, the registered `token_endpoint_auth_method`,
`client_secret_expires_at`, a generation and an opaque secret reference; the `client_secret` itself is written only through the
host secret-storage hook (store, resolve, delete by reference), never into any
record, and the SDK has no implicit secret store. `prepare` and any
non-interactive acquisition, including the up-front lookup's re-acquisition and
challenge handling, MAY register when no usable record exists. A record is
unusable when its nonzero `client_secret_expires_at` has passed or its secret
cannot be resolved. Host `prepare` MAY replace a usable record so changed
registration settings take effect. Processes register once: under the registration key's lock,
the SDK reloads, registers only when no usable record exists, stores the secret
under a fresh reference, and persists by compare-and-set on the generation; a
loser deletes its reference and uses the winner's record. After a successful
replacement or compare-and-delete the old secret reference is deleted. An
`invalid_client` token response compare-and-deletes that generation; the SDK
then re-registers at most once per operation and re-acquires once, and a repeated
`invalid_client` is `credential_unavailable` and counts as a failed registration
for the backoff below, so a server that accepts registrations but rejects their
clients is not re-registered on every operation. After a failed registration the
SDK sends no registration request for that key until a backoff passes
(host-configurable; default 1 minute doubling per failure, capped at 1 hour, or
a later `Retry-After` up to that cap). Where a registration would be needed
meanwhile, the SDK returns the code of the last failure, so a retryable failure
stays `provider_failed` (`retry`); host `prepare` bypasses the backoff. When
processes share the store, the backoff SHOULD be shared through it; otherwise it
MAY be process-local.

### MCP parity

PKCE client handling matches MCP clients: the grant is keyed by the configured
client source, DCR runs per login with the actual ephemeral redirect, the issued
client is stored with the tokens, and the newest completed login wins. The
remaining AHP-only rules are deliberate: client-credentials registration records
(MCP defines no unattended DCR; without a login, a reusable record avoids
registering on every acquisition), the `localhost` exclusion (RFC 8252 section
8.3), and host trust policy (issuer permission for DCR, destination policy for
every endpoint, and allowed scope ceilings), because AHP clients deliver events
to backends they did not choose interactively.

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
Only an explicit host `prepare` creates one. As in MCP clients, a pending login
belongs to the process that created it, which receives its callback (for
example, on its own loopback listener) and completes it by `state`. A host MAY
instead persist pending logins so another process sharing the store can complete
one by `state`, for example from a pasted callback URL. The PKCE verifier is a
secret: it is never in metadata or logs, and when persisted it is held only in
the pending record. A
pending login's lifetime is fixed at creation (host-configured and bounded;
default 10 minutes), independent of the creating `prepare` call's deadline or
cancellation, and expired pending logins MUST be deleted. It holds its exact
redirect, client, PKCE verifier and acquisition ceiling, which bounds the grant
recorded on completion.

Several pending logins MAY exist for one grant key, up to a host-configurable
cap (default 5), counted per process for process-owned logins or per store when
persisted; creating one beyond the cap cancels the oldest, atomically in a
persisted store. Each requests
scopes by the acquisition rule in
[token response acceptance](#token-response-acceptance-and-persistence).
Completing one never invalidates another; each completion replaces the grant,
so the newest completed login wins, as in MCP clients. Concurrent step-ups may
therefore lose scopes the other login added; another `insufficient_scope`
challenge recovers them. Effective scopes stay
filtered by each caller's ceiling at use, and bindings share grants only through
explicitly host-assigned partitions. A pending login ends exactly one way:
completion (a claimed valid callback, including consent denial), `cancel`
(including by the cap), or expiry; a callback for an ended login fails with
`interaction_required`. `cancel` never deletes a valid stored grant. All SDKs
expose `cancel`.

Validate the callback's `state`, issuer and exact redirect before consuming a
pending login. A stray invalid callback cannot burn a valid pending operation.
Atomically claim a valid callback once before code exchange; valid consent
denial consumes that operation. No implicit listener or browser is created.

A pending login stores the R it was prepared with: H at `prepare` time ∪ E ∪
any Ch in the `prepare` context. `complete()` MUST succeed only when the
effective grant (granted ∩ current ceiling) covers that stored R. Otherwise it
fails with `access_denied` carrying the stored R; the grant and any rotated
refresh token are still persisted, and the login is consumed.

Every error thrown by `complete()` MUST carry a boolean login-ended flag: true
when the pending login named by the callback's `state` no longer exists after
the call (it was consumed, or it had expired or was unknown); false when it is
still pending, for example after a stray or invalid callback rejected before
the claim. Native names are TS `loginEnded`, Python `login_ended`, Go
`LoginEnded` and Rust `login_ended()`. The flag is host-facing SDK behaviour,
not a wire field, and does not change error codes or recovery.

## Challenges, step-up and replay

A 401 means the token was rejected. Challenge handling MUST retire the rejected
token before any other decision, then renew as for any 401. If the Bearer
challenge carries `scope`, the scopes within the ceiling are added to the
scopes requested by the next acquisition or sign-in; scopes outside the ceiling
are not requested. A 401's `scope` is **not** a step-up requirement: it MUST NOT
be added to R, and a 401 never yields `access_denied` because of its `scope`.

Only a Bearer 403 with `error="insufficient_scope"` creates a step-up
requirement (Ch); other 403s are not auth retries. Ch applies to the challenged
operation and the acquisition it triggers; it is **not** persisted as a binding
requirement. Later operations use the stored grant's effective scopes, which
after step-up consent include the broader scopes. If the challenge omits
`scope`, the SDK MUST NOT invent a broader scope set: it uses `H ∪ E` when that
is non-empty, and otherwise fails with `access_denied` carrying no scopes.

A challenge `scope` value MUST be RFC 6749 scope tokens separated by single SP.
An empty value, leading, trailing or repeated spaces, a character outside the
scope-token grammar, or a duplicated `scope` parameter is malformed: the SDK
MUST fail closed with `access_denied` carrying no scopes (recovery `none`). A
403 whose challenge cannot be parsed is not an auth retry at the transport.

R is then checked against the ceiling as in [scope sets](#scope-sets). When a
403 `insufficient_scope` arrives but the stored grant already records R as
granted within the current ceiling, the SDK MUST fail with `access_denied`
carrying R; it MUST NOT replay the same token, return `interaction_required` or
create a login. Otherwise PKCE returns `interaction_required` carrying R, and
the host passes R to a subsequent `prepare` to request consent. Client
credentials acquire a new grant for R without human login. Do not reuse a
token known to lack the operation's requirements.

The transport alone decides whether to replay, under
[challenged replay](base/transports/http.md#challenged-replay): at most once, with
the same request identity and body, and never for notifications or uploads.
Unknown authentication schemes and valid padded token68 payloads do not corrupt
Bearer challenge parsing; malformed or ambiguous Bearer auth-params fail closed.

## Executable coverage

The shared JSON contains recovery, key, response-policy, scope-decision,
completion, client-selection and registration-response vectors and a
**lifecycle coverage manifest**, not an
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
