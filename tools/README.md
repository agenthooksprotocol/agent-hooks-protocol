# Conformance tooling

`check_conformance.py` is dependency-free and uses only the Python standard library. Run it from any working directory:

```sh
python3 tools/check_conformance.py
```

It resolves the `draft` specification, schema, fixture, and conformance roots as one logical snapshot by default. Pass `--snapshot draft|YYYY-MM-DD` to validate one snapshot.

The selected key must be the literal `draft` or a real calendar date in exact `YYYY-MM-DD` form. To validate one frozen snapshot explicitly:

```sh
python3 tools/check_conformance.py --snapshot 2026-08-27
```

To validate `draft`, discover and validate every dated snapshot, and enforce released immutability:

```sh
python3 tools/check_conformance.py --all
```

In `--all` mode, the checker scans the four parallel roots below, rejects every non-`draft` directory whose name is not a valid exact date, and validates `draft` plus each discovered date. A version present under only some roots therefore fails as an incomplete snapshot. It selects the chronologically newest exact-date release tag reachable from `HEAD` and compares every date snapshot present at that tag against the complete working tree. Modification, deletion, rename, replacement, or addition beneath a released version fails even when a refreshed manifest would otherwise match. A wholly new version remains allowed and must independently pass complete-snapshot validation. When no release tag is reachable, the repository is in first-release bootstrap mode: internal validation still runs, but there is no released immutable baseline yet.

Release preparation uses `python3 tools/freeze_snapshot.py YYYY-MM-DD`. The command validates the mutable draft, copies all four trees, retargets only the defined version/status/path/schema-ID and on-wire fields, refreshes copied hashes, validates the date snapshot in staging, and refuses an existing destination. Review and commit the resulting snapshot before creating the matching date tag.

Each selected snapshot must exist under all four parallel roots:

```text
spec/<snapshot>/
schema/<snapshot>/
fixtures/<snapshot>/
conformance/<snapshot>/
```

The directory key, manifest `snapshotVersion`, and on-wire `protocolVersion` must agree. They are all `draft` for mutable work and all the same publication date for a release. The requirement and artifact manifests provide the revision metadata that the checker cross-checks.

It checks:

- JSON parsing with duplicate-key rejection and basic Draft 2020-12 schema structure;
- snapshot/status agreement, public schema identifiers, protocol-version constants, and offline-only `$ref` resolution;
- golden fixture framing, schema outcomes, hashes, and request/event ID equality;
- requirement ID uniqueness, requirement text hashes, document anchors, headings, and artifact references;
- absence of private Notion URLs and broken relative Markdown links;
- schema, fixture, and conformance profile manifest drift.

CI fetches full history, runs the standard-library checker tests, and invokes `check_conformance.py --all`; the Python checker owns snapshot discovery and newest-reachable-release-tag selection.

## Supported JSON Schema subset

The built-in fixture validator supports the keywords used by this repository: `$ref` with local JSON Pointers, `type`, `const`, `enum`, `required`, `properties`, `patternProperties`, `additionalProperties`, `propertyNames`, `items`, `minItems`, `maxItems`, `uniqueItems`, `contains`, `minContains`, `maxContains`, string lengths and patterns, numeric bounds, `allOf`, `anyOf`, `oneOf`, `not`, `if`/`then`/`else`, and the `uri` and `date-time` formats.

This validator is intentionally not presented as a complete JSON Schema implementation. The schemas declare Draft 2020-12 and can also be consumed by a conforming general-purpose validator. The local subset keeps repository CI deterministic and dependency-free.

During unpublished artifact development, refresh manifest hashes with:

```sh
python3 tools/check_conformance.py --update-manifests
```

`--update-manifests` is limited to the mutable `draft` snapshot and only refreshes hashes already declared in its manifests. It is not an approval step or a release-freezing command. Date-stamped snapshots are immutable.

## Focused checker tests

Run the focused checker tests with:

```sh
python3 -m unittest discover tools/tests
```

The suite exercises the snapshot resolver, `--all` behavior, and structural safeguards, including literal-draft and calendar-date formats, release retargeting, cross-root version agreement, required parallel roots, path confinement, manifest hash drift, offline schema references, aggregate schema coverage, first-release behavior, newest reachable release-tag selection, rejection of manifest updates for frozen snapshots, and full-tree comparison against temporary Git repositories. Run both the focused tests and `python3 tools/check_conformance.py --all` when changing checker behavior or any part of a logical snapshot.

The release freeze commands and required pre-tag validation are documented in the [release policy](../docs/RELEASES.md).
