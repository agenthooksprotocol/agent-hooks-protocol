# Conformance tooling

`check_conformance.py` is dependency-free and uses only the Python standard library. Run it from any working directory:

```sh
python3 tools/check_conformance.py
```

It checks:

- JSON parsing with duplicate-key rejection and basic Draft 2020-12 schema structure;
- immutable draft identifiers and offline-only `$ref` resolution;
- golden fixture framing, schema outcomes, hashes, and request/event ID equality;
- requirement ID uniqueness, requirement text hashes, document anchors, headings, and artifact references;
- source migration proposal hash and preserved open-question count;
- absence of private Notion URLs;
- schema, fixture, and conformance profile manifest drift; and
- SDK-generation protocol version and stable names for schema roots and definitions in the schema manifest.

## Supported JSON Schema subset

The built-in fixture validator supports the keywords used by this repository: `$ref` with local JSON Pointers, `type`, `const`, `enum`, `required`, `properties`, `patternProperties`, `additionalProperties`, `propertyNames`, `items`, `minItems`, `maxItems`, `uniqueItems`, `contains`, `minContains`, `maxContains`, string lengths and patterns, numeric bounds, `allOf`, `anyOf`, `oneOf`, `not`, `if`/`then`/`else`, and the `uri` and `date-time` formats.

This validator is intentionally not presented as a complete JSON Schema implementation. The schemas declare Draft 2020-12 and can also be consumed by a conforming general-purpose validator. The local subset keeps repository CI deterministic and dependency-free.

While Working Draft artifacts are changing, refresh manifest hashes with:

```sh
python3 tools/check_conformance.py --update-manifests
```

Once a protocol release freezes an artifact set, later corrections use a new release rather than editing it in place.

## SDK model generator

The Rust generator reads SDK-generation metadata directly from the schema manifest and emits a language-neutral IR or TypeScript wire models and loss-preserving structural codecs:

```sh
cargo run --locked --manifest-path tools/sdk-codegen/Cargo.toml -- \
  check --revision 0.1.0-draft.1

cargo run --locked --manifest-path tools/sdk-codegen/Cargo.toml -- \
  generate --revision 0.1.0-draft.1 --language typescript --output /tmp/ahp.generated.ts
```

The structural codecs preserve unknown JSON but do not replace canonical Draft 2020-12 validation. See [`tools/sdk-codegen/README.md`](sdk-codegen/README.md).
