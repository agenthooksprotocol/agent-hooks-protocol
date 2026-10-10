# Binary content upload binding

This binding is normative for the mutable draft. JSON schemas define registration
and content parts, not HTTP octets, authentication, ordering, or availability.
Only non-text, non-JSON media attachments use this upload binding. Ordinary text,
including application-serialized structured content, MUST remain inline in event
JSON; senders MUST NOT upload text or provider-native prompt JSON as a substitute
for canonical messages. There is no JSON content-part kind.

## Configuration and destination

Each subscription MAY configure `upload: {endpoint, timeoutMs, maxBytes, auth?}`.
The sender MUST POST to the exact configured endpoint, preserving its path and
query. It MUST NOT append a route, derive a URL from a content reference, or assume
that the upload endpoint shares the event receiver's origin. It MUST NOT follow
redirects. HTTPS with receiver validation is required except for explicitly
configured loopback tests. HTTP upload is independent of event transport, including
stdio. `maxBytes` is a transfer limit, not permission to truncate content.

`auth`, when configured, uses the same explicit endpoint bindings as event delivery:
`bearer` (exactly one of `tokenEnv` or `tokenRef`) or `oauth`. See [authentication bindings](capability-auth.md#authentication-bindings).
The sender MUST resolve upload credentials independently. Absence of `upload.auth`
uses standard OAuth protected-resource discovery on this endpoint, not event
credentials or an anonymous-only assumption. Upload authentication, discovery,
acquisition, refresh, and retries count against the upload budget, not a fresh
budget per attempt. The receiver MUST derive authorization scope from verified
credentials and authorize uploads within that scope; merely accepting a valid
token is insufficient. Event IDs, subscription IDs, and opaque refs are not
identity or authorization claims. Anonymous uploads require explicit receiver
authorization and a receiver-defined scope. Secrets MUST NOT appear in descriptor metadata.

## Request framing

* Method: `POST`.
* `Content-Type: application/octet-stream`.
* `Content-Length`: exact body length in octets, including zero.
* `AHP-Content-SHA256`: lowercase 64-character SHA-256 hex of the exact body.

The body MUST be raw arbitrary octets. It MUST NOT be JSON-wrapped, base64-encoded,
converted through UTF-8, compressed, or otherwise content-encoded. This binding
does not use chunked transfer. `bodyBase64` in a test fixture is test storage only;
clients decode it once before counting, hashing, and sending bytes.

## Confirmation and errors

The sender supplies neither a content reference nor subscription identity. The
receiver allocates an opaque immutable reference after validating and authorizing
the bytes. Only `201 Created` with `Content-Type: application/json` and a canonical
[content-upload-receipt](../../schema/draft/content-upload-receipt.schema.json) JSON body
`{"ref": "receiver-allocated-reference", "size": 3, "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"}`
confirms synchronous availability within the authorized scope. Before publishing
an event, the sender MUST validate this receipt and verify that `size` and
`sha256` match the exact bytes sent. It MUST use the returned `ref`, not construct
one. A missing, malformed, or mismatched receipt fails the upload. `202` and
`204` are not confirmation. The receiver uses:

| Status | Meaning |
| --- | --- |
| 400 | Missing/malformed framing or a length/hash mismatch |
| 401 | Missing or invalid credentials |
| 403 | Principal is not authorized for the upload scope |
| 404 | Unknown upload route |
| 413 | Receiver size limit exceeded |

Other statuses fail the upload. An event receiver MAY return 404 when referenced
content is unavailable. The 201 receipt is an upload confirmation, not an event
acknowledgement. No renewal, expiry negotiation, or protocol-level retrieval route
is introduced.

The receiver MUST retain confirmed bytes for event processing. References are
immutable and access is controlled by credential-derived scope. A retry MAY
allocate another reference, including after a lost response. Senders MUST NOT
probe caller-selected references or rely on reference-based idempotence. A
receiver MUST NOT change the bytes bound to an allocated reference. Changed bytes
require a new upload and reference from the producer, not an attachment-content
modification effect. An effect cannot mutate or rebind an existing attachment.

## Upload before publication

For both `hooks/intercept` and `hooks/observe`, the sender MUST finish every
selected, authorized upload before publishing its referring message. Failure MUST
NOT yield a ready body reference. Apply the boundary's failure policy, suppress
failed delivery, or use an explicit schema-valid content gap. Never substitute
inline bytes, a local path, or an arbitrary retrieval URL. Selection is not
authorization; permissions are checked before transfer. Observations remain best
effort and a failed upload MUST NOT reopen settlement.

Attachment preparation is distinct from interceptor execution. A sender MAY
materialize immutable attachments once and prepare their authorized uploads to
matching subscriptions concurrently before the interceptor chain. This does not
permit concurrent interceptor calls or advance a later subscription's failure
policy ahead of its turn. Upload failures remain associated with the affected
delivery; they do not invalidate confirmed uploads for independent receivers.
An accepted edit may remove an attachment after its upload has completed; the
sender MUST NOT publish that removed attachment in the effective event. A
confirmed reference MAY be reused for the same immutable attachment and receiver
authorization context in a later delivery, including observation after settlement;
it MUST NOT be reused across independent authorization scopes. All preparation
remains subject to resource limits, deadlines, and cancellation.

## Canonical messages and content selection

<a id="AHP-CONTENT-001"></a>
**AHP-CONTENT-001 — MUST.** Model-visible content uses canonical message roles and ordered parts; ordinary text stays inline, structured content is serialized into text, and no JSON part is permitted.

<a id="AHP-CONTENT-002"></a>
**AHP-CONTENT-002 — MUST.** Authorization and body/metadata/omit selection apply to inline text and attachments without duplicate-content bypass; only immutable non-text, non-JSON media attachments use uploads.

A canonical message has `id`, `role`, and an ordered `parts` list of content
parts, with an optional boolean `synthesized` identity marker. Roles are `system`,
`developer`, `user`, `assistant`, and `tool`; provider-native role names MUST be
normalized. Role belongs to the message, not to a part or an uploaded blob.
Message order and part order are significant and MUST be preserved. Model-visible
event slots use the shared `message` definition (`modelVisibleItem` is an alias),
not provider-native message objects or uploaded prompt JSON. Generic content
slots use parts without inventing a conversation role.

Each part has `id`, `kind` (`text` or `attachment`), `mediaType`, and `selection`.
A text part has `mediaType: "text/plain"`; a body-selected text part carries a
`text` string directly in event JSON.
Applications that need structured content MUST serialize it into that string;
there is no JSON part and no protocol interpretation or merge of structure
serialized inside text. Text MUST NOT be replaced with an upload reference.
Reasoning and skills are content categories, not additional part kinds; reasoning
remains within its owning assistant message and distinguishable by category.

A body-selected attachment has a non-text, non-JSON `mediaType` and `body: {ref}`.
Attachment media types MUST NOT be `text/*`, `application/json`, or JSON-derived
media types (including `+json` types).
Attachments are immutable. A message-list effect MAY carry unchanged, authorized
existing attachment references, but MUST NOT edit or merge the bytes behind a
reference. Removing or reordering a message or part does not mutate its referenced
attachment. There is no binary modification target or path. Changed attachment
bytes require a new authorized upload and reference at the producing boundary,
not an edit through a modification effect. Upload confirmation `size` and `sha256`
belong to the separate receipt, not the ready body reference or ready part.
The receiver resolves the reference within its authorized scope and retains the
verified size and digest with it.

Content selection uses a required `default` plus category-name keys whose values
are `body`, `metadata`, or `omit`. Unknown categories use the default. Explicit
`category` overrides media-derived selection. Producers MUST preserve known
reasoning classification as `category: "reasoning"`; renaming a category MUST NOT
bypass its authorization or selection policy. Selection and authorization apply
to inline text as well as attachments: metadata/omit parts MUST NOT carry `text`
or `body`; a missing requested body uses `selection: "body"` and `gap` instead.
These views may disclose optional size/hash metadata only under the selected
policy. They do not imply body availability. Metadata/omit selection MUST NOT
cause an upload or consume an attachment source. Missing requested bodies follow
the boundary failure policy; intentional metadata/omit selection is not failure.

Synthesized message and part IDs MUST carry `synthesized: true`; the marker is
boolean. All SHA-256 fields are exactly 64 lowercase hexadecimal characters.
A sender MUST reject CR/LF in framing values before constructing HTTP headers.

## Preparation and security boundaries

For attachments, select independently authorized receivers and content views before consuming an owned body source. Constructing an owned source MUST NOT read or upload its bytes. Metadata-only, omitted, and unmatched routes MUST NOT consume the source. For selected body delivery, snapshot the bytes once within byte and time bounds, compute their actual size and SHA-256, and reuse that immutable snapshot for authorized fan-out rather than racing readers. Each receiver still allocates and confirms its own independently authorized upload reference before dependent event publication. Optional caller-supplied size/hash expectations MUST be checked against actual bytes, not trusted as verification; they are not required to construct a source.

Owned sources and any spool resources have explicit cleanup on success, failure, cancellation, and unused-source paths. Snapshotting and staging remain within the owning operation; they MUST NOT continue as detached work after completion. Host execution data remains separate from a delivery source. Bounded spooling is permitted; unbounded buffering and silent truncation are not.

The configured transfer budget is separate from the per-interceptor timeout, which starts at interception dispatch. Both are bounded by any enclosing hook-call budget, including pre-dispatch preparation. Transfer
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
