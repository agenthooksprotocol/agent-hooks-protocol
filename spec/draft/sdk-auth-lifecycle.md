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
[classify](#classify).

## Scope decisions

Each SDK MUST implement scope handling as one decision function
([decide](#decide)), one completion procedure ([complete](#complete)) and one
error-classification function ([classify](#classify)), exactly as specified
here. Every entry point calls them ([entry points](#entry-points-and-transport)).
No other code decides scopes, attaches scopes to errors or chooses a
scope-related error code.

### Inputs

* **S**, requested scopes: the client's configured scopes.
* **C**, the ceiling: the caller's allowed scopes. It defaults to S.
* **H**: the host-required scopes configured for the client.
* **E**: the explicit required scopes passed to `prepare`.
* **Preset**: an explicit OAuth binding preset. Its `scopes` member is
  optional: omitted means no constraint; present means it MUST equal S.
* **Grant**: the stored grant for the grant key, with a `generation`, a
  `granted` scope set, a `token` that is valid or expired, and a `refresh` token
  that is present or absent.
* **sent**: the grant generation whose token was sent on the rejected request,
  or none when no token was sent.
* **Challenge**: one of a 401 with an optional `scope` hint P; a Bearer 403
  `insufficient_scope` with a valid `scope`, giving Ch; a Bearer 403
  `insufficient_scope` without `scope`; another 403; or an unparseable or
  ambiguous challenge.
* **R = H ∪ E ∪ Ch**, the required set. Only R is mandatory for an operation.
  S, preset scopes and 401 hints are never part of R.
* **Scope set on an error**: deduplicated, sorted in ascending byte order, and
  only valid RFC 6749 scope tokens. A set with an invalid token is invalid
  configuration, never silently filtered. It is never secret.

### Decide

`decide(op, config, grant, sent, challenge)` returns exactly one outcome. The
steps run in this order; the first that matches wins. Step 0 runs first and
never ends the decision.

0. **401 retires first.** On a 401, retire generation `sent` if it is still the
   current generation. This happens before every other step and before every
   target or configuration check (an unsupported binding, invalid arguments, a
   missing deadline, an incompatible stored binding, a host callback failure),
   whatever the outcome, including a malformed or unparseable challenge.
   Whenever the rejected grant can be identified, it is retired. Only an
   operation whose budget is already exhausted or aborted, so that no I/O is
   possible, may fail before step 0.
1. **Configuration.** Fail `invalid_configuration` with no scopes if S ⊄ C, if
   the preset's `scopes` is present and differs from S, or if S, C, H or E
   contains an invalid scope token. This is an SDK auth error, never a native
   exception or `provider_failed`, and the SDK MUST NOT drop a scope silently.
2. **Challenge form.** Fail `access_denied` with no scopes for an unparseable or
   ambiguous Bearer challenge: a malformed auth-param (such as an unterminated
   quoted string), two Bearer challenges, a duplicated parameter, a token68
   directly after `Bearer`, or a malformed `scope` value ([entry
   points](#entry-points-and-transport)). Under RFC 9110 a bare token after a
   comma that follows Bearer auth-params, not followed by `=`, starts a new
   challenge: `Bearer error="insufficient_scope", scope` is a Bearer challenge
   without `scope` followed by a challenge whose scheme is `scope`, not a
   malformed auth-param. A following challenge whose scheme is `Bearer` makes
   the header ambiguous. A challenge `scope` MUST be RFC 6749 scope tokens
   separated by single SP; an empty value, HTAB, repeated, leading or trailing
   spaces, or a character outside the scope-token grammar is malformed. Fail
   `access_denied` with no scopes for a 403 that is not `insufficient_scope`,
   and for a 403 `insufficient_scope` without `scope` when H ∪ E is empty.
3. **401 hint.** The token was already retired at step 0. A well-formed hint
   is P′ = P ∩ C. It is used only by a client-credentials acquisition in this
   same operation (step 8). It MUST NOT be stored, added to R, or cause
   `access_denied`; a malformed 401 `scope` has already failed at step 2.
4. **Ceiling.** If R ⊄ C, fail `access_denied` with R. A host-required scope
   outside the ceiling is this case too, never `interaction_required`,
   `provider_failed` or a native exception. When the binding or a preset is
   known, the SDK MUST decide this locally with no network I/O. An unbound
   target MAY first run one protected-resource metadata discovery, which client
   selection needs, and MUST make no authorization-server request (metadata,
   registration, token or authorization). That discovery is one request to the
   challenge's `resource_metadata` URL when the challenge parses and carries
   one; otherwise it tries the RFC 9728 well-known locations in MCP's order,
   endpoint-path-specific then origin-root, so at most two requests. An
   unusable challenge (unparseable, or without `resource_metadata`) never fails
   discovery; it falls back to those locations.
5. **Server disagrees with the record** (403 `insufficient_scope` only). If
   the grant's generation equals `sent`, or `sent` is none, and
   granted ∩ C ⊇ R, fail `access_denied` with R; the SDK MUST NOT replay the
   token or create a login. Otherwise, if the current generation differs from
   `sent` and granted ∩ C ⊇ R, return **use** with the current token.
6. **Use.** If the token is valid and granted ∩ C ⊇ R, return **use**.
7. **Refresh** (PKCE grants only). If a refresh token is present and the
   previous granted ⊇ R, refresh. Record the response scopes ∩ the previous
   granted. If the result does not cover R, fail `access_denied` with R; the
   grant and any rotated refresh token are still persisted.
8. **Client credentials.** Reissue the grant with current client credentials,
   even when it holds a refresh token, requesting
   previous ∪ ((S ∪ R ∪ P′) ∩ C). Record the response scopes ∩ (previous ∪ C).
   If the result does not cover R, fail `access_denied` with R; the grant is
   still persisted.
9. **PKCE.** For `op = prepare`, create a pending login that requests
   previous ∪ ((S ∪ R) ∩ C) and stores R. For any other op, fail
   `interaction_required` with R; the host passes that set back to `prepare` as
   explicit required scopes.

A step-up requirement (Ch) is operation-scoped and MUST NOT be persisted on a
binding or grant. Later operations use the stored grant, which after step-up
consent includes the broader scopes.

### Complete

`complete(callback)` runs these steps in order.

1. **Look up the pending login by the callback's `state`**, before any host
   callback runs. If it is absent, unknown, expired or ended, fail
   `interaction_required` with no scopes and the login-ended flag true.
2. **Validate the callback.** For an SDK whose `complete` takes a pending-login
   handle, a handle that names a different login than the callback's live
   `state` is an invalid callback, and both logins stay pending. The callback
   MUST match the exact redirect (scheme, host, port, path and the redirect's
   own query parameters; parameters the authorization server adds, such as
   `session_state`, are allowed), contain no
   fragment or userinfo, carry exactly one `state`, an `iss` equal to the issuer
   when present or required by the authorization server, exactly one of `code`
   or `error`, and no duplicated parameter. An unparseable callback URL is an
   invalid callback. An invalid callback fails `access_denied` with no scopes
   and the flag false, and MUST NOT consume the login, so a stray callback
   cannot burn a valid pending operation.
3. **Host policy** before the claim, for every valid callback including a
   consent denial: client selection, a client source that is no longer
   permitted, and destination checks (the token-endpoint destination and
   metadata approval). A failure keeps the host's
   code (`invalid_configuration` when the client source is no longer
   permitted), carries no scopes, leaves the login pending and sets the flag
   false.
4. **Claim the login atomically.** From here on the flag is true. If the
   claim's outcome is uncertain (store error, deadline), re-read the store on
   a fresh short budget: the flag is true if the login is gone, and false if it
   still cannot be determined.
5. **Consent denied** (`error` on the callback): fail `access_denied` with no
   scopes, except that `error=invalid_client` for a CIMD client fails
   `invalid_configuration` ([CIMD](#client-id-metadata-documents)).
6. **Exchange the code.** A token-endpoint `invalid_grant` fails
   `interaction_required`. `invalid_client` fails by client source:
   `credential_unavailable` for a pre-registered client,
   `invalid_configuration` for a CIMD client, and `interaction_required` for a
   DCR client, whose next `prepare` registers again
   ([DCR](#dynamic-client-registration)). Every other token-endpoint error
   (including `unauthorized_client`, `access_denied` and `invalid_scope`), an
   unreachable or 404 endpoint, and an invalid token response fail
   `provider_failed`. None of these carry scopes.
7. **Persist the grant**, recording the response scopes ∩ (previous ∪ the
   ceiling stored on the login). If granted ∩ current C does not cover the
   stored R, fail `access_denied` with the stored R. A pending record written
   by an older release has no stored R; for it, use the prepare-time H if the
   record holds that field, even when it is empty, and the current H only when
   the field is absent.

The login-ended flag is a boolean on every error from `complete`: true when the
pending login named by the callback's `state` no longer exists after the call.
It always describes that login, never the one a host handle names.
Native names are TS `loginEnded`, Python `login_ended`, Go `LoginEnded` and
Rust `login_ended()`. It is host-facing SDK behaviour, not a wire field, and
does not change error codes or recovery. No implicit listener or browser is
created.

### Renewal errors

Token-endpoint errors from a refresh ([decide](#decide) step 7) or a
client-credentials grant (step 8) map as follows; none carries scopes.

* `invalid_grant`: `refresh_rejected` for a PKCE refresh
  ([refresh failures](#refresh-failures-and-interaction-lifetime)), and
  `credential_unavailable` for a client-credentials grant.
* `unauthorized_client`: `credential_unavailable`.
* `invalid_client`, by client source as in [complete](#complete) step 6:
  `credential_unavailable` for a pre-registered client, `invalid_configuration`
  for a CIMD client, and `interaction_required` for a PKCE DCR client. A
  client-credentials DCR client re-registers once and re-acquires; a repeated
  `invalid_client` is `credential_unavailable`
  ([DCR](#dynamic-client-registration)).
* Every other token-endpoint error (including `access_denied` and
  `invalid_scope`), an unreachable or 404 endpoint, and an invalid token
  response: `provider_failed`.

### Classify

`classify(error)` is the only place that sets an error's code, scope set and
login-ended flag.

* Scopes are attached **only** to the `access_denied` and
  `interaction_required` outcomes of [decide](#decide) and of
  [complete](#complete) step 7. Every other error, including errors raised by
  host callbacks, host stores, the host network and the token endpoint,
  carries none.
* Scope attachment is opt-in: an SDK adds scopes where a rule calls for them;
  it does not attach them everywhere and strip them in some places. Errors not
  produced by the SDK's own scope logic always have any scopes stripped.
* The login-ended flag appears only on errors from `complete`, never on errors
  from other operations, and is never copied from a host error.

### Entry points and transport

Every public entry point that yields a credential or handles a challenge MUST
call [decide](#decide): credential lookup and delivery, `prepare`,
`challenge`, preemptive and proactive credential paths, and any legacy
provider adapter (such as TS `auth()`). `complete` follows
[complete](#complete). Each SDK lists its entry points in a routing test that
asserts each one goes through `decide`.

The transport MUST hand every 401 to the provider. It hands a 403 to the
provider only when its header parses as exactly one Bearer challenge with
`error="insufficient_scope"`; scope validation is the provider's job
(step 2). It returns any other 403 without a retry. A token rejected on the
replay is also handed to the provider, so a 401 retires it.

The transport alone decides whether to replay, under
[challenged replay](base/transports/http.md#challenged-replay): at most once, with
the same request identity and body, and never for notifications or uploads.
A Bearer challenge carries auth-params only (RFC 6750). A token68 directly
after the `Bearer` scheme, such as `Bearer abc==` or a lone `Bearer scope` with
no `=`, is a malformed Bearer challenge: `access_denied` with no scopes
([decide](#decide) step 2). Other schemes, including one carrying a token68,
are skipped and do not affect Bearer parsing. This differs from a bare token
after a comma that follows Bearer auth-params, which starts a new challenge
(step 2). Malformed or ambiguous Bearer auth-params fail closed.

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
`previously granted ∪ ((requested ∪ required) ∩ caller ceiling)`, plus a
same-operation 401 hint for client credentials ([decide](#decide) step 8), and records
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
its effective grant does not cover R ([decide](#decide) steps 7 and 8,
[complete](#complete) step 7).
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
redirect, client, PKCE verifier, acquisition ceiling, which bounds the grant
recorded on completion, and the required set R it was prepared with
([decide](#decide) step 9).

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

Callbacks are handled by [complete](#complete).

## Executable coverage

The shared JSON contains recovery, key, response-policy, scope-decision,
completion, token-error, client-selection and registration-response vectors and a
**lifecycle coverage manifest**, not an
OAuth implementation or executable lifecycle runner.
The protocol fixture validator checks internal consistency only; it does not
prove any SDK's persistence, network behavior, cancellation or consent lifecycle.
Each SDK vendors this file byte-for-byte and supplies native tests at real
host-network/store/transport boundaries. Each SDK MUST have a test asserting
that every `lifecycle` id in the vendored fixture maps to an existing native
test. (Recommended, non-normative: a test or CI step checking that the
vendored fixture is byte-identical to this file at the SDK's pinned protocol ref.)

Each SDK MUST run every `scope_decisions`, `completions` and `token_errors`
vector against its [decide](#decide), [complete](#complete) and [renewal
error](#renewal-errors) implementations in a table-driven test. Every field a
vector defines MUST be asserted, and a field not defined here MUST fail the run.

A vector's optional `requires` lists optional SDK features it depends on. It
applies only to SDKs with all of them; an SDK without one MUST skip the vector
and MUST NOT remap it onto a different mechanism. Every vector without
`requires` applies to every SDK. The features are:

* `token_validation_callback`: the SDK exposes a host token-validation
  callback.
* `complete_handle`: the SDK's `complete` accepts a pending-login handle.
* `legacy_prepare_time_host_required`: the SDK's previous release persisted
  the prepare-time host-required scopes on pending logins. An SDK without it
  MUST NOT persist a new field only to satisfy those vectors; the absent-field
  fallback to the current H applies to every SDK.

A `scope_decisions` vector has `id`, optional `requires`, `input` and `expect`.
Its `input` members are:

* `flow`: `authorization_code_pkce` or `client_credentials`.
* `path`: the entry point: `fresh` (first use of a target), `cached`,
  `prepare`, `credential` (lookup and delivery), `challenge` or `preemptive`.
  `op` is `prepare` exactly when `path` is `prepare`. With a `stored` or
  `preset` binding, `fresh` is the first credential lookup. With an `unbound`
  target, `fresh` is an anonymous request (no token sent) that receives the
  vector's 401 and is handled by `challenge`; such a vector has `challenge`
  with status 401 and `sent` `null`, and every SDK runs it that way.
* `binding`: `stored` (a stored binding), `preset` (an explicit preset) or
  `unbound`.
* `requested` (S), `host_required` (H) and `explicit` (E): scope lists.
* `ceiling` (C): a scope list; when absent, C is `requested`.
* `preset_scopes`: the preset's `scopes`, present only when that member is
  present; only with `binding` `preset`.
* `grant`: `null`, or an object with `generation` (integer), `granted` (scope
  list), `token` (`valid` or `expired`) and `refresh` (boolean).
* `sent`: present only with `challenge`; the generation whose token was sent,
  or `null` when none was sent.
* `challenge`: `status` (401 or 403) and the raw `www_authenticate` header.
* `prior_401_hint`: a scope list from a 401 in an earlier operation; it MUST
  NOT affect this operation.
* `host_error`: an error a host callback raises, with `callback`
  (`client_selection`, `token_validation` or `destination`), `target` for
  `destination` (`registration_endpoint`, `issuer_metadata` or
  `token_endpoint`), `code` and the `scopes` it carries.
* `target_error`: a target or configuration check that fails before any
  decide step, with `check` (`unsupported_binding`) and the `code` it yields;
  a 401 still retires first.

Its `expect` members are:

* `code`: the error code, or `null` for success.
* `scopes`: the error's scope set; present exactly when `code` is
  `access_denied` or `interaction_required`.
* `required`: R, sorted; present on every outcome that reaches
  [decide](#decide) step 4.
* `action`: present exactly when `code` is `null`: `use`, `refresh`,
  `acquire` or `login` (a pending login is created).
* `acquisition_scopes`: present exactly when `action` is `acquire` or `login`;
  the sorted scopes requested.
* `retired`: present exactly for a 401, whatever the outcome; whether
  generation `sent` was retired at [decide](#decide) step 0.
* `authorization_server_requests` and either `network_calls` or
  `network_calls_max`: present on failures decided at steps 1, 2, 4 and 5. They
  count requests made after the inputs (after the challenged response on the
  challenge path): authorization-server requests (metadata, registration, token
  and authorization), and all network requests exactly (`network_calls`) or at
  most (`network_calls_max`, for an `unbound` target). For an `unbound`
  target `network_calls_max` counts the protected-resource metadata
  discovery's requests: 1 when the challenge parses and carries
  `resource_metadata`, otherwise 2 (the path-specific and origin-root
  well-known locations).

A `completions` vector has `id`, optional `requires`, `input` and `expect`.
Its `input` members are:

* `pending`: the login named by the callback's `state`: `pending`, `expired`,
  `ended`, `unknown`, or `absent` (no `state`).
* `handle`: the handle the host passes: `matching`, `other` (names a different
  login than the callback's live `state`; an invalid callback) or `none`. An SDK without `complete_handle` passes none for `matching`.
* `client_source`: `preregistered`, `cimd` or `dcr`.
* `source_permitted`: whether host policy still permits that client source.
* `callback`: `valid`, `valid_with_server_params` (adds `session_state`),
  `denied` (`error=access_denied`), `denied_invalid_client`
  (`error=invalid_client`), or the defect that makes it invalid:
  `redirect_mismatch`, `redirect_query_mismatch`, `state_duplicated`,
  `iss_mismatch`, `fragment`, `userinfo`, `missing_code`, `code_and_error`,
  `duplicate_param` or `unparseable`.
* `host_error`: a pre-claim host callback failure, as in `scope_decisions`.
* `claim`: the string `ok`, or an object for an uncertain claim with
  `outcome` (always `uncertain`), `error_code` (the store error's code) and
  `reread` (`gone`, `pending` or `undetermined`).
* `exchange`: `ok`, a token-endpoint OAuth error code (`invalid_grant`,
  `invalid_client`, `unauthorized_client`, `access_denied`, `invalid_scope`,
  `server_error`), `unreachable`, `not_found` (HTTP 404) or `invalid_response`.
* `stored_required`: the stored R, or `null` for a record written by an older
  release; then `prepare_time_host_required` (when the record holds it, even
  empty) and `current_host_required` apply.
* `granted`: the recorded granted scopes after the exchange; `ceiling`: the
  current C.

Its `expect` members are `code` (`null` for success), `scopes` (as for
`scope_decisions`), `login_ended` (present exactly when `code` is not `null`)
and `grant_persisted` (whether the grant was written).

A `token_errors` vector has `id`, optional `requires`, `input` and `expect`,
for a token-endpoint error outside `complete` ([renewal errors](#renewal-errors)).
Its `input` members are `grant_type` (`refresh`, a PKCE refresh, or
`client_credentials`), `client_source` (as for `completions`; `cimd` only with
`refresh`) and `error` (a value of `completions` `exchange` other than `ok`).
Its `expect` members are `code` and, present exactly for a client-credentials
DCR `invalid_client`, `reregistrations` (the registrations sent before the
repeated `invalid_client`). A `token_errors` outcome never carries scopes.

Native APIs and test names may differ; the semantic vectors and observed
behaviors may not.
