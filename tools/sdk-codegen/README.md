# SDK model generator

`ahp-codegen` is the official schema-driven SDK model generator. It reads the schema manifest directly; stable SDK names live in that manifest so schema and generation changes are reviewed together. Every named schema-document root receives parse and encode entrypoints. If generated output conflicts with its source schema, the schema takes precedence. Implementations may use this generator, another generator, or handwritten models.

Generated source belongs in each SDK repository together with a lock recording the protocol tag, schema revision, and generator version.

## Compatibility model

Parsing and canonical validation are separate operations. A parser can preserve data that is unknown to, or invalid under, its selected schema; accepting that data as valid AHP still requires validation against the selected Draft 2020-12 schema and applicable semantic requirements. Shipping a full JSON Schema evaluator may be an optional SDK feature, but every official SDK must run canonical validation and compatibility tests in CI.

Generated codecs preserve unknown object properties recursively, retain unknown enum and discriminator values, select known variants exactly, and do not coerce values, apply defaults, fabricate required data, or structurally score union candidates. Their round-trip promise is semantic JSON preservation, not preservation of whitespace, key order, escape spelling, number spelling, or duplicate keys.

Do not use named parse entrypoints as validators or response classifiers. In particular, array cardinality is enforced by canonical validation, so `parseInterceptDenyResponse` and `parseInterceptNoEffectResponse` can both structurally parse the same payload. Canonically validate a response before classifying it or making an authorization decision; `parse*().ok` alone is not authorization.

## Commands

From the repository root:

```sh
cargo run --locked --manifest-path tools/sdk-codegen/Cargo.toml -- \
  check --revision 0.1.0-draft.1

cargo run --locked --manifest-path tools/sdk-codegen/Cargo.toml -- \
  generate --revision 0.1.0-draft.1 --language typescript \
  --output /tmp/ahp.generated.ts
```

Use `--emit-ir` instead of `--language` to inspect the language-neutral lowering. The first emitter is TypeScript; later emitters consume the same IR and compatibility behavior.

Draft `0.1.0-draft.1` permits integer request IDs and null response IDs. The TypeScript structural codec accepts only safely representable JSON integers; an arbitrary-precision parser is required to losslessly ingest larger draft.1 integers from text. String-only request IDs require a future schema change; generation does not silently alter the schema.
