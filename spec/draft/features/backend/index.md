# Backend

A backend is the external policy, security, approval, or runtime-middleware component that receives AHP messages. It acts as the AHP protocol server. “Server” describes message direction; **backend** is the AHP role and domain term.

This page is an informative guide for backend implementers. The linked canonical sections define protocol semantics and conformance requirements.

**Role responsibilities**

- Receive and interpret protocol messages and canonical events as defined by [Messages and events](../events.md).
- Evaluate tool-call decisions and return only valid, advertised effects as defined by [Tool interception](../tool-interception.md).
- Consume supported one-way events without responding to notifications as defined by [Lifecycle observation](../lifecycle-observation.md).
- Apply version, unknown-field, duplicate-delivery, error, and transport behavior under the [Basic protocol](../../basic/index.md).
- Remain portable across conforming harnesses; provider-specific translation belongs in an adapter governed by the [Compatibility-adapter requirements](../adapters.md).

Start with the [backend conformance responsibilities](../../basic/index.md#61-base-protocol-profile), then follow the feature and transport sections for every profile and binding the implementation claims.
