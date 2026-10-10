# Changelog

This file records unpublished draft changes and changes between protocol snapshots.

## Unpublished draft: canonical inline messages

- Model-visible content uses ordered canonical messages (`id`, `role`, `parts`),
  with roles `system`, `developer`, `user`, `assistant`, or `tool`.
- Parts are `text` or `attachment`. Ordinary text is inline event JSON; structured
  content is application-serialized text, not a JSON part or uploaded prompt JSON.
  Uploads carry only immutable non-text, non-JSON media attachments.
- Content selection applies to inline text as well as attachments. User messages,
  MCP elicitation, progress, summaries, and file snapshots follow these rules.
- Message-list and text-part-list replacement replaces the entire list; merge
  appends in order without deduplication. Actual object targets retain shallow
  merge. Atomic acceptance and serial effective-value chaining remain required.
- Tool result `modify(output)` targets canonical `tool.after.items`, with
  whole-list replacement and ordered append merge, not an undeclared native
  JSON result field.
- Elicitation result `modify(content)` retains the structured MCP answer object
  and shallow merge, unlike outbound message content lists. Contextual validation
  rejects cross-boundary value types. Elicitation request params do not gain
  modification authority.

## Unpublished draft: content receipts and shared events

- Event content references now contain only `ref`. The HTTP 201 upload response
  is a separate `ContentUploadReceipt` containing `ref`, `size`, and `sha256`.
- Ready body-selected attachment parts no longer repeat `size` or `sha256`; both
  fields are explicitly forbidden there and inside content references. Metadata,
  omitted, and body-gap descriptors retain their optional metadata disclosure.
- Interception and observation use the shared `Event` schema. Intercept requests
  retain their existing event-type subset as a request-level constraint.

### Breaking migration

This changes the draft wire contract and generated SDK APIs. Upgrade senders and
receivers together. Keep `{ref, size, sha256}` only as the upload receipt; after
checking it against the uploaded bytes, publish `{ref}` in event and effect
references. Remove outer `size` and `sha256` from ready attachment parts too.
Do not remove metadata from metadata-only or unavailable-body disclosures.
Receivers resolve references in the authenticated scope and use their stored
metadata; legacy size/hash claims are rejected, not trusted or silently ignored.

Use the shared generated `Event` type for both request event fields. Intercept
requests still reject known observation-only event kinds. Moving an event into
an intercept request does not make that event interceptable.

## Date-versioning model

- protocol releases, frozen directory names, release tags, public schema `$id` namespaces, snapshot metadata, and on-wire `protocolVersion` now use one `YYYY-MM-DD` publication date;
- the mutable working snapshot uses the literal `draft` in all four artifact roots, metadata, schema IDs and constants, examples, fixtures, and on the wire until release preparation assigns a date;
- release preparation retargets and validates all four copied trees; and
- frozen validation uses the newest reachable date release tag as its immutable full-tree baseline, while repositories with no such tag remain able to validate a first release.
