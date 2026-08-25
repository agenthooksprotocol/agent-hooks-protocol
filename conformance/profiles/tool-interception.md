# Tool Interception profile

**Status: Working Draft — `0.1.0-draft.1`.** This is a language-neutral description for the `tool.before` and `deny` behavior defined by the canonical Working Draft.

## Harness observations

Given a valid, enabled `tool.before` intercept subscription that covers a pending tool call:

1. The harness sends one `hooks/intercept` request after tool name and input are final and before tool side effects.
2. The JSON-RPC request ID equals the event ID. Retries preserve event, session, call, and request identities and event content.
3. Capabilities advertise `deny` for the event.
4. Matching interceptors execute serially in deterministic registration order under their original deadline.
5. A valid empty effect list continues normal host authorization without granting permission.
6. One valid advertised `deny` stops evaluation and tool execution.
7. Malformed output, unavailable backends, unsupported semantics, and timeouts apply the configured `fail-open` or `fail-closed` policy.

## Backend observations

Given a syntactically valid `hooks/intercept` request for protocol `0.1` and `tool.before`, the backend returns exactly one successful response containing either:

- `effects: []`; or
- one advertised `deny` effect with a non-empty reason.

The backend does not return an unadvertised effect or multiple effects.
