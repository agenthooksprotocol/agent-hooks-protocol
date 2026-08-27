# Conformance tooling

`check_conformance.py` is dependency-free and uses only the Python standard library. Run it from any working directory:

```sh
python3 tools/check_conformance.py
```

It resolves the `draft` specification, schema, fixture, and conformance roots as one logical snapshot by default. Pass `--snapshot <exact-semver>` to validate a frozen release snapshot.

The selected key must be `draft` or an exact `MAJOR.MINOR.PATCH` without a `v` prefix, prerelease suffix, build suffix, or numeric component with a leading zero. To validate one frozen snapshot explicitly:

```sh
python3 tools/check_conformance.py --snapshot 0.1.0
```

To discover and validate every frozen snapshot:

```sh
python3 tools/check_frozen_snapshots.py
```

The frozen-snapshot checker scans the four parallel roots below, rejects every non-`draft` directory whose name is not exact SemVer, and passes each discovered version to `check_conformance.py`. A version present under only some roots therefore fails as an incomplete snapshot.

Each selected snapshot must exist under all four parallel roots:

```text
spec/<snapshot>/
schema/<snapshot>/
fixtures/<snapshot>/
conformance/<snapshot>/
```

The directory key identifies the repository snapshot; it does not derive or replace the wire `protocolVersion`. The requirement and artifact manifests provide the revision metadata that the checker cross-checks. The checker does not create or retarget a frozen snapshot; freezing remains a reviewed publication step under the release policy.

It checks:

- JSON parsing with duplicate-key rejection and basic Draft 2020-12 schema structure;
- snapshot version agreement, public schema identifiers, and offline-only `$ref` resolution;
- golden fixture framing, schema outcomes, hashes, and request/event ID equality;
- requirement ID uniqueness, requirement text hashes, document anchors, headings, and artifact references;
- absence of private Notion URLs and broken relative Markdown links;
- schema, fixture, and conformance profile manifest drift.

CI runs the checker tests, validates `draft`, and runs `check_frozen_snapshots.py`.

## Supported JSON Schema subset

The built-in fixture validator supports the keywords used by this repository: `$ref` with local JSON Pointers, `type`, `const`, `enum`, `required`, `properties`, `patternProperties`, `additionalProperties`, `propertyNames`, `items`, `minItems`, `maxItems`, `uniqueItems`, `contains`, `minContains`, `maxContains`, string lengths and patterns, numeric bounds, `allOf`, `anyOf`, `oneOf`, `not`, `if`/`then`/`else`, and the `uri` and `date-time` formats.

This validator is intentionally not presented as a complete JSON Schema implementation. The schemas declare Draft 2020-12 and can also be consumed by a conforming general-purpose validator. The local subset keeps repository CI deterministic and dependency-free.

During unpublished artifact development, refresh manifest hashes with:

```sh
python3 tools/check_conformance.py --update-manifests
```

`--update-manifests` is limited to the mutable `draft` snapshot and only refreshes hashes already declared in its manifests. It is not an approval step or a release-freezing command. Frozen SemVer snapshots are immutable.

## Focused checker tests

Run the focused checker tests with:

```sh
python3 -m unittest discover tools/tests
```

The suite exercises the snapshot resolver and structural safeguards independently of the command-line entrypoint, including cross-root version agreement, required parallel roots, path confinement, manifest hash drift, offline schema references, aggregate schema coverage, and rejection of manifest updates for frozen snapshots. Run both the focused tests and `python3 tools/check_conformance.py` when changing checker behavior or any part of the logical draft snapshot.

The release freeze commands and required pre-tag validation are documented in the [release policy](../docs/RELEASES.md).
