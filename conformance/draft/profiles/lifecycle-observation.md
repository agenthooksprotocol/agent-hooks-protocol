# Lifecycle Observation profile

**Status: Working Draft — `0.1.0-draft.1`.** This optional profile is described independently of implementation language and runtime.

An implementation claims the profile only for event types it supports. It emits observations as `hooks/observe` JSON-RPC notifications with no request ID. A receiver sends no JSON-RPC response. Delivery is non-blocking and best effort.

Supported event types are `tool.before`, `tool.after`, `tool.error`, `session.start`, and `session.end`. Tool completion events reuse the original `callId`; an interception denial does not produce `tool.error`.
