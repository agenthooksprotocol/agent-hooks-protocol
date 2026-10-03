# Lifecycle observation

## Event lifecycle
### `tool.before`

<a id="AHP-TB-001"></a>
**AHP-TB-001 — MUST.** A `tool.before` event describes a tool call before execution and includes the common event, session, and tool fields required by this protocol revision.

`tool.before` is the minimum tool-interception boundary, not the only
interceptable event. The current catalogue and event-specific capability schemas
define other interception boundaries and their available effects.
The harness MUST create it after the tool name and input are finalized for execution but before the tool causes any external side effect. AHP interception SHOULD occur before the harness displays its own permission prompt so an operation denied by organization policy does not generate an unnecessary prompt.
A no-effect result means the harness continues its normal authorization and execution flow. It MUST NOT bypass built-in permissions, sandboxing, or user confirmation.
If any interceptor denies the event, the harness MUST NOT execute the tool. The harness SHOULD present the denial reason to the user or agent unless local policy marks backend reasons as sensitive.
### `tool.after`
`tool.after` records the resolved invocation outcome, including `ok`, `error`,
`denied`, `cancelled`, and `timeout`. It reuses the invocation's `call.id` from
`tool.before`, not a `tool.callId` field. The event requires `call`, `tool`,
`path`, `outcome`, `execution`, and `items` alongside the common envelope.
Execution distinguishes an executed call from a skipped one; denial requires
skipped execution with reason `policy`. A supplied result retains its ordinary
outcome and uses skipped execution with reason `supplied_result`. The supplying
subscription is tracked only in harness-local state.

Result content uses `items` and the content selection/upload contract, not
`tool.output`. An error outcome requires event-level `error` with `class` and
`message`; other outcomes forbid it. `tool.error` is not a standard event.
See [Execution payloads](../execution-payloads.md) for the complete wire rules.
`tool.after` also supports interception with its event-specific capability grants;
observing this boundary does not grant effect authority.

### `session.start`
`session.start` describes session creation. Its required payload includes
`session`, `trigger`, `harness`, `permissionMode`, `manifest`, and `items`.
Capability discovery is also available independently of session start.

### `session.end`
`session.end` describes a known session ending and requires `session`, `outcome`,
and `reason`. Outcome is `completed`, `cancelled`, `error`, or `unknown`.
Delivery cannot be guaranteed after a crash or forced process termination.

## Observation delivery

These lifecycle names identify boundaries, not a separate observation-only event
catalogue. `hooks/observe` carries `protocolVersion` and the
settled effective event without a JSON-RPC `id`. It is one-way and best effort;
observers cannot return effects or reopen decisions. An observed `tool.before`
remains a proposal, not evidence of execution. Follow
[Observation delivery after settlement](../observation-disposition.md) for
short-circuit delivery, content preparation, and interruption behavior.
