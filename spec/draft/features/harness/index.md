# Harness

A harness is the agent runtime that is about to perform an operation. It acts as the AHP protocol client. “Client” describes message direction; **harness** is the AHP role and domain term.

This page is an informative guide for harness implementers. The linked canonical sections define protocol semantics and conformance requirements.

**Role responsibilities**

- Construct protocol messages and canonical events, then preserve event, session, and tool-call identity as defined by [Messages and events](../events.md).
- Initiate tool-call decisions, advertise only enforceable effects, validate backend responses, and apply accepted effects as defined by [Tool interception](../tool-interception.md).
- Emit supported one-way events without making observer delivery part of tool execution as defined by [Lifecycle observation](../lifecycle-observation.md).
- Own backend ordering, deadlines, failure policy, host authorization, and effect enforcement under the [Basic protocol](../../basic/index.md).
- When translating a native hook system, preserve the harness role while following the [Compatibility-adapter requirements](../adapters.md).

Start with the [harness conformance responsibilities](../../basic/index.md#61-base-protocol-profile), then follow the feature and transport sections for every profile and binding the implementation claims.
