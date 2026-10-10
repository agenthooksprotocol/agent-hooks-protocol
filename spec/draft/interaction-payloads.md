# Interaction and configuration payload encoding

This mutable-draft binding composes the common event envelope with the named
`interaction-event.schema.json` overlays. Nested protocol-owned payloads are
closed; MCP elicitation uses the pinned published subset described below.

| Event | Required payload fields |
| --- | --- |
| `config.change.before` | `change`: `source`, `scope`, `settings`, `summary` |
| `config.change.after` | `change`: `source`, `scope`, `settings`, `summary` |
| `user.attention` | `attention`: `kind`, `message`, `title` |
| `user.elicitation.request` | `elicitation`: `server`, `mode`; optional `request` (text part) |
| `user.elicitation.result` | `elicitation`: `server`, `mode`, `action`; optional `result` (text part) |
| `user.message.inbound` | `message`: `channel`, `sender`, `messages` |
| `user.message.outbound` | `message`: `channel`, `messages` |
| `hook.failure` | `failure`: `backendId`, `reason`, `policy` (plus `parentEventId`) |

Configuration `change` describes proposed or actual effective configuration, never
a general mutation API. Denied proposals MUST NOT generate fictitious after events.
Managed secrets remain excluded even when a diff summary is present.

User `message.messages` is an ordered canonical message list. Inbound messages
have role `user`; outbound messages have role `assistant`. Ordinary text lives
inline in ordered `parts`; only non-text, non-JSON media attachments use immutable
references. The transport wrapper retains channel and inbound sender identity;
the canonical role does not assert that an external sender is human.
Attention `message` and `title` are singular text parts subject to the same
inline selection rules. Attention is a notification, not permission approval.
Hook failures identify the backend, subscription, affected parent event, reason,
and applied failure
policy; they are not tool/model execution errors.

Validate the complete event through the wire root, not an overlay in isolation.
An overlay intentionally does not repeat envelope requirements. Exact request/result
correlation, effective application, permission enforcement, and native surface
coverage require runtime checks.

Configuration after events describe actual effective application, not merely a
file write or restart, and no after event represents a denied or unchanged
proposal. User attention is not an idle veto or evidence of task change. Inbound
external messages must not be fabricated as human input. Hook-failure audit
routing must not depend on the failing backend receiving its own failure.

## MCP elicitation binding (2025-11-25)

The authoritative types are pinned in `upstream/mcp/2025-11-25`; the generated
`mcp-elicitation.schema.json` definitions `request` and `result` validate content
bodies independently of the AHP event envelope. `server` preserves the requesting
server identity, scoped by the adapter/source, and MUST NOT be replaced with the
responding user identity. The result repeats that identity and normalized mode;
`parentEventId` correlates the result with the AHP request event.

`request` and `result` are optional singular ordinary text parts, not messages,
item arrays, JSON parts, or attachment references. For body selection,
`request.text` contains application-serialized JSON for the COMPLETE MCP
`elicitation/create` **params object**, including `_meta` and any supported task
metadata, not the JSON-RPC envelope or an extracted prompt. `result.text` contains
serialized JSON for the COMPLETE MCP `ElicitResult`, including its structured
`content` and `_meta`, not an AHP item array. Applications serialize these objects
into ordinary text; the boundary parses that text and validates the value against
the pinned request/result definition as well as validating the AHP envelope.
Do not upload MCP JSON or ordinary prompts. Serialization MUST preserve the
complete value; original JSON byte formatting is not a wire requirement.

### Elicitation modification target

At `user.elicitation.result`, `modify(content)` edits only the parsed result's
`content` answer object, not a canonical message list, serialized text, the
result wrapper, or its `action`. Both replace and merge require objects. Merge
is shallow; answer fields replace whole values and omitted fields survive.
Only an accepted form result with authorized body selection and read/write
permission can expose this target. Validate the effective content against the
pinned MCP answer shape and the original requested form schema before acceptance;
serialize the complete effective result back into the ordinary text binding.
Keep action, mode, and envelope metadata consistent. A list here MUST be rejected
atomically, just as an object at ordinary outbound `modify(content)` MUST be
rejected. The standalone effect schema cannot resolve this distinction.
No JSON message part or attachment upload is introduced. Attachment bytes remain
immutable. `user.elicitation.request` has no `modify(request)` capability: its
structured MCP params are not the model request message-list target.

Metadata selection has `selection: "metadata"` without `text`. Omission has no
part or an explicit `selection: "omit"` part; missing requested content uses
`selection: "body"` with `gap` instead of `text`. Apply authorization and content
selection before including inline text. These views preserve event/control
metadata but do not imply content availability or authorize execution. No upload
is required or permitted for this structured-text binding. A metadata/omit/gap
view is not an executable request with validated input. Apply the subscription
failure policy for missing requested content, not intentional metadata/omit
selection.

A form request has optional `mode: "form"` (omission means form), `message`, and
`requestedSchema`. Only the published top-level primitive/enum subset is valid:
strings, numbers/integers, booleans, single-select enums (untitled, titled, legacy
enumNames) and multi-select string enums (untitled or titled). Arbitrary nested
objects/arrays and arbitrary JSON Schema keywords are not supported. This is an
explicit AHP subset restriction: the upstream generated JSON Schema leaves
objects open, but its plain-string alternative otherwise accepts unsupported
form keywords and malformed enum definitions. The extraction closes the
requested-schema vocabulary and primitive/enum objects; unknown extension
keywords there are not compatible with this binding. MCP params/result extension
fields and `_meta` remain open and preserved in the serialized value. A URL
request requires `mode: "url"`, `message`, `url`, and opaque `elicitationId`.
AHP metadata `mode` is always explicit and MUST equal the parsed mode after
the form default is interpreted; no default is injected into the serialized value.

Results have `action: "accept" | "decline" | "cancel"`. `content` is optional
structured data with string, number, boolean or string-array values, never content
items. It is present only for accepted form data and must satisfy the requesting
form schema; decline/cancel and URL results omit it. Envelope `action` MUST equal
the parsed result action. These cross-body/mode rules require runtime checks.
URL acceptance means consent to open the URL, **not completion** of that flow.
`notifications/elicitation/complete` does not automatically create an AHP result
or other event. Sensitive information MUST use URL mode, never a form; authorize
URL navigation and preserve requester identity without exposing secrets in logs.

`capabilities.elicitation.form` and `.url` are independent optional objects.
Absent modes are unsupported; an empty AHP elicitation object grants neither.
For an MCP-origin capability only, legacy `elicitation: {}` maps to form support.
AHP adapters are not required to implement either mode or both. Advertise only
modes and effects that the adapter can faithfully enforce. Return/modify effects
remain subject to authorization, structured payload validation and updated inline
text when values change.

## Pinned upstream maintenance

`upstream/mcp/2025-11-25/pin.json` records SHA-256 digests of the official
version-tagged TypeScript and JSON Schema. `python3 tools/mcp_elicitation.py`
checks digests and extraction offline. `--refresh` downloads only that pinned
version; review the diff, run compatibility tests and regenerate SDKs before
adopting changes. This is not automatic support for future MCP revisions.
Form defaults remain annotations, not values injected into answers. Authenticated
adapter configuration scopes requester identity; payload fields alone are not
trusted identity.
