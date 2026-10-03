# HTTP binding profile

**Status: Working Draft — `draft`.**

Each POST request body contains exactly one JSON-RPC object with a JSON media type. A request receives at most one JSON-RPC response object. A notification can use an empty successful HTTP response and never receives a JSON-RPC response body.

Portable bearer authentication stores exactly one environment-variable or deployment-managed secret reference in registration. The resolved token is sent in the HTTP `Authorization` header and is absent from registration, events, logs, denial reasons, and JSON-RPC error data.

## Authentication coverage

A conforming HTTP implementation MUST demonstrate the endpoint behavior in
the matching snapshot's `capability-auth.md`, Authentication bindings section.
Runtime evidence must cover supported mechanisms and explicit refusal of
unsupported ones, including:

- An omitted event binding and an omitted upload binding independently accepting
  success or processing a 401, header metadata, path-specific/root well-known
  fallback, and ordered authorization-server discovery.
- Refusal of untrusted issuers, mismatched resources/audiences, insufficient scopes,
  expired tokens, disallowed destinations/redirects, and credential leakage across
  event/upload endpoints or cache entries.
- Failed explicit bearer resolution/rejection and OAuth preset mismatch without
  identity switching, unauthenticated fallback, or discovery overriding the preset.
- Preconfigured TLS certificate selection before HTTP, without requiring a challenge.
- Missing/unsupported workload profiles rejected before acquisition; a claimed
  deployment profile needs its own advertisement, verification/exchange, and trust
  evidence. A generic `workload` capability is insufficient.
- Bounded acquisition/refresh/retries and host interaction under dispatch deadlines
  and cancellation, separate bounded preparation/upload budgets, and both failure
  policies preserving the underlying operation and forbidding late delivery.

Repository schema tests validate omitted bindings, shared upload/event preset
shapes, secret-reference exclusivity, and required workload profile identifiers.
They do not execute OAuth discovery, TLS, token validation, or workload exchange.
The existing SDK integration matrices exercise their pinned legacy behavior, not
this discovery contract; a passing matrix does not certify these runtime cases.
Implementations MUST report unsupported authentication paths as capability gaps.
