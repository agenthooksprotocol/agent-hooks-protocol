# Lifecycle observation

## Event lifecycle
### `tool.before`

<a id="AHP-TB-001"></a>
**AHP-TB-001 — MUST.** A `tool.before` event describes a tool call before execution and includes the common event, session, and tool fields required by this protocol revision.

`tool.before` is the only interceptable event in this protocol revision.
The harness MUST create it after the tool name and input are finalized for execution but before the tool causes any external side effect. AHP interception SHOULD occur before the harness displays its own permission prompt so an operation denied by organization policy does not generate an unnecessary prompt.
A no-effect result means the harness continues its normal authorization and execution flow. It MUST NOT bypass built-in permissions, sandboxing, or user confirmation.
If any interceptor denies the event, the harness MUST NOT execute the tool. The harness SHOULD present the denial reason to the user or agent unless local policy marks backend reasons as sensitive.
### `tool.after`
`tool.after` is an optional observation event emitted after successful tool completion. It MUST reuse the `callId` from `tool.before`.
The output may be large or sensitive. Harnesses SHOULD support truncation, redaction, or omission and SHOULD indicate those transformations through a namespaced extension.
### `tool.error`
`tool.error` is an optional observation event emitted when an attempted tool execution fails. It MUST NOT be emitted merely because an AHP interceptor denied the call.
The `error` object MUST contain `message` and MAY contain `code`, `category`, and provider-specific extension data.
### `session.start`
`session.start` is an optional observation event emitted when the harness creates a session. It SHOULD be the first AHP event for the session when supported.
### `session.end`
`session.end` is an optional observation event emitted when the harness knows a session has ended. Delivery is best effort because a harness crash or forced process termination may prevent it.
The event MAY contain an `outcome` value of `completed`, `cancelled`, `error`, or `unknown`.
