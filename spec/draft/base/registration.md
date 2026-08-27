# Portable registration

## Portable registration
### Document shape

<a id="AHP-REG-001"></a>
**AHP-REG-001 — MUST.** A portable registration document declares protocol version `0.1` and an ordered, non-empty `hooks` array.

A portable registration document is a JSON object with an ordered `hooks` array.
```json
{
  "protocolVersion": "0.1",
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
          "failurePolicy": "fail-closed"
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
          "failurePolicy": "fail-open"
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
<td>HTTP bearer only</td>
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
<td>Non-empty array of exact event names. No matcher language in v0.1.</td>
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
<td>`includeNative`</td>
<td>OPTIONAL</td>
<td>Boolean; defaults to `false`.</td>
</tr>
</table>
An `intercept` subscription MUST contain only `tool.before` in v0.1. An `observe` subscription MUST NOT include `timeoutMs` or `failurePolicy`.
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
A bearer authentication object contains:
- `type`: exact value `bearer`
- `tokenEnv`: environment variable containing the token
Implementations MAY support additional local secret-reference forms, but portable documents cannot assume them.
### Native harness configuration
A harness MAY translate this registration model into its native configuration format. It may still claim protocol conformance if the resulting order, subscriptions, timeout, failure, transport, and credential semantics are equivalent.
