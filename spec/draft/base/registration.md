# Portable registration

## Portable registration
### Document shape

<a id="AHP-REG-001"></a>
**AHP-REG-001 — MUST.** A portable registration document declares the selected snapshot as its `protocolVersion` and contains an ordered, non-empty `hooks` array.

A portable registration document is a JSON object with an ordered `hooks` array.
```json
{
  "protocolVersion": "draft",
  "hooks": [
    {
      "id": "com.example.policy",
      "transport": {
        "type": "http",
        "url": "https://policy.example.com/agent-hooks"
      },
      "authentication": {
        "type": "bearer",
        "tokenEnv": "AHP_POLICY_TOKEN"
      },
      "subscriptions": [
        {
          "events": ["tool.before"],
          "mode": "intercept",
          "timeoutMs": 750,
          "failurePolicy": "fail-closed",
          "content": {"default": "metadata"}
        }
      ]
    },
    {
      "id": "com.example.local-review",
      "transport": {
        "type": "stdio",
        "command": "/usr/local/bin/local-review",
        "args": ["serve"],
        "lifecycle": "persistent"
      },
      "subscriptions": [
        {
          "events": ["tool.before"],
          "mode": "intercept",
          "timeoutMs": 500,
          "failurePolicy": "fail-open",
          "content": {"default": "metadata"}
        }
      ]
    }
  ]
}
```
### Backend fields
<table>
<tr>
<td>Field</td>
<td>Requirement</td>
<td>Semantics</td>
</tr>
<tr>
<td>`id`</td>
<td>REQUIRED</td>
<td>Unique reverse-DNS backend identifier within the document.</td>
</tr>
<tr>
<td>`transport`</td>
<td>REQUIRED</td>
<td>Exactly one supported transport configuration.</td>
</tr>
<tr>
<td>`authentication`</td>
<td>Optional endpoint authentication</td>
<td>Credential reference; never a literal credential.</td>
</tr>
<tr>
<td>`subscriptions`</td>
<td>REQUIRED</td>
<td>Non-empty array of event subscriptions.</td>
</tr>
</table>
### Subscription fields
<table>
<tr>
<td>Field</td>
<td>Requirement</td>
<td>Semantics</td>
</tr>
<tr>
<td>`events`</td>
<td>REQUIRED</td>
<td>Non-empty array of exact event names, whole-family wildcards such as `tool.*`, or `*`. No arbitrary patterns.</td>
</tr>
<tr>
<td>`mode`</td>
<td>REQUIRED</td>
<td>`intercept` or `observe`.</td>
</tr>
<tr>
<td>`timeoutMs`</td>
<td>Intercept only</td>
<td>Required positive integer deadline.</td>
</tr>
<tr>
<td>`failurePolicy`</td>
<td>Intercept only</td>
<td>Required `fail-open` or `fail-closed`.</td>
</tr>
<tr>
<td>`content`</td>
<td>REQUIRED</td>
<td>Flat selection object with a required `default` and optional category-name keys, each set to `body`, `metadata`, or `omit`. See [content upload](../content-upload.md).</td>
</tr>
<tr>
<td>`includeNative`</td>
<td>OPTIONAL</td>
<td>Boolean; defaults to `false`.</td>
</tr>
</table>
An `intercept` subscription MUST select only events the harness advertises as interceptable, including when expanding wildcards. An `observe` subscription MUST NOT include `timeoutMs` or `failurePolicy`.
### Subscription dispatch

<a id="AHP-REG-002"></a>
**AHP-REG-002 — MUST.** A harness sends an event to a backend only when that backend has a subscription whose `events` array matches the event name exactly or through a supported wildcard and whose `mode` matches the delivery method, except for best-effort observation of uncalled intercept subscriptions after short-circuit settlement.

