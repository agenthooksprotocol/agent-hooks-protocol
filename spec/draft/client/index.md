# Client features

The Harness is the AHP protocol client, and the Backend is the AHP protocol server. This section is reserved for features exposed by a Harness to a Backend; the AHP role names remain authoritative in protocol prose.

This protocol revision defines no client-offered protocol feature or Backend-initiated method. Harness responsibilities for messages sent to Backend Server Features are defined by the [Base Protocol](../base/index.md) and the relevant [Server Features](../server/index.md), rather than being restated here as a fabricated client feature.

The only client-specific material in this protocol revision is:

- [Compatibility-adapter requirements](adapters.md), for translating a native Harness hook system into AHP.
