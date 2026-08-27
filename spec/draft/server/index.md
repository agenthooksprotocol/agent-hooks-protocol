# Server features

The Backend is the AHP protocol server, and the Harness is the AHP protocol client. This section groups the methods that a Backend exposes to a Harness; the AHP role names remain authoritative in protocol prose.

This protocol revision defines these Server Features:

- [Tool interception](tool-interception.md), which defines Backend decisions returned through `hooks/intercept`.
- [Lifecycle observation](lifecycle-observation.md), which defines lifecycle events accepted by a Backend through `hooks/observe`.

Shared message, event, capability, transport, and other protocol mechanics are defined by the [Base Protocol](../base/index.md).
