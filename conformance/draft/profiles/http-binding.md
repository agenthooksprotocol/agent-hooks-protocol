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
API and actual event/upload receivers, not only a low-level HTTP constructor:

- Static `Authorization: Bearer abc123`, literal API keys/cookies, empty and absent
  maps, env/ref sources, and ignored source-object extensions.
- Invalid HTTP names and CR/LF/NUL injection rejected; legal non-ASCII values
  accepted where supported by the native library, without an ASCII-only policy.
- Case-insensitive duplicate names use the last configuration property, including
  differently sourced values, with only the winning source resolved.
- Configured headers override transport/default/authentication headers, including
  `Content-Type` and `Authorization`, without duplicate joined field values.
- Configured Authorization, in any casing and with any source, suppresses OAuth
  discovery/acquisition/challenge/retry flows even alongside explicit auth config.
  Missing references fail rather than fall back to another credential.
- Actual content uploads receive the backend's configured headers, merged last
  over upload defaults/auth; explicit Authorization suppresses upload OAuth too.
- Existing redirect behavior and native sensitive-header handling are preserved;
  headers are not attached globally to other backends or discovery/token requests.
- Existing secret helper behavior, deadlines/cancellation, failure policies, and
  redaction apply to literal and resolved values for events and uploads.

Schema fixtures cover configuration shape and injection characters, not native
HTTP support, duplicate precedence, secret resolution, or request behavior.
