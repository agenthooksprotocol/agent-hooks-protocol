# SDK authentication lifecycle contract

This contract defines interoperable SDK host behavior for endpoint authentication.
It adds no AHP wire fields, JSON-RPC methods, portable credential values or
permission to replay deliveries. The [endpoint authentication requirements](capability-auth.md)
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

## Token response acceptance and persistence

`expires_in` is recommended, not required (RFC 6749 section 5.1). An absent expiry
means **unknown**, not expired or invalid. Such a token may be used until a
rejection or stricter explicit host policy; do not invent a short expiry or
refresh it on every call. Explicit invalid/expired expiry values remain distinct
from absence.

Keep requested scopes, locally allowed scopes and granted scopes separate.
The allowed ceiling defaults to requested scopes unless the host supplies a
broader ceiling. A provided response `scope` may narrow requested scopes or add
only scopes inside the local ceiling.
A grant need not equal the requested set. For an initial response, omitted
`scope` inherits the requested set; for a refresh response it inherits the
previously granted set (or an explicitly authorized narrowing on that refresh).
A refresh cannot broaden an existing grant: permitted broader requirements need
a new PKCE consent or a new client-credentials grant. Scope grammar is validated
and sets are deduplicated for comparison. Only explicit host requirements and validated
challenge requirements are mandatory for a particular operation; requested
scopes alone do not turn a valid narrowed token response into a failed exchange.

Persist a successfully received rotated refresh credential before reporting
operation-level scope insufficiency. Never discard it solely because expiry is
unknown or granted scopes differ from requested scopes. A token that fails local
validity/permission policy is not dispatched; any retained refresh material is
quarantined from use as an access credential until host policy permits repair.
No secret client configuration belongs in persisted metadata/token records.
Resolve current client secrets through host infrastructure when a grant needs
one; secret rotation does not change the grant's identity.

## Two storage identities

A binding authorizes one destination. A grant represents one OAuth principal
and scope-policy partition. Keeping these identities separate supports cached
unbound authentication without implicitly inheriting event credentials for an
upload. Client secrets, secret values/references, authentication method,
metadata capability arrays, endpoint URLs, expiry and actual granted/requested
scope sets are **not** grant identity fields.

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

