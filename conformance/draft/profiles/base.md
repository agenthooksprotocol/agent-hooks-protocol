# Base Protocol profile

**Status: Working Draft — `draft`.** For each snapshot, the canonical requirements are those selected by that snapshot's conformance manifest; this profile is a language-neutral test description.

## Claim inputs

A conformance claim records:

- role: `harness`, `backend`, or `both`;
- transport binding: `stdio`, `http`, or both;
- at least one capability profile.

## Observable checks

1. Every emitted protocol message is UTF-8 JSON-RPC 2.0.
2. Requests, notifications, and successful results contain `protocolVersion: "draft"` at the specified payload location.
3. Unknown object fields do not invalidate an otherwise valid message. Unknown event, effect, and enum values remain unsupported semantics.
4. Extension keys use reverse-DNS names.
5. A harness validates portable registration before use and sends an event to a backend only for an exact event-name subscription whose mode matches the delivery method.
6. A harness preserves event identity across retries.
7. A backend returns a defined JSON-RPC error for unsupported request semantics and sends no response to a notification.
8. No successful AHP result weakens host authorization, permission, approval, or sandbox controls.

A Base Protocol claim is incomplete without a capability-profile claim and at least one transport-binding claim.

## Canonical content and edit checks

For supported events and advertised edit capabilities, exercise these checks in
addition to envelope validation:

1. Model, turn, and context `items` are canonical message lists. Each message
   owns its `id`, `role`, `parts`, and optional `synthesized` flag. User-message
   events use `message.messages`. Parts do not repeat role or parent identity.
2. A selected text part carries `kind: "text"`, `mediaType: "text/plain"`,
   `selection: "body"`, and inline `text`, without a body reference or upload.
   Metadata, omit, and gap views contain neither `text` nor `body`.
3. Attachment bodies remain immutable `{ref}` references. A replacement may
   retain unchanged, authorized attachment descriptors or remove them. Reject
   attachment-content edits and unauthorized reference substitution; removal
   does not modify stored attachment bytes.
   Whole-list replacement is rejected if the existing list contains any
   metadata/omit/gap text part: changing identities must not bypass hidden-text
   selection. Append merge preserves those entries and remains subject to the
   host's selection and category policy for incoming parts. This restriction does
   not prevent removal or reordering of attachments without changing their bytes.
4. `modify` values for prompt, request, response, output, and ordinary outbound
   content are canonical message lists; instructions and summary are text-part
   lists. Elicitation result content is instead the parsed MCP answer object of
   an accepted form result. Input and workspace require actual object targets
   and object values. Output maps only to `tool.after.items`: no opaque native
   JSON output target is defined.
5. Replace substitutes the whole target. List merge appends in order without
   deduplicating messages or parts. Object merge is shallow, replaces nested
   values, and preserves literal null rather than treating it as deletion.
6. Reject edits without the exact target/operation capability, body selection,
   and both read and write authorization. Inline text does not bypass selection,
   category policy, or host authorization; an opaque reference grants no access.
7. Stage and validate the entire response before publishing any changes. An
   invalid later effect rejects all edits from that response. Accepted responses
   chain serially over the effective value; a later fail-open rejection does not
   undo earlier accepted responses.
8. For a body-selected elicitation request/result text part, parse the complete
   serialized MCP JSON and validate the pinned MCP contract. Request mode
   (including the MCP omitted-mode `form` default) and result action must agree
   with the envelope. Result content is allowed only for accepted form results.
   Do not parse metadata, omit, or gap views as executable MCP payloads.

### Checker scope

`tools/check_conformance.py` uses schemas to check edit value shapes in response
fixtures and adds semantic checks for selected inline MCP elicitation JSON in the
current draft, resolving the pinned schema within the selected snapshot. Schemas and isolated
fixtures cannot establish registration selection, category/part authorization,
reference scope, the actual output target type, or atomic host execution.

`apply_modify_response` is a focused, draft-only, modify-only runtime test helper. Its explicit
`EditContext` supplies host-resolved target selection, read/write authority, exact
capabilities, and authorized unchanged attachment descriptors. It validates the complete effect list against the canonical schema before staging
detached candidates without changing its input. The caller remains responsible for
complete response-envelope validation, event-specific roles (for example inbound
user and outbound assistant messages), target-specific JSON contracts,
per-part/category policy, and duplicate
bodies inside native/input/output JSON. It is not a general effect executor:
non-modify effects fail closed, and production implementations must stage control
state and external side effects with all other effects in a response. The focused
`tools/tests/test_inline_messages.py` tests these boundaries, including atomic
rejection and serial chaining, without claiming that wire fixtures prove runtime
permission enforcement.

### Boundary-specific modification values

The standalone `content` effect value admits canonical messages or an object;
conformance MUST also resolve the pending boundary. Outbound user content requires
canonical message lists; elicitation result content requires the MCP answer
object of an accepted form result. The contextual edit helper checks host-resolved
event, selection, permissions, capabilities, mode/action, and pinned answer
shape before staging. Hosts additionally validate the original requested form
schema and serialize the complete effective MCP result. No request modification
is permitted at `user.elicitation.request`; its structured params are not a model
message-list target. Contextual fixtures declare host-only `editContext` and
`contextualExpectedValid` in the manifest, separate from wire validity.

`tool.after` output edits target canonical `items` messages. Contextual output
fixtures cover replacement, ordered append, and rejection of objects and bare
parts; no native JSON output property is inferred.
