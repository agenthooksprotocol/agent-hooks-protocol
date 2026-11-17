# HTTP binding

## HTTP binding
### Request and response

<a id="AHP-HTTP-001"></a>
**AHP-HTTP-001 — MUST.** The HTTP binding sends one JSON-RPC object per POST with a JSON media type and returns at most one JSON-RPC response object.

The harness sends each JSON-RPC message as the body of a new HTTP `POST` request.
Requirements:
- Request and response bodies MUST use `Content-Type: application/json` by default; explicit configured request headers take precedence as specified below.
- The body MUST contain one JSON-RPC object, not a batch.
- A successful `hooks/intercept`, `hooks/capabilities`, or `hooks/configuration` response MUST use HTTP status `200` and contain the correlated JSON-RPC response.
- A successfully accepted observe notification MUST use `202 Accepted` or `204 No Content` and no response body.
- HTTP 401, or a Bearer HTTP 403 challenge with `error="insufficient_scope"`, means the hook did not process the request. It can initiate bounded authentication or locally permitted scope step-up, never automatic consent, and permits only the [challenged replay](#challenged-replay) below. Other 403 responses, unresolved challenges, and any status other than the successes specified above are operational failures. See the [SDK authentication lifecycle](../../sdk-auth-lifecycle.md).
- Redirects MUST NOT be followed unless explicitly enabled for the configured endpoint.
This protocol revision does not use SSE, streaming responses, or a corresponding HTTP `GET` endpoint.
### Challenged replay
After the authentication provider handles a 401 or Bearer 403 `insufficient_scope` challenge, the client replays the rejected request at most once, with the same JSON-RPC request identity and the same buffered body, only for event deliveries that expect a response (`hooks/intercept`), `hooks/configuration`, and `hooks/capabilities`. Observe notifications and content uploads are not replayed; they rely on credentials sent on the first attempt, so an endpoint whose binding is known receives its stored token up front. A second rejection, any other status, or an unresolved challenge is an operational failure. The replay stays within the original operation's deadline and cancellation.
### TLS
Remote endpoints MUST use `https`. Plain `http` MAY be used only for loopback addresses or explicitly controlled local development environments.
Implementations MUST validate server certificates using platform trust policy unless a deployment explicitly configures a narrower trust root. Disabling certificate validation is NOT RECOMMENDED.
### Authentication
Event and capability-discovery endpoints follow
[authentication bindings](../../capability-auth.md#authentication-bindings).
Without an explicit HTTP binding or configured `Authorization`, clients attempt the endpoint and handle standard
OAuth protected-resource discovery; absence does not mean anonymous-only delivery.
Explicit `bearer` and `oauth` bindings remain endpoint-bound client policy.
Deployment-specific TLS identity selection can precede HTTP discovery and is
outside portable AHP configuration.
Unsupported authentication and credential failures MUST NOT cause an unauthenticated
fallback or automatic replacement of an explicit binding. For interception, the
configured failure policy determines whether the underlying harness operation
continues or is denied. Payload correlation fields never grant authority.
Upload endpoints apply authentication and discovery independently and MUST NOT
inherit event authentication credentials implicitly. Explicit backend header configuration also applies to uploads. See [content uploads](../../content-upload.md).

### Configured backend headers

[HTTP registration headers](../registration.md#AHP-REG-003) apply to event requests
and the backend's content uploads. Merge configured values last, including
protocol and authentication overrides. Configured `Authorization` suppresses
OAuth discovery, acquisition, challenge handling, and retries on both paths.
Existing redirect policies and native sensitive-header handling are unchanged.
