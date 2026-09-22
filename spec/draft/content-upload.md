# Binary content upload binding

This binding is normative for the mutable draft. JSON schemas define registration
and descriptors, not HTTP octets, authentication, ordering, or availability.

## Configuration and destination

Each subscription MAY configure `upload: {endpoint, timeoutMs, maxBytes, auth?}`.
The sender MUST POST to the exact configured endpoint, preserving its path and
query. It MUST NOT append a route, derive a URL from a content reference, or assume
that the upload endpoint shares the event receiver's origin. It MUST NOT follow
redirects. HTTPS with receiver validation is required except for explicitly
configured loopback tests. HTTP upload is independent of event transport, including
stdio. `maxBytes` is a transfer limit, not permission to truncate content.

`auth`, when configured, is `{type: "bearer", tokenEnv: "ENV_NAME"}`. The sender
MUST resolve this token independently from event authentication and send it as
`Authorization: Bearer <token>`. Absence of `upload.auth` MUST NOT inherit event
credentials. The receiver MUST authorize access to the named subscription; merely
accepting a valid token is insufficient. Anonymous uploads require explicit
receiver authorization. Secrets MUST NOT appear in descriptor metadata.

## Request framing

* Method: `POST`.
* `Content-Type: application/octet-stream`.
* `Content-Length`: exact body length in octets, including zero.
* `AHP-Subscription`: unpadded base64url of the UTF-8 subscription ID.
* `AHP-Content-Ref`: unpadded base64url of the UTF-8 producer-assigned reference.
* `AHP-Content-SHA256`: lowercase 64-character SHA-256 hex of the exact body.

The body MUST be raw arbitrary octets. It MUST NOT be JSON-wrapped, base64-encoded,
converted through UTF-8, compressed, or otherwise content-encoded. This binding
does not use chunked transfer. `bodyBase64` in a test fixture is test storage only;
clients decode it once before counting, hashing, and sending bytes.

## Confirmation and errors

Only `204 No Content`, with no receipt body, confirms synchronous availability of
those bytes to that subscription. `202` is not confirmation. The receiver uses:

| Status | Meaning |
| --- | --- |
| 400 | Missing/malformed framing or a length/hash mismatch |
| 401 | Missing or invalid credentials |
| 403 | Principal is not authorized for the subscription |
| 404 | Unknown upload route |
| 409 | Existing subscription-scoped reference has different bytes |
| 413 | Receiver size limit exceeded |

Other statuses fail the upload. An event receiver MAY return 404 when referenced
content is unavailable. No response receipt, renewal, expiry negotiation, or
protocol-level retrieval route is introduced.

The receiver MUST retain confirmed bytes for event processing. References are
immutable and scoped to a subscription. A retry MAY reuse the reference only for
identical bytes and metadata. Changed bytes require a new reference while keeping
the same logical item ID. A receiver MUST reject conflicting reuse.

## Upload before publication

For both `hooks/intercept` and `hooks/observe`, the sender MUST finish every
selected, authorized upload before publishing its referring message. Failure MUST
NOT yield a ready body reference. Apply the boundary's failure policy, suppress
failed delivery, or use an explicit schema-valid content gap. Never substitute
inline bytes, a local path, or an arbitrary retrieval URL. Selection is not
authorization; permissions are checked before transfer. Observations remain best
effort and a failed upload MUST NOT reopen settlement.

A normalized item's `body` is `{ref, size, sha256}`. `size` and hash describe exact
uploaded bytes. A receiver MUST resolve and verify this descriptor against confirmed
`(subscription, ref, size, sha256)` storage. Metadata and omitted views have no body.
Body-selection gaps have a `gap` instead of `body`. Optional descriptor `size` and
`sha256` metadata must agree with body metadata when both are present; JSON Schema
cannot enforce cross-field equality.

Content selection uses a required `default` plus category-name keys whose values
are `body`, `metadata`, or `omit`. Unknown categories use the default. Explicit
`category` overrides media-derived selection except that `kind: "reasoning"`
always selects reasoning. Reasoning blocks use `parentItemId` to name their owning
assistant message. Skills use ordinary `kind: "skill"` items. Opaque `native`
payloads are not normalized descriptors and never bypass content permissions.

## Contextual model-visible roles and synthesized identity

A generic descriptor does not inherently have a conversation role. Model-visible
slots use the `modelVisibleItem` definition: `role` is required on owning items
and their represented blocks, including metadata/omit views. A child block MUST
repeat the owning role; `parentItemId` alone does not establish role inheritance.
The producer MUST keep the child's role consistent with its known owner. Generic
file snapshots, audit descriptors, and upload references need not carry role.
Synthesized descriptor IDs MUST carry `synthesized: true`; the marker is boolean.
All SHA-256 fields are exactly 64 lowercase hexadecimal characters. A sender MUST
reject CR/LF in framing values before constructing HTTP headers.

## Preparation and security boundaries

Upload content as soon as it is available. Its configured transfer budget is
separate from the hook timeout, which starts at interception dispatch. Transfer
failure is distinct from hook failure; content gaps describe withholding,
unavailability, limits or transfer failure without silent truncation. Observation
preparation and uploads are best effort, never an execution gate or a durable
buffering requirement.

Authorization and selection apply to duplicate bodies in native, input, output
and other opaque JSON, not only normalized descriptors. Producers must project
those payloads explicitly; schemas cannot identify every embedded secret or
model-visible byte. `includeNative` is off by default; approved native values may
be any JSON value, without an invented wrapper, and never replace required
normalized fields. Event authentication, client certificates and token acquisition
must not migrate to an upload resource merely because event delivery uses them.
An upload confirmation is not an event acknowledgement or proof of durable storage.
