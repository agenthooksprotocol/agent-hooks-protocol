# AHP-NNNN: HTTP registration headers across the spec and SDKs

- **Status:** Draft
- **Authors:** @danielkov
- **Tracking issue:** https://github.com/agenthooksprotocol/agent-hooks-protocol/issues/40
- **Created:** 2026-10-17
- **Review period:** Not started
- **Supersedes:** None
- **Superseded by:** None

`NNNN` is a template placeholder, not an assigned identifier. A maintainer is
requested to assign a number in the tracking issue before this document lands.

## Summary

Add optional `transport.headers` to HTTP backend registration, and support it in
the TypeScript, Python, Go, and Rust SDK registration-to-request paths. Values can
be literal non-secret metadata or explicit environment/secret-store references.
Existing authentication bindings retain ownership of `Authorization`.

This is a non-normative proposal, not an implementation or a change to the current
draft snapshot. The rules below are proposed requirements for a subsequent
spec/schema change; SDKs remain non-normative implementations.

## Motivation

Gateways and backend deployments need tenant/routing metadata and sometimes a
custom API-key header. Today the portable HTTP registration contract exposes only
`type` and `url`. Unknown configuration fields must be ignored, so merely adding
`headers` to a configuration does not reliably put them on the wire.

The SDK gap is specifically registration-driven delivery, not whether their HTTP
libraries can send headers. Python already exposes programmatic headers on its
low-level HTTP transport, but its registered hooks path does not forward them.
TypeScript's registered HTTP path sets JSON media headers; Go and Rust construct
requests with protocol/authentication headers rather than registration headers.
The pinned baseline sources are listed below.

### What MCP actually provides

MCP is a useful reference for configuration and transport plumbing, not a new
wire-protocol dependency:

- VS Code's MCP client configuration defines `headers` as a string-valued map on
  HTTP server entries and documents cross-origin isolation. This is a **client
  configuration convention**, not a universal MCP configuration schema.
- The MCP TypeScript SDK accepts `requestInit.headers`. Its common-header builder
  starts with `new Headers(...)` and applies transport-owned values with `set`,
  giving case-insensitive precedence to authentication/session/version headers.
- The MCP Python SDK accepts a preconfigured `httpx2.AsyncClient` for default
  headers. Its transport supplies MCP media/session/version headers per request.
- MCP core defines HTTP behavior and required headers such as `Accept` and
  `MCP-Protocol-Version`; it does not define the VS Code `headers` configuration
  property. AHP should specify its own registration contract.

Borrow the separation between caller metadata and transport-owned headers. Do
not copy MCP's session fields, OAuth fallback behavior, or client-specific
interpolation syntax into AHP. The proposal below deliberately rejects collisions
rather than silently allowing a reserved configured value to be overwritten.

## Goals and non-goals

- One language-neutral registration shape, with consistent behavior in all four SDKs.
- Support routing metadata and custom secret headers without embedding credentials.
- Prevent header injection, identity override, and cross-endpoint disclosure.
- Specify schema, runtime checks, and shared conformance cases before implementation.

No new hook envelope fields, server response headers, authentication variants,
stdio behavior, templating language, or upload-header configuration. This PR does
not change SDK repositories or generated artifacts.

## Proposal

### Configuration and secret resolution

```json
{
  "type": "http",
  "url": "https://policy.example/hooks",
  "headers": {
    "X-Tenant-Id": "engineering",
    "X-Api-Key": { "valueEnv": "AHP_POLICY_API_KEY" },
    "X-Gateway-Token": { "valueRef": "secrets/policy-gateway" }
  }
}
```

This object is a backend's `transport`, not a complete registration. `headers` is
optional; omission and `{}` preserve existing behavior. Each map entry is either:

- A string used verbatim for **non-secret** metadata. No environment expansion,
  `${...}` substitution, shell evaluation, or whitespace trimming occurs.
- An object containing exactly one recognized source: `valueEnv` (environment
  variable name) or `valueRef` (nonempty opaque secret-provider reference).

Literal credentials are prohibited, including API keys and cookies; SDKs cannot
reliably infer whether an arbitrary string is secret, so documentation and harness
configuration policy must enforce this distinction. The source model mirrors
`auth.tokenEnv` / `auth.tokenRef`, but resolves a complete header value rather
than automatically adding a `Bearer ` prefix. Bearer tokens belong in `auth`,
not in this map. Header configuration neither disables nor replaces existing
OAuth discovery when `auth` is omitted.

