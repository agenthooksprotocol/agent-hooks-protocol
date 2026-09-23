# HTTP binding

## HTTP binding
### Request and response

<a id="AHP-HTTP-001"></a>
**AHP-HTTP-001 — MUST.** The HTTP binding sends one JSON-RPC object per POST with a JSON media type and returns at most one JSON-RPC response object.

The harness sends each JSON-RPC message as the body of a new HTTP `POST` request.
Requirements:
- Request and response bodies MUST use `Content-Type: application/json`.
- The body MUST contain one JSON-RPC object, not a batch.
- A successful `hooks/intercept` or `hooks/capabilities` response MUST use HTTP status `200` and contain the correlated JSON-RPC response.
- A successfully accepted observe notification MUST use `202 Accepted` or `204 No Content` and no response body.
- Any other HTTP status is an operational failure.
- Redirects MUST NOT be followed unless explicitly enabled for the configured endpoint.
This protocol revision does not use SSE, streaming responses, or a corresponding HTTP `GET` endpoint.
### TLS
Remote endpoints MUST use `https`. Plain `http` MAY be used only for loopback addresses or explicitly controlled local development environments.
Implementations MUST validate server certificates using platform trust policy unless a deployment explicitly configures a narrower trust root. Disabling certificate validation is NOT RECOMMENDED.
### Authentication
Event and discovery endpoints use the authentication bindings defined in
[capabilities and authentication](../../capability-auth.md#authentication-bindings):
`bearer`, `oauth`, `mtls`, or `workload`, according to advertised support.
Unsupported authentication MUST fail before delivery. Credential resolution and
validation MUST fail closed; payload correlation fields never grant authority.
Upload endpoints use their independently configured authentication and MUST NOT
inherit event credentials. See [content uploads](../../content-upload.md).
