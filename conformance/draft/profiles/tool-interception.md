# Tool Interception profile

**Status: Working Draft — `draft`.** This is a language-neutral description for the `tool.before` and `deny` behavior defined by the canonical specification.

## Harness observations

Given a valid `tool.before` intercept subscription that exactly matches a pending tool call's event name and delivery mode:

1. The harness sends one `hooks/intercept` request after tool name and input are final and before tool side effects.
2. The JSON-RPC request ID equals the event ID. Retries preserve event, session, call, and request identities and event content.
3. The request contains `capabilities.effects`, including `deny` to declare that the harness accepts and enforces a valid denial for the event.
4. Matching interceptors execute serially in deterministic registration order under their original deadline.
5. A valid empty effect list continues normal host authorization without granting permission.
6. One valid advertised `deny` stops evaluation and tool execution.
7. Malformed output, unavailable backends, unsupported semantics, and timeouts apply the configured `fail-open` or `fail-closed` policy.

Without a matching `tool.before` intercept subscription, the harness sends no AHP decision request and continues its normal authorization flow.

## Backend observations

Given a syntactically valid `hooks/intercept` request for protocol `draft` and `tool.before`, the backend returns exactly one successful response containing either:

- `effects: []`; or
- one advertised `deny` effect with a non-empty reason.

The backend does not return an unadvertised effect or multiple effects.
