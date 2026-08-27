# HTTP binding

## 18. HTTP binding
### 18.1 Request and response

<a id="AHP-HTTP-001"></a>
**AHP-HTTP-001 — MUST.** The HTTP binding sends one JSON-RPC object per POST with a JSON media type and returns at most one JSON-RPC response object.

The harness sends each JSON-RPC message as the body of a new HTTP `POST` request.
Requirements:
- Request and response bodies MUST use `Content-Type: application/json`.
- The body MUST contain one JSON-RPC object, not a batch.
- A successful intercept response MUST use HTTP status `200` and contain the JSON-RPC response.
- A successfully accepted observe notification SHOULD use `202 Accepted` or `204 No Content` and no JSON-RPC response.
- Any other HTTP status is an operational failure.
- Redirects MUST NOT be followed unless explicitly enabled for the configured endpoint.
AHP v0.1 does not use SSE, streaming responses, or a corresponding HTTP `GET` endpoint.
### 18.2 TLS
Remote endpoints MUST use `https`. Plain `http` MAY be used only for loopback addresses or explicitly controlled local development environments.
Implementations MUST validate server certificates using platform trust policy unless a deployment explicitly configures a narrower trust root. Disabling certificate validation is NOT RECOMMENDED.
### 18.3 Authentication
AHP v0.1 defines one portable HTTP authentication profile: static bearer authentication through a credential reference.
The registration document names an environment variable or implementation-defined secret reference. The harness resolves it at runtime and sends:
```text
Authorization: Bearer <token>
```
The literal token MUST NOT appear in the portable registration document, event payload, logs, denial reason, or JSON-RPC error data.
OAuth 2.1, mTLS, workload identity, signed requests, and service discovery are out of scope for the portable v0.1 HTTP binding.