allowed = sorted_unique(locally_allowed_scopes).join(" ")
grant_key = frame("ahp.oauth.grant:", [
  namespace, exact_issuer, exact_resource, client_id, flow, allowed, partition
])
```

Binding metadata stores the validated discovery source and non-secret principal
material needed to find a grant. Revalidate current local policy and any explicit
binding constraints before use. A client secret rotation, a newly advertised
grant type, or other non-identity metadata change does not permanently invalidate
a stored grant. Invalidate/rebuild obsolete binding metadata as needed, outside
the credential-store lock; do not leave a record permanently unusable merely
because a metadata/configuration snapshot differs.

By default event/upload/backend/destination bindings have separate grant
partitions; PKCE can therefore require separate consent. A host may explicitly
assign the same nonempty grant partition to independently authorized bindings
with the same namespace, issuer, resource, client, flow and allowed ceiling.
They may then reuse one grant/consent. This never authorizes a new destination:
each binding still needs independent local destination/purpose policy approval.
No upload binding or absent upload policy implicitly inherits event auth.

## Cached acquisition and metadata availability

Check a locally authorized stored token before performing online discovery.
Persist or otherwise retain trusted binding metadata so a recreated provider
can find a valid grant without an available metadata endpoint. Local policy is
still checked; a cache does not override issuer/resource/destination constraints.
Discover on a cache miss, explicit invalidation, or when renewed metadata is
needed for acquisition/refresh, outside the credential-store coordination lock.
Reload and recheck the grant after acquiring its lock to avoid refresh storms.

An explicit OAuth preset already fixes issuer/resource/client/flow. All SDKs
use its issuer metadata without requiring protected-resource metadata first.
An unbound endpoint learns its binding through standard protected-resource
discovery. Before that first learning, anonymous delivery is allowed under local
policy; after a trusted binding/grant is stored, use it **up front**, including
for notifications and uploads that cannot be replayed. An SDK must not knowingly
send such operations anonymously merely because the portable binding is absent.
Hosts can explicitly prepare an unknown endpoint before its first unsafe call.

Go preserves the existing bearer-only `Provider.Credential` success contract.
A separate optional preemptive-lookup capability may represent an unbound cache
miss with an absent credential; it never returns an empty successful bearer or
silently changes legacy custom-provider ownership. Other languages use their
native optional-credential result.

## Refresh failures and interaction lifetime

A 5xx, timeout, non-OAuth response or transient OAuth error preserves the last
known refresh material and is operationally retryable under host policy. It is
not, by itself, `interaction_required`. Client credentials can reissue a grant
using current credentials and never enter a human-login state. Definitive
interactive refresh `invalid_grant` requires new consent. If an exchange outcome
is ambiguous, retain that fact without permanently wedging the grant; expose
retry/host repair, not a fabricated definitive authorization loss. Retrying an
ambiguous rotating refresh may ultimately yield `invalid_grant`; no SDK can
promise atomic rollback at an external authorization server. Hosts may impose
stricter explicit rotation policy, but it must not be the implicit default.

Dispatch/credential/challenge paths do not create pending interactive logins.
They return `interaction_required` when consent is necessary. Only an explicit
host `prepare` owns pending login creation, its callback state and its consent
budget. A short delivery deadline never replaces or creates the host's pending
login. Reuse requires the same grant/principal partition, exact redirect identity,
requested scope set and explicit required scope set; the original host context
must still be active and its deadline must cover the new prepare budget. Reuse
never extends or transfers the original operation's deadline/cancellation ownership.
If any condition differs, an explicit prepare atomically invalidates/replaces
the old pending handle with a fresh host-owned operation; an old callback cannot
complete it. A host polling the same operation retains its original budget rather
than repeatedly creating a later one. `cancel` abandons a pending operation
without deleting a valid stored grant. All SDKs expose this action.

Validate callback redirect, state and issuer before consuming a pending login.
A stray invalid callback cannot burn a valid pending operation. Atomically claim
a valid callback once before code exchange; valid consent denial consumes that
operation. Loopback desktop/CLI redirects may use HTTP only to literal loopback
IP addresses under RFC 8252; remote redirects remain HTTPS. No implicit listener
or browser is created.

## Insufficient scope and replay

Recognize a Bearer `403` challenge with `error="insufficient_scope"`, not every
403 as an auth retry. Validate required scopes against the local allowed ceiling.
A forbidden scope fails with `access_denied`; a permitted step-up records the
binding's requirement. If the challenge omits `scope`, do not invent a broader
scope set: use an explicit host-provided requirement, or report `access_denied`. PKCE returns `interaction_required` so a subsequent host
`prepare` can request consent with its own budget. Client credentials can acquire
a new grant for permitted scopes without human login. Do not reuse a token known
to lack explicit operation requirements.

The transport alone decides whether to replay. Preserve the existing at-most-one
safe retry and never replay effectful deliveries, notifications or uploads just
because the auth provider handled a challenge. An unsafe operation may fail its
current attempt while preparing authorization state for a subsequent operation.
Unknown authentication schemes and valid padded token68 payloads do not corrupt
Bearer challenge parsing; malformed or ambiguous Bearer auth-params fail closed.

## Executable coverage

The shared JSON contains recovery/key/response-policy vectors and a **lifecycle
coverage manifest**, not an OAuth implementation or executable lifecycle runner.
The protocol fixture validator checks internal consistency only; it does not
prove any SDK's persistence, network behavior, cancellation or consent lifecycle.
Each SDK vendors these exact vectors and supplies executable native tests mapped
to the lifecycle IDs at real host-network/store/transport boundaries. In particular,
recreated-provider offline acquisition, narrowed-grant refresh scope inheritance,
transient rotating-refresh recovery, and scope/budget-incompatible pending reuse
must be exercised before claiming that SDK conforms. Native APIs and test names
may differ; the semantic vectors and observed behaviors may not.
