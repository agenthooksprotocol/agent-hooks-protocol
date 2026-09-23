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
