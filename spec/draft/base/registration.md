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
<td>OPTIONAL</td>
<td>Non-empty array of explicit event subscriptions. Omission selects server-driven configuration discovery; an explicit empty array is invalid.</td>
</tr>
</table>
See [server-driven configuration](../server-driven-configuration.md) for backend-provided defaults, explicit client precedence, and initialization admission.

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
- `headers`: optional configured HTTP fields shared by events and backend uploads; see [HTTP registration headers](#AHP-REG-003)
Authentication supports the configured endpoint bindings in [capabilities and authentication](../capability-auth.md#authentication-bindings). Bearer authentication bindings use exactly one of `tokenEnv` or `tokenRef`; those bindings never contain literal credentials. Explicit `headers` may contain literals and override authentication headers. Upload authentication bindings are configured independently; backend headers apply to uploads as well.
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

`transport.headers` is an optional map on an HTTP backend transport. Omission and
an empty map preserve existing behavior. Each value is a literal string or an
object containing exactly one recognized source, `valueEnv` or `valueRef`.
`valueEnv` names a harness environment variable; `valueRef` is a nonempty opaque
secret reference. Unknown source-object fields are ignored. An object containing
neither recognized source or both is invalid.

```json
{
  "type": "http",
  "url": "https://policy.example/hooks",
  "headers": {
    "Authorization": "Bearer abc123",
    "Cookie": "session=example",
    "X-Api-Key": { "valueEnv": "AHP_API_KEY" },
    "X-Gateway-Token": { "valueRef": "example/gateway" }
  }
}
```

This is a transport fragment with synthetic example credentials. Literal strings,
including API keys, cookies, and Authorization values, are permitted and used
without interpolation or an added authentication scheme. References resolve the
complete header value, not a token to which `Bearer ` is automatically prefixed.
Implementations MUST reuse existing environment/secret helpers and resolvers where
applicable, including their deadline, cancellation, and redacted-error handling;
this field does not introduce a separate secret-management framework. Failed or
unavailable referenced values MUST fail the operation rather than send a request
with missing headers or fall back to another credential. Resolution uses the
existing operation budget; it MUST NOT start a fresh deadline.

#### Validation and precedence

Names MUST be valid HTTP field names. Every valid field name is permitted,
including `Authorization`, `Cookie`, `Origin`, and protocol/default header names;
there is no AHP header denylist. Values MUST be strings accepted by the native
HTTP library's field-value validation. Implementations MUST reject CR, LF, and NUL
injection, but MUST NOT impose a printable-ASCII-only restriction: legal non-ASCII
values are accepted where the native library supports them. Existing native
transport limits apply; this feature adds no header-size policy.

Names are compared case-insensitively. When configured properties differ only in
case, the last property in configuration iteration order MUST win; values MUST
NOT be joined or rejected as ambiguous. Apply this deduplication before resolving
sources so that only the winning configured value is used. For serialized JSON,
iteration order follows the member order presented by the configuration; parsers
that discard order MUST preserve it for this map to implement this rule.

Build transport, client-default, protocol, and authentication headers first, then
apply the resolved configured headers LAST through a case-insensitive API.
Configured values MUST override earlier values, including `Authorization` and
`Content-Type`. Native HTTP rejection of an invalid or unsupported request remains
an operation failure, not a reason to silently omit configured headers.

The presence of a configured `Authorization` name, in any casing and with any
source, MUST suppress OAuth discovery, acquisition, challenge handling, and retry
flows for that operation, even when an authentication configuration also exists.
Resolve the explicit header normally; failure MUST NOT fall back to OAuth or
another identity. A challenge to this explicit value is handled as an operation
failure, not an invitation to acquire or retry with another token.

#### Uploads, isolation, and existing transport behavior

The same backend header configuration MUST apply to interception/observation
requests AND that backend's content uploads. Upload operations MUST resolve/reuse
the same configuration through the existing secret helpers and upload operation
budget, then merge configured values LAST over upload defaults and authentication.
Configured `Authorization` suppresses upload OAuth discovery, acquisition,
challenge handling, and retry flows as well. This explicit backend-wide
configuration is distinct from implicitly reusing event authentication tokens.

Header configuration MUST NOT change pre-feature redirect behavior or native
sensitive-header handling. It neither disables all redirects nor authorizes
blanket forwarding to redirect targets. Existing event and upload redirect
policies continue to apply.

Configured headers MUST NOT be installed as global defaults on unrelated clients
or sent to discovery/token endpoints or other backends. Existing failure policies,
observation delivery errors, deadlines, cancellation, and diagnostic redaction
apply. Header values, including literal credentials, MUST NOT leak through errors,
logs, event payloads, or metadata. Schema acceptance validates configuration shape,
not native HTTP support or OAuth/upload behavior. Older implementations may ignore
`headers`; deployments requiring it must select supporting implementations.
