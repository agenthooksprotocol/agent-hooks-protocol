# Server-driven configuration design

## Decision and scope

Backend configuration discovery supplies subscription defaults when the client has not supplied subscriptions. Explicit client subscriptions replace defaults completely and do not depend on discovery. Discovery failures and advertisements with no supported event/mode matches skip that backend. There is no required/optional subscription negotiation and no protocol-mandated application policy for continuing after a skip.

The normative contract is [server-driven configuration](../spec/draft/server-driven-configuration.md). This document records design rationale for the coordinated protocol and SDK implementation; it does not claim a released protocol version or a separate governance decision.

## Motivation

A client currently repeats a backend's desired events, modes, timeouts, content selection, and failure policy. A backend can publish those defaults instead. The client keeps connection identity, endpoint, authentication, headers, and an explicit subscription override. Harness capability discovery continues to describe harness support, not backend wishes.

## Shared configuration surface

One canonical Configuration model holds existing subscription shapes. SDKs use it for backend advertisements and programmatic client declarations; file-loaded JSON uses the same wire schema. Standard events have typed selectors, durations use language-native conventions, and metadata selection has a helper. Generated models, aliases, event catalogues, and semantic helpers come from canonical schemas and the shared generator rather than independent handwritten wire models.

TypeScript uses object arguments and literal unions. Python uses keyword-only constructors and timedelta. Go uses typed event constants, constructors, and time.Duration. Rust uses typed event selectors and fluent builders. Timeout, failure policy, and content choices remain explicit. Public examples do not import generated internals or assemble wire unions.

## Alternatives

- Required and optional hooks were considered. Requirements would need an explicit guarantee model for initialization coverage versus per-request effects. They are excluded: current capabilities may narrow for each invocation.
- Merging client and backend subscriptions was rejected. Full replacement gives deterministic precedence and preserves existing explicit configuration behavior.
- Reusing hooks/capabilities would conflate harness authority with backend defaults. Configuration discovery is a separate method.
- A distinct inactive registration state adds no delivery behavior. A backend with no applicable subscriptions is skipped.
- Periodic reload and a server acknowledgement of selected subscriptions are not required. Discovery is an initialization/admission operation; reinitialization can reload.

## Compatibility

Existing explicit registration continues to work against both old and new backends, including a new backend whose discovery fails. Only omitted subscriptions opt into discovery. Explicit empty subscriptions remain invalid. Old backends can return method-not-found, which skips a discovery-configured backend. The working draft gains new method/schema shapes; frozen snapshots are unchanged. This feature does not claim mixed-version support for omitted subscriptions on older clients.

## Security and operation

Adopting defaults may disclose more content than a metadata-only local configuration. Local disclosure and credential authorization boundaries still apply. Discovery cannot replace event connection/authentication settings, transfer event credentials to an upload destination, or grant per-request effects. Responses are validated and correlated before installation. Discovery is bounded, cancellation-aware, and runs no session/model/tool. Concurrent admission shares a completed configuration snapshot.

## Delivery

One protocol PR includes the normative contract, canonical schemas, generator changes, fixtures, and conformance checks. Four coordinated SDK PRs implement the server/client runtime and public declaration surfaces. Proposed SDK revisions can be pinned for integration checks before merging, then removed after SDK branches land. Nothing in this design publishes a release automatically.
