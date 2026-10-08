# Changelog

This file records changes between published protocol snapshots.

## Unpublished draft: content receipts and shared events

- Event content references now contain only `ref`. The HTTP 201 upload response
  is a separate `ContentUploadReceipt` containing `ref`, `size`, and `sha256`.
- Ready body-selected content items no longer repeat `size` or `sha256`; both
  fields are explicitly forbidden there and inside content references. Metadata,
  omitted, and body-gap descriptors retain their optional metadata disclosure.
- Interception and observation use the shared `Event` schema. Intercept requests
  retain their existing event-type subset as a request-level constraint.

### Breaking migration

This changes the draft wire contract and generated SDK APIs. Upgrade senders and
receivers together. Keep `{ref, size, sha256}` only as the upload receipt; after
checking it against the uploaded bytes, publish `{ref}` in event and effect
references. Remove outer `size` and `sha256` from ready body-selected items too.
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
