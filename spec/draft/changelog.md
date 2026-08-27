# Changelog

This file records changes between published protocol snapshots. No published snapshots exist yet.

## Date-versioning model

- protocol releases, frozen directory names, release tags, public schema `$id` namespaces, snapshot metadata, and on-wire `protocolVersion` now use one `YYYY-MM-DD` publication date;
- the mutable working snapshot uses the literal `draft` in all four artifact roots, metadata, schema IDs and constants, examples, fixtures, and on the wire until release preparation assigns a date;
- release preparation retargets and validates all four copied trees without making TypeScript a protocol source; and
- frozen validation uses the newest reachable date release tag as its immutable full-tree baseline, while repositories with no such tag remain able to validate a first release.

## Draft source reconciliation

The split canonical draft was reconciled across the complete `AHP-0001` update chain from the original `4942b3b` revision through the August 25 `e6ccb30` revision and the latest August 26 `4cead76` revision.

Adopted substantive changes:

- defined a subscription as exact event names plus a delivery mode and mode-specific policy;
- removed references to a separate, undefined `enabled` subscription state;
- required exact event-name and delivery-mode matching before dispatch to a backend; and
- made the Tool Interception requirement explicitly name `capabilities.effects` and the advertised `deny` effect.

No normative source change was rejected. The following non-normative source edits were intentionally not copied verbatim:

- source fetch dates and proposal-provenance links are obsolete because the final canonical tree has no migrated proposal or source-migration record;
- the August 25 move of “AHP at a glance,” the August 26 move of adjacent-standards material, and all resulting one-file renumbering are superseded by the canonical architecture plus `base/`, `server/`, and `client/` document split;
- both Mermaid rendering changes were presentation-only, so the canonical architecture keeps equivalent plain-text diagrams; and
- the isolated `standardizes`/`standardises` spelling change was not a protocol change, so repository spelling remains consistent.

The August 25 grouping of stdio and HTTP beneath Transport is represented by `base/transports/`, and the August 26 malformed table column group repair is applied to the split terminology table.
