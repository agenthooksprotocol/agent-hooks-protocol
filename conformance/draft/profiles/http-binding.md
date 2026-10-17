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
- Bounded acquisition/refresh/retries and host interaction under dispatch deadlines
  and cancellation, separate bounded preparation/upload budgets, and both failure
  policies preserving the underlying operation and forbidding late delivery.

Repository schema tests validate omitted bindings, shared upload/event preset
shapes, secret-reference exclusivity, and rejection of non-portable mechanism types.
They do not execute OAuth discovery, TLS, token validation, or workload exchange.
The SDK integration matrices do not execute OAuth discovery; a passing matrix
does not certify these runtime cases.
Implementations MUST report unsupported authentication paths as capability gaps.

Deployment-specific workload/federation and client-certificate tests MAY supply
additional security evidence, but MUST NOT be reported as portable AHP
authentication variants. Their HTTP/TLS or credential-provider configuration is
outside AHP; the general security and timing requirements still apply.

## Registration header coverage

A conforming harness MUST demonstrate AHP-REG-003 through its public registration
API and a local request receiver, not only a low-level HTTP constructor:

- Literal metadata, empty strings, absent/empty maps, env/ref sources, ignored
  source extensions, no interpolation, and per-dispatch secret rotation.
- Invalid names, case-insensitive duplicates, every reserved exact name/prefix,
  malformed source objects, and non-ASCII/control values rejected before I/O.
- Missing/empty/unsupported/failed sources and invalid resolved values producing
  zero event requests, without exposing values in errors or logs.
- Deadline/cancellation during resolution, one resolution per dispatch, reuse
  across already-permitted auth retries, and both interception failure policies.
- Interception and observation delivery, with best-effort observation errors.
- Case-insensitive merging of programmatic defaults and registration values,
  registration precedence, and protection against injected-client overrides.
- No forwarding to same-origin or cross-origin redirects, authentication or
  capability discovery, token exchange, uploads, or another backend.
- Finite aggregate limits with explicit errors rather than truncation, and
  browser/CORS failures surfaced rather than silently dropping headers.

The registration fixtures exercise schema-representable structure only. Reserved
names, case-folded duplicates, full-string validation, resolution, redaction,
and request isolation require runtime evidence; passing schema fixtures alone
does not establish header support.
