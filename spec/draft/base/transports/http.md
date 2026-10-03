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
- HTTP 401 can initiate the bounded OAuth discovery/authentication procedure below. An unresolved challenge and any status other than the successes specified above are operational failures.
- Redirects MUST NOT be followed unless explicitly enabled for the configured endpoint.
This protocol revision does not use SSE, streaming responses, or a corresponding HTTP `GET` endpoint.
### TLS
Remote endpoints MUST use `https`. Plain `http` MAY be used only for loopback addresses or explicitly controlled local development environments.
Implementations MUST validate server certificates using platform trust policy unless a deployment explicitly configures a narrower trust root. Disabling certificate validation is NOT RECOMMENDED.
### Authentication
Event and capability-discovery endpoints follow
[authentication bindings](../../capability-auth.md#authentication-bindings).
Without an explicit HTTP binding, clients attempt the endpoint and handle standard
OAuth protected-resource discovery; absence does not mean anonymous-only delivery.
Explicit `bearer` and `oauth` bindings remain endpoint-bound client policy.
Deployment-specific TLS identity selection can precede HTTP discovery and is
outside portable AHP configuration.
Unsupported authentication and credential failures MUST NOT cause an unauthenticated
fallback or automatic replacement of an explicit binding. For interception, the
configured failure policy determines whether the underlying harness operation
continues or is denied. Payload correlation fields never grant authority.
Upload endpoints apply authentication and discovery independently and MUST NOT
inherit event credentials. See [content uploads](../../content-upload.md).
