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
| `discovery_failed` | `retry` | Retry under host budget/backoff policy. |
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
upload. The allowed ceiling, client secrets, secret values/references, the
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

## Cached acquisition and metadata availability

Check a locally authorized stored token before performing online discovery.
Persist or otherwise retain trusted binding metadata so a recreated provider
can find a valid grant without an available metadata endpoint. Local policy is
still checked; a cache does not override issuer/resource/destination constraints.
Full discovery (protected-resource, then authorization-server metadata) runs
only on a cache miss, explicit invalidation, host `prepare` or challenge
handling, outside the credential-store coordination lock. A `prepare` rebuilds
binding metadata, so stale persisted metadata always has a repair path.
Reload and recheck the grant after acquiring its lock to avoid refresh storms.

An explicit OAuth preset already fixes issuer/resource/client/flow. All SDKs
use its issuer metadata without requiring protected-resource metadata first.
An unbound endpoint learns its binding through standard protected-resource
discovery. Before that first learning, anonymous delivery is allowed under local
policy. Once a validated, locally approved binding is stored for the
destination, the client sends its stored credential on the first attempt,
including for notifications and uploads that are never replayed.
That lookup never creates an interactive login and never performs
protected-resource discovery. It MAY refresh an expired stored token, or
re-acquire a client-credentials grant, non-interactively using the persisted
authorization-server metadata; if that metadata is absent it MAY fetch
authorization-server metadata for the stored issuer only, and a failure is
`discovery_failed`. If a binding is known but its grant holds no usable token
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
atomically supersedes it with a login requesting old pending scopes ∪ ((new
requested ∪ new required) ∩ new caller's ceiling) and storing ceiling = old
ceiling ∪ new caller's ceiling. Both sets only grow, so alternating callers
converge; effective scopes stay filtered per caller at use. Reuse is not a confused-deputy risk: effective scopes are filtered by
each caller's ceiling at use, and bindings share grants only through explicitly
host-assigned partitions. A pending login ends exactly one way: completion
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

The shared JSON contains recovery/key/response-policy vectors and a **lifecycle
coverage manifest**, not an OAuth implementation or executable lifecycle runner.
The protocol fixture validator checks internal consistency only; it does not
prove any SDK's persistence, network behavior, cancellation or consent lifecycle.
Each SDK vendors this file byte-for-byte and supplies native tests at real
host-network/store/transport boundaries. Each SDK MUST have a test asserting
that every `lifecycle` id in the vendored fixture maps to an existing native
test. (Recommended, non-normative: a test or CI step checking that the vendored
fixture is byte-identical to this file at the SDK's pinned protocol ref.)

Native APIs and test names may differ; the semantic vectors and observed
behaviors may not.