For normal dispatch, `hooks/intercept` matches `intercept` mode and `hooks/observe` matches `observe` mode. After short-circuit settlement, remaining uncalled matching intercept subscriptions receive best-effort `hooks/observe` as defined in [observation delivery](../observation-disposition.md). Exact selectors match only the named event; family wildcards match that event family, and `*` matches all supported events for the mode. Intercept wildcards select only advertised interceptable boundaries. There is no arbitrary matcher language or separate `enabled` subscription state. Absent a matching subscription or that short-circuit observation rule, the harness MUST NOT send that event to the backend.
### stdio transport fields
A stdio transport contains:
- `type`: exact value `stdio`
- `command`: executable path or name
- `args`: optional string array
- `lifecycle`: `persistent` or `per_event`
- `cwd`: optional backend working directory
- `env`: optional object of literal string environment variables for the backend process; see [process environment](transports/stdio.md#process-environment)
### HTTP transport fields
An HTTP transport contains:
- `type`: exact value `http`
- `url`: absolute endpoint URL
Authentication supports the configured endpoint bindings in [capabilities and authentication](../capability-auth.md#authentication-bindings). Bearer authentication uses exactly one of `tokenEnv` or `tokenRef`; credentials are never literal values. Upload authentication is configured independently.
### Native harness configuration
A harness MAY translate this registration model into its native configuration format. It may still claim protocol conformance if the resulting order, subscriptions, timeout, failure, transport, and credential semantics are equivalent.

### Forward compatibility and local identity

Subscriptions and subscription identifiers are harness-local configuration, not
wire identity. Receivers MUST ignore unknown registration/configuration fields,
MUST validate recognized fields, and MAY warn about ignored fields. Unknown fields
do not change dispatch, authorization, or supported effect/operation semantics.

### Endpoint discovery example

This HTTP backend deliberately omits an explicit identity binding. The client
attempts the endpoint and, if challenged, uses standard OAuth protected-resource
discovery subject to its own trust and identity policy. The upload endpoint does
so independently. Neither absence authorizes credential inheritance.

```json
{
  "protocolVersion": "draft",
  "hooks": [{
    "id": "org.example.policy",
    "transport": {"type": "http", "url": "https://hooks.example/events"},
    "subscriptions": [{
      "id": "tool-policy",
      "events": ["tool.before"],
      "mode": "intercept",
      "timeoutMs": 1000,
      "failurePolicy": "fail-closed",
      "content": {"default": "metadata"},
      "upload": {"endpoint": "https://uploads.example/bytes", "timeoutMs": 5000, "maxBytes": 1048576}
    }]
  }]
}
```

For a static upload secret, explicitly set `upload.auth` to
`{"type": "bearer", "tokenRef": "secrets/policy-upload"}`. This deployment-managed
reference is not a URL or a secret value. Resolution failure MUST NOT switch to
OAuth; an event secret is not a substitute. An explicit OAuth preset can constrain
issuer, resource, client, flow, and scopes without requiring every registration to
predeclare OAuth before encountering an endpoint challenge.

### HTTP registration headers

<a id="AHP-REG-003"></a>
**AHP-REG-003 — MUST.** HTTP registration headers MUST follow the configuration, resolution, validation, request construction, endpoint isolation, and failure rules in this section.


```json
{
  "type": "http",
  "url": "https://policy.example/hooks",
  "headers": {
    "X-Tenant-Id": "engineering",
    "X-Api-Key": { "valueEnv": "AHP_POLICY_API_KEY" },
    "X-Gateway-Token": { "valueRef": "secrets/policy-gateway" }
  }
}
```

This object is a backend's `transport`, not a complete registration. `headers` is
optional; omission and `{}` preserve existing behavior. Each map entry is either:

- A string used verbatim for **non-secret** metadata. No environment expansion,
  `${...}` substitution, shell evaluation, or whitespace trimming occurs.
- An object containing exactly one recognized source: `valueEnv` (environment
  variable name) or `valueRef` (nonempty opaque secret-provider reference).

Literal credentials are prohibited, including API keys and cookies; SDKs cannot
reliably infer whether an arbitrary string is secret, so documentation and harness
configuration policy must enforce this distinction. The source model mirrors
`authentication.tokenEnv` / `authentication.tokenRef`, but resolves a complete header value rather
than automatically adding a `Bearer ` prefix. Bearer tokens belong in `authentication`,
not in this map. Header configuration neither disables nor replaces existing
OAuth discovery when `authentication` is omitted.

A harness-owned resolver receives backend identity, exact destination, event
purpose, source name/reference, and the dispatch deadline/cancellation context.
It returns a string, not a new authentication identity. Resolve once per dispatch
and reuse the resolved set on any already-permitted challenged retry; resolve
again for the next dispatch so rotation is possible. A resolver may cache under
its own explicit policy but must keep bindings endpoint-scoped. Environment
lookup uses the harness environment, not the stdio child `env` setting.

Missing, empty, unsupported, or failed referenced values fail the dispatch before
an event request is sent. No fallback to a literal, another secret, or a request
with fewer headers is allowed. Empty literal metadata strings are permitted.
Resolution is cancellable and included in the existing operation deadline.

### Names, values, and precedence

1. Validate names as nonempty ASCII HTTP field-name tokens, using
   `^[!#$%&'*+.^_\x60|~0-9A-Za-z-]+$`. Names are case-insensitive. Reject two map
   keys that differ only by ASCII case, even if their values match; never join them.
   Registration parsers should reject duplicate identical JSON keys when possible.
2. Values must contain only ASCII space through tilde (`U+0020`–`U+007E`), including
   values returned by resolvers. Reject CR, LF, NUL, tabs, DEL, non-ASCII characters,
   arrays, numbers, and null. This intentionally portable subset avoids differing
   library encodings; applications can use an agreed ASCII encoding for metadata.
3. Reject configuration of `Authorization`, `Proxy-*`, `Cookie`, `Set-Cookie`,
   `Host`, `Content-Type`, `Accept`, `Content-Length`, `Content-Encoding`,
   `Connection`, `Keep-Alive`, `Transfer-Encoding`, `TE`, `Trailer`, `Upgrade`,
   `Expect`, `Origin`, `Referer`, `Sec-*`, and `AHP-*`, case-insensitively. These are
   authentication, browser/security, framing, or reserved protocol concerns.
   `*` denotes a prefix, not a valid configured wildcard name. Reject reserved
   names even when no auth provider or protocol default would currently set them.
4. Build each request from validated configured metadata, then apply mandatory
   protocol and authentication headers through a case-insensitive header API.
   Authentication exclusively supplies `Authorization`. Never concatenate a
   configured value with a transport-owned value.
5. For SDK APIs that also accept programmatic headers/default HTTP clients, merge
   non-reserved defaults first and registration values second, comparing names
   case-insensitively; registration wins. Reject ambiguous duplicates within either
   input. Apply the same reserved-name/value rules to user-supplied additional
   headers on the registration path. Reject a conflict rather than let an injected
   client silently replace auth or framing fields. Low-level transport APIs used
   outside registration are not retroactively redefined by this registration contract.

No arbitrary per-header size is standardized here: existing transport/harness
limits still apply, and implementations must enforce finite aggregate header
limits. A limit failure is an explicit delivery error, not truncation.

### Endpoint isolation and failures

Configured headers apply only to event POSTs (interceptions and observations) to
the configured HTTP transport URL, including auth retries that are already safe
under AHP's replay rules. They do not apply to OAuth/protected-resource discovery,
issuer metadata, token exchange, content uploads, or another backend, even at the
same origin. Do not attach them to a shared HTTP client's global defaults.

With a nonempty header map, redirects are not automatically followed, including
same-origin redirects. A redirect is an operational delivery failure; configure
the final trusted URL explicitly. This stricter-than-origin scope prevents secret
leakage to another path or downgrade and avoids replay of event bodies. Harness
extensions must not weaken this portable behavior. No credential or metadata is
forwarded to a redirect target. With no headers, existing redirect policy remains
unchanged. Upload configuration and authentication remain independent.

Static invalid configuration fails backend initialization/registration before
network I/O. Resolution/runtime validation failures prevent that event request;
existing interception failure policy decides whether the harness operation
continues or is denied. Observations report their existing best-effort delivery
error. Neither outcome authorizes sending a partial or unauthenticated fallback
request. Header failures create no additional retry mechanism.

Diagnostics may identify a backend and offending header name, but must not include
values, secret contents, or the full header map. Treat even literal metadata as
potentially sensitive in logging/tracing; redact all configured values. Real secret
references never enter event payloads, error bodies, or generated fixtures;
conformance fixtures use synthetic reference names only.


Unknown fields in source objects MUST be ignored, but exactly one recognized
source is required. Header-map keys are field names, not ignorable extensions.
Runtime validation MUST match full strings, including rejecting final newlines.
Older implementations may ignore `headers`; schema acceptance is not feature
negotiation. Deployments requiring headers MUST select a supporting implementation.