A harness-owned resolver receives backend identity, exact destination, event
purpose, source name/reference, and the dispatch deadline/cancellation context.
It returns a string, not a new authentication identity. Resolve once per dispatch
and reuse the resolved set on any already-permitted challenged retry; resolve
again for the next dispatch so rotation is possible. A resolver may cache under
its own explicit policy but must keep bindings endpoint-scoped. Environment
lookup uses the harness environment, not the stdio child `env` setting.

Missing, empty, unsupported, or failed referenced values fail the dispatch before
an event request is sent. No fallback to a literal, another secret, or a request
with fewer headers is allowed. Empty literal metadata strings are permitted.
Resolution is cancellable and included in the existing operation deadline.

### Names, values, and precedence

1. Validate names as nonempty ASCII HTTP field-name tokens, using
   `^[!#$%&'*+.^_\x60|~0-9A-Za-z-]+$`. Names are case-insensitive. Reject two map
   keys that differ only by ASCII case, even if their values match; never join them.
   Registration parsers should reject duplicate identical JSON keys when possible.
2. Values must contain only ASCII space through tilde (`U+0020`–`U+007E`), including
   values returned by resolvers. Reject CR, LF, NUL, tabs, DEL, non-ASCII characters,
   arrays, numbers, and null. This intentionally portable subset avoids differing
   library encodings; applications can use an agreed ASCII encoding for metadata.
3. Reject configuration of `Authorization`, `Proxy-*`, `Cookie`, `Set-Cookie`,
   `Host`, `Content-Type`, `Accept`, `Content-Length`, `Content-Encoding`,
   `Connection`, `Keep-Alive`, `Transfer-Encoding`, `TE`, `Trailer`, `Upgrade`,
   `Expect`, `Origin`, `Referer`, `Sec-*`, and `AHP-*`, case-insensitively. These are
   authentication, browser/security, framing, or reserved protocol concerns.
   `*` denotes a prefix, not a valid configured wildcard name. Reject reserved
   names even when no auth provider or protocol default would currently set them.
4. Build each request from validated configured metadata, then apply mandatory
   protocol and authentication headers through a case-insensitive header API.
   Authentication exclusively supplies `Authorization`. Never concatenate a
   configured value with a transport-owned value.
5. For SDK APIs that also accept programmatic headers/default HTTP clients, merge
   non-reserved defaults first and registration values second, comparing names
   case-insensitively; registration wins. Reject ambiguous duplicates within either
   input. Apply the same reserved-name/value rules to user-supplied additional
   headers on the registration path. Reject a conflict rather than let an injected
   client silently replace auth or framing fields. Low-level transport APIs used
   outside registration are not retroactively redefined by this proposal.

No arbitrary per-header size is standardized here: existing transport/harness
limits still apply, and implementations must enforce finite aggregate header
limits. A limit failure is an explicit delivery error, not truncation.

### Endpoint isolation and failures

Configured headers apply only to event POSTs (interceptions and observations) to
the configured HTTP transport URL, including auth retries that are already safe
under AHP's replay rules. They do not apply to OAuth/protected-resource discovery,
issuer metadata, token exchange, content uploads, or another backend, even at the
same origin. Do not attach them to a shared HTTP client's global defaults.

With a nonempty header map, redirects are not automatically followed, including
same-origin redirects. A redirect is an operational delivery failure; configure
the final trusted URL explicitly. This stricter-than-origin scope prevents secret
leakage to another path or downgrade and avoids replay of event bodies. Harness
extensions must not weaken this portable behavior. No credential or metadata is
forwarded to a redirect target. With no headers, existing redirect policy remains
unchanged. Upload configuration and authentication remain independent.

Static invalid configuration fails backend initialization/registration before
network I/O. Resolution/runtime validation failures prevent that event request;
existing interception failure policy decides whether the harness operation
continues or is denied. Observations report their existing best-effort delivery
error. Neither outcome authorizes sending a partial or unauthenticated fallback
request. Header failures create no additional retry mechanism.

Diagnostics may identify a backend and offending header name, but must not include
values, secret contents, or the full header map. Treat even literal metadata as
potentially sensitive in logging/tracing; redact all configured values. Secret
references never enter event payloads, error bodies, or generated fixtures.

### Proposed specification and schema work

A follow-up implementation should update registration, HTTP transport, and
capability/authentication text together. Add `headers` to `httpTransport` in the
draft registration schema. The following is the proposed property schema (not a
replacement for the complete registration schema):

