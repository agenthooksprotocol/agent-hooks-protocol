# Messages and events

## Common message envelope
### Intercept request
```json
               {
  "jsonrpc": "2.0",
  "id": "evt_01JQ8Z2Y6YR0H8N7Q2M3X4V5W6",
  "method": "hooks/intercept",
  "params": {
    "protocolVersion": "0.1",
    "event": {
      "id": "evt_01JQ8Z2Y6YR0H8N7Q2M3X4V5W6",
      "source": "urn:uuid:5b7de29e-a9e0-41a8-bf26-d94b05f0656d",
      "type": "tool.before",
      "time": "2026-08-24T08:51:14Z",
      "session": {
        "id": "sess_123",
        "cwd": "/repo",
        "workspaceRoots": ["/repo"]
      },
      "tool": {
        "callId": "call_456",
        "name": "Bash",
        "kind": "shell",
        "input": {
          "command": "git push --force"
        }
      }
    },
    "capabilities": {
      "effects": ["deny"]
    }
  }
}
```
The JSON-RPC request `id` MUST equal `params.event.id`. A retry of the same event MUST reuse both values.
### Intercept response with no effect
```json
{
  "jsonrpc": "2.0",
  "id": "evt_01JQ8Z2Y6YR0H8N7Q2M3X4V5W6",
  "result": {
    "protocolVersion": "0.1",
    "effects": []
  }
}
```
An empty effect list means only that this interceptor requests no change. It does not grant permission, override another interceptor, or bypass the harness's permission system.
### Intercept response denying the operation
```json
{
  "jsonrpc": "2.0",
  "id": "evt_01JQ8Z2Y6YR0H8N7Q2M3X4V5W6",
  "result": {
    "protocolVersion": "0.1",
    "effects": [
      {
        "type": "deny",
        "reason": "Force pushes are prohibited by organization policy.",
        "code": "com.example.policy.force_push"
      }
    ]
  }
}
```
### Observe notification
```json
{
  "jsonrpc": "2.0",
  "method": "hooks/observe",
  "params": {
    "protocolVersion": "0.1",
    "event": {
      "id": "evt_01JQ8Z7BEQW0X31TVPN5AZ1YJ6",
      "source": "urn:uuid:5b7de29e-a9e0-41a8-bf26-d94b05f0656d",
      "type": "tool.after",
      "time": "2026-08-24T08:51:15Z",
      "session": {
        "id": "sess_123"
      },
      "tool": {
        "callId": "call_456",
        "name": "Bash",
        "kind": "shell",
        "input": {
          "command": "git status"
        },
        "output": {
          "exitCode": 0,
          "stdout": "On branch main\n",
          "stderr": ""
        }
      }
    }
  }
}
```
A notification has no JSON-RPC `id` and MUST NOT receive a JSON-RPC response.
## Event model
### Common event fields
<table>
<tr>
<td>Field</td>
<td>Type</td>
<td>Requirement</td>
<td>Semantics</td>
</tr>
<tr>
<td>`id`</td>
<td>string</td>
<td>REQUIRED</td>
<td>Globally unique event identifier. Stable across retries.</td>
</tr>
<tr>
<td>`source`</td>
<td>string</td>
<td>REQUIRED</td>
<td>Stable, non-secret URI identifying the harness installation or runtime instance.</td>
</tr>
<tr>
<td>`type`</td>
<td>string</td>
<td>REQUIRED</td>
<td>Event type defined by this specification.</td>
</tr>
<tr>
<td>`time`</td>
<td>RFC 3339 string</td>
<td>REQUIRED</td>
<td>Time at which the harness created the event.</td>
</tr>
<tr>
<td>`session`</td>
<td>object</td>
<td>REQUIRED</td>
<td>Session identity and optional execution context.</td>
</tr>
<tr>
<td>`tool`</td>
<td>object</td>
<td>Tool events only</td>
<td>Tool-call identity and event-specific data.</td>
</tr>
<tr>
<td>`native`</td>
<td>object</td>
<td>OPTIONAL</td>
<td>Provider-specific fidelity payload. Disabled by default.</td>
</tr>
<tr>
<td>`extensions`</td>
<td>object</td>
<td>OPTIONAL</td>
<td>Namespaced extension data.</td>
</tr>
</table>
`id` MUST be unique across events produced by a harness. UUIDv4, UUIDv7, and suitably generated ULIDs are acceptable examples; the protocol does not require a specific algorithm.
`source` SHOULD remain stable for the life of the harness installation. It MUST NOT contain access tokens, user secrets, raw prompts, or other sensitive data.
### Session object
<table>
<tr>
<td>Field</td>
<td>Type</td>
<td>Requirement</td>
<td>Semantics</td>
</tr>
<tr>
<td>`id`</td>
<td>string</td>
<td>REQUIRED</td>
<td>Stable session identifier.</td>
</tr>
<tr>
<td>`cwd`</td>
<td>string</td>
<td>OPTIONAL</td>
<td>Current working directory at event creation.</td>
</tr>
<tr>
<td>`workspaceRoots`</td>
<td>string array</td>
<td>OPTIONAL</td>
<td>Workspace roots visible to the harness.</td>
</tr>
<tr>
<td>`model`</td>
<td>string</td>
<td>OPTIONAL</td>
<td>Harness-reported model identifier.</td>
</tr>
<tr>
<td>`agent`</td>
<td>object</td>
<td>OPTIONAL</td>
<td>Current subagent identity, if applicable.</td>
</tr>
</table>
Paths and model identifiers may be sensitive. A harness MUST permit implementations or administrators to omit optional session fields.
If present, `agent` has a required `id` and an optional provider-defined `type`.
### Tool object
<table>
<tr>
<td>Field</td>
<td>Type</td>
<td>Requirement</td>
<td>Semantics</td>
</tr>
<tr>
<td>`callId`</td>
<td>string</td>
<td>REQUIRED</td>
<td>Stable identifier for this invocation within the session.</td>
</tr>
<tr>
<td>`name`</td>
<td>string</td>
<td>REQUIRED</td>
<td>Tool name as exposed by the harness, preserved verbatim.</td>
</tr>
<tr>
<td>`kind`</td>
<td>string</td>
<td>REQUIRED</td>
<td>Coarse portable classification defined below.</td>
</tr>
<tr>
<td>`input`</td>
<td>object</td>
<td>`tool.before`, `tool.after`, `tool.error`</td>
<td>Parsed tool input. MUST be a JSON object.</td>
</tr>
<tr>
<td>`output`</td>
<td>any JSON value</td>
<td>`tool.after` only</td>
<td>Successful tool output, subject to redaction and size limits.</td>
</tr>
<tr>
<td>`error`</td>
<td>object</td>
<td>`tool.error` only</td>
<td>Tool failure information.</td>
</tr>
<tr>
<td>`mcp`</td>
<td>object</td>
<td>OPTIONAL</td>
<td>MCP server and tool identity when known.</td>
</tr>
</table>
`callId` MUST remain identical across the `tool.before`, `tool.after`, and `tool.error` events for one tool invocation. If a native harness does not supply an ID, an adapter MUST synthesize a stable ID. A deterministic hash of session identity, turn identity, tool name, and input is one acceptable strategy.
The v0.1 `kind` values are:
- `shell`
- `file_read`
- `file_write`
- `file_edit`
- `search`
- `fetch`
- `task`
- `mcp`
- `other`
`kind` is deliberately coarse. It supports portable broad policy, such as denying all shell execution, without claiming that arbitrary tool input schemas are portable. `name` and `input` retain the harness representation. A future tool-projection profile may standardize selected inputs, but that work is out of scope here.
If present, `mcp` MAY contain `server`, `tool`, and transport metadata. Only `server` and `tool` are candidates for future portable semantics; transport metadata is informational.
### Native payload
The optional `native` object has this shape:
```json
{
  "provider": "claude-code",
  "eventName": "PreToolUse",
  "payload": {}
}
```
A harness MUST omit `native` unless native-payload delivery is enabled by local policy. A portable backend MUST function when `native` is absent. Native fields MUST NOT change the semantics of a core effect.
### Extensions

<a id="AHP-EXT-001"></a>
**AHP-EXT-001 — MUST.** Extension keys use reverse-DNS names and core behavior does not depend on an extension unless that dependency is explicitly declared.

Extension keys MUST use reverse-DNS namespacing, such as `com.example.policy` or `org.w3c.trace_context`. Unnamespaced extension keys are invalid.
Extension values MAY contain any JSON value. Core implementations MUST preserve extensions when proxying an otherwise unchanged event, but they MUST NOT interpret unknown extensions.
A Trace Context extension might look like:
```json
{
  "extensions": {
    "org.w3c.trace_context": {
      "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
      "tracestate": "congo=t61rcWkgMzE"
    }
  }
}
```
This extension provides correlation only. Its presence does not make OpenTelemetry part of AHP conformance.
