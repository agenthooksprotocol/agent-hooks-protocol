# Base Protocol profile

**Status: Working Draft — `0.1.0-draft.1`.** For each snapshot, the canonical requirements are those selected by that snapshot's conformance manifest; this profile is a language-neutral test description.

## Claim inputs

A conformance claim records:

- role: `harness`, `backend`, or `both`;
- transport binding: `stdio`, `http`, or both;
- at least one capability profile.

## Observable checks

1. Every emitted protocol message is UTF-8 JSON-RPC 2.0.
2. Requests, notifications, and successful results contain `protocolVersion: "0.1"` at the specified payload location.
3. Unknown object fields do not invalidate an otherwise valid message. Unknown event, effect, and enum values remain unsupported semantics.
4. Extension keys use reverse-DNS names.
5. A harness validates portable registration before use and preserves event identity across retries.
6. A backend returns a defined JSON-RPC error for unsupported request semantics and sends no response to a notification.
7. No successful AHP result weakens host authorization, permission, approval, or sandbox controls.

A Base Protocol claim is incomplete without a capability-profile claim and at least one transport-binding claim.