```json
{
  "type": "object",
  "propertyNames": { "pattern": "^[!#$%&'*+.^_`|~0-9A-Za-z-]+$" },
  "additionalProperties": {
    "anyOf": [
      { "type": "string", "pattern": "^[ -~]*$" },
      {
        "type": "object",
        "properties": {
          "valueEnv": { "type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]*$" },
          "valueRef": { "type": "string", "minLength": 1 }
        },
        "oneOf": [
          { "required": ["valueEnv"] },
          { "required": ["valueRef"] }
        ]
      }
    ]
  }
}
```

The source object's unknown fields remain ignored, consistent with registration
extensibility, but an object with neither recognized source or both is invalid.
The map's arbitrary keys are header names, not ignorable extension properties.
Case-insensitive duplicates, reserved names, resolver results, and endpoint
isolation require runtime checks; schema acceptance alone is not conformance.
Runtime checks must use full-string matching (including rejecting final newlines),
regardless of a schema engine's end-anchor behavior.

## Compatibility and interoperability

This is an additive registration field, not a wire-envelope change. Old SDKs ignore
unknown fields, which can silently omit routing or credential headers. Schema
acceptance is therefore **not** a feature negotiation mechanism. Deployments that
require headers must pin a supporting SDK/harness release and validate its support
before enabling the configuration. A harness should expose support in its local
configuration validation; this proposal adds no server capability bit.

Existing registrations without headers retain their behavior. If an old deployment
has used an ignored `headers` field, upgrading can activate network behavior; call
this out in release notes and audit such configuration before upgrading. SDKs must
not claim support until the entire registration path and isolation tests pass.

## Security, privacy, and operational impact

Registration is trusted harness configuration, not backend-controlled capability
output. A server cannot select environment variables or secret references through
a challenge, redirect, or discovery document. Configuration consent and secret
provider access policy remain harness responsibilities.

Custom headers can carry deployment credentials or tenant attribution, but do not
by themselves establish a verified AHP principal. Servers must authenticate and
authorize requests normally; a tenant label is not proof of tenant membership.
Never forward these headers from untrusted incoming requests. Secret references
reduce stored credential exposure but do not make untrusted configuration safe.

Browser HTTP stacks may reject headers or require CORS preflight/allowlisting.
Supporting browser SDKs must surface this failure rather than drop headers;
backends remain responsible for their CORS policy. Resolving secrets can add
latency; it must not extend dispatch deadlines. No automatic retries are added.

## Alternatives considered

- **Only programmatic HTTP hooks:** available in some SDKs, but not portable across
  registration-driven clients and easy to bypass in convenience APIs.
- **Plain MCP-style string map with interpolation:** compact, but makes literal
  secrets and runtime-specific substitution ambiguous. Explicit references keep
  the existing AHP credential boundary.
- **Allow Authorization with last-writer-wins:** supports static bearer strings but
  creates two competing identity systems. Keep bearer/OAuth in `auth` instead.
- **Origin-scoped forwarding:** closer to some MCP implementations, but broader
  than AHP's endpoint binding. Require an explicit final event URL instead.

## Rollout, release, and deprecation

After proposal approval, implement the draft spec/schema and all four SDKs through
separately reviewed changes linked to the tracking issue. No delivery dates or
SDK version numbers are committed by this proposal. Acceptance alone publishes
nothing; use the existing release policy and refreshed draft manifests when
normative artifacts actually change. No existing field is deprecated.

| Surface | Required follow-up |
| --- | --- |
| Spec/schema/conformance | Define the above rules; add schema definitions, positive/negative registration fixtures, request-observation cases, and manifest updates. Regenerate registration models from the updated snapshot where applicable. |
| TypeScript SDK | Thread registration headers through the public registered client to its HTTP request builder; add resolver injection and case-insensitive `Headers` construction without overriding auth. |
| Python SDK | Thread the parsed field through registered hooks into `AuthenticatedHTTPTransport`; reuse low-level header mechanics only after enforcing the common validation, resolver, auth, and isolation rules. |
| Go SDK | Preserve headers in registration models/decoding, resolve in the dispatch context, build an endpoint-local `http.Header`, and prevent redirect/client-default bypass. |
| Rust SDK | Preserve headers in registration models, resolve with deadline/cancellation, use checked `HeaderName`/`HeaderValue` construction and endpoint-local request builders; retain no-redirect behavior. |

Every SDK must demonstrate via its **public registration API**, not just a raw HTTP
constructor: literal metadata and env/ref resolution arriving at a local receiver;
absent/empty maps; mixed-case collisions and reserved names; invalid source objects,
invalid names, CR/LF/NUL/tab/non-ASCII values (including resolved values); missing
secrets and resolver failures with zero event requests; cancellation/deadline
expiry without late sends; rotation on a subsequent dispatch; redacted errors;
authentication plus custom headers with no override; programmatic-default merge;
no forwarding to redirects, discovery/token endpoints, upload endpoints, or a
second backend. Exercise interception and observation delivery, bounded auth
retries, and server errors without introducing replay. Include a legacy-client
case demonstrating the ignore-unknown-field deployment hazard.

Run shared fixtures in the cross-SDK integration matrix before announcing portable
support. Roll back by removing the configuration only where the deployment can
operate without it; otherwise disable the backend or revert the supporting release
under the configured failure policy. Do not silently omit required headers.

## Unresolved questions

A maintainer must assign the proposal number. No behavioral options are left
implicit; the concrete choices above are offered for review.

## Decision record

Pending. No acceptance, normative change, or release is implied by this draft.

## Pinned references

Inspected 2026-10-17. These are source observations, not claims that a local
end-to-end MCP deployment was exercised. External code is linked for reference;
no upstream implementation code is copied into this proposal.

### MCP reference implementations

- [VS Code MCP configuration schema](https://github.com/microsoft/vscode/blob/9a59550fc841805ebb44cfd5a09b92beed591d15/src/vs/workbench/contrib/mcp/common/mcpConfiguration.ts#L317-L322)
  (MIT): string-valued headers and documented cross-origin isolation.
- [MCP TypeScript transport options](https://github.com/modelcontextprotocol/typescript-sdk/blob/9bc40e903fde15534ec06ff4c1347d47000dbe16/packages/client/src/client/streamableHttp.ts#L186-L225)
  and [common-header builder](https://github.com/modelcontextprotocol/typescript-sdk/blob/9bc40e903fde15534ec06ff4c1347d47000dbe16/packages/client/src/client/streamableHttp.ts#L489-L519)
  (MIT): request initialization and case-insensitive managed-header precedence.
- [MCP Python client options](https://github.com/modelcontextprotocol/python-sdk/blob/9f66f376d486e84e9810b368cf8be9d5908661ab/src/mcp/client/streamable_http.py#L714-L740)
  and [protocol-header construction](https://github.com/modelcontextprotocol/python-sdk/blob/9f66f376d486e84e9810b368cf8be9d5908661ab/src/mcp/client/streamable_http.py#L140-L157)
  (MIT): custom HTTP client and per-request protocol defaults.
- [MCP core Streamable HTTP specification, 2025-11-25](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/86f6e0c675afea9a8f233e09b6faf566b126086c/docs/specification/2025-11-25/basic/transports.mdx)
  (MIT): wire requirements, distinct from client configuration conventions.

### AHP baseline

- [Registration contract](https://github.com/agenthooksprotocol/agent-hooks-protocol/blob/42c68e8518d7db23416718f866f5cf2094f6dbf6/spec/draft/base/registration.md),
  [registration schema](https://github.com/agenthooksprotocol/agent-hooks-protocol/blob/42c68e8518d7db23416718f866f5cf2094f6dbf6/schema/draft/registration.schema.json),
  and [authentication/isolation rules](https://github.com/agenthooksprotocol/agent-hooks-protocol/blob/42c68e8518d7db23416718f866f5cf2094f6dbf6/spec/draft/capability-auth.md).
- [TypeScript registered transport](https://github.com/agenthooksprotocol/typescript-sdk/blob/1187b856adc44bdea5a17a2e38678580ca196d0c/packages/sdk/src/client/transport.ts).
- [Python registered hooks](https://github.com/agenthooksprotocol/python-sdk/blob/789782f3f99af65525ca1cbf3c08fd1a6d8158ed/src/agenthooksprotocol/_hooks.py)
  and [programmatic HTTP transport](https://github.com/agenthooksprotocol/python-sdk/blob/789782f3f99af65525ca1cbf3c08fd1a6d8158ed/src/agenthooksprotocol/transports/http.py).
- [Go registered HTTP transport](https://github.com/agenthooksprotocol/go-sdk/blob/279970ae947de51fbd402b7b3ccb8028be06cc1e/client/transport.go).
- [Rust registered adapter](https://github.com/agenthooksprotocol/rust-sdk/blob/96815abcb6a624dabe136de383a5bd38940229e0/src/adapters/registered.rs).
