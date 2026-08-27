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

for target in typescript python go rust; do
  cargo run --locked --manifest-path tools/sdk-codegen/Cargo.toml -- \
    generate --revision 0.1.0-draft.1 --language "$target" \
    --output "/tmp/ahp.generated.$target"
done
```

Use `--emit-ir` instead of `--language` to inspect the language-neutral lowering. All emitters consume the same IR and implement the same structural parsing compatibility behavior.

Draft `0.1.0-draft.1` permits integer request IDs and null response IDs. Generated codecs accept only integers that are safely interoperable across all supported SDKs. String-only request IDs require a future schema change; generation does not silently alter the schema.

## SDK synchronization

A push to `main` that changes schemas, the active revision, or the generator runs `.github/workflows/sync-sdks.yml`. The workflow regenerates the TypeScript, Python, Go, and Rust SDKs and opens or updates one `automation/schema-sync` pull request in each SDK repository. Each SDK records the exact source commit, schema revision, manifest digest, and language in `ahp-codegen.lock.json`.

Cross-repository writes use a dedicated GitHub App. Configure `SDK_SYNC_APP_ID` as an Actions variable and `SDK_SYNC_APP_PRIVATE_KEY` as an Actions secret in this repository. Install the App only on the four SDK repositories with **Contents: read and write** and **Pull requests: read and write** permissions. Do not expose these credentials to pull-request workflows.
