# Changelog

This file records changes between published protocol snapshots.

## Date-versioning model

- protocol releases, frozen directory names, release tags, public schema `$id` namespaces, snapshot metadata, and on-wire `protocolVersion` now use one `YYYY-MM-DD` publication date;
- the mutable working snapshot uses the literal `draft` in all four artifact roots, metadata, schema IDs and constants, examples, fixtures, and on the wire until release preparation assigns a date;
- release preparation retargets and validates all four copied trees; and
- frozen validation uses the newest reachable date release tag as its immutable full-tree baseline, while repositories with no such tag remain able to validate a first release.
