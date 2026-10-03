# SDK model generator

`ahp-codegen` is the official schema-driven SDK model generator. It reads the schema manifest directly; stable SDK names live in that manifest so schema and generation changes are reviewed together. Every named schema-document root receives parse and encode entrypoints. If generated output conflicts with its source schema, the schema takes precedence. Implementations may use this generator, another generator, or handwritten models.

Generated source belongs in each SDK repository together with a lock recording the protocol tag, schema snapshot, and generator version.

## Compatibility model

Parsing and protocol validation are separate operations. Generated parsers preserve forward-compatible data, but a successful parse does not establish that a message is valid AHP.

Generated codecs preserve unknown object properties recursively, retain unknown enum and discriminator values, select known variants exactly, and do not coerce values, apply defaults, fabricate required data, or structurally score union candidates. Their round-trip promise is semantic JSON preservation, not preservation of whitespace, key order, escape spelling, number spelling, or duplicate keys.

Do not use named parse entrypoints as validators or response classifiers. In particular, array cardinality is enforced by canonical validation, so `parseInterceptDenyResponse` and `parseInterceptNoEffectResponse` can both structurally parse the same payload. Canonically validate a response before classifying it or making an authorization decision; `parse*().ok` alone is not authorization.

## Commands

From the repository root:

```sh
cargo run --locked --manifest-path tools/sdk-codegen/Cargo.toml -- \
  check --revision draft

for target in typescript python go rust; do
  cargo run --locked --manifest-path tools/sdk-codegen/Cargo.toml -- \
    generate --revision draft --language "$target" \
    --output "/tmp/ahp.generated.$target"
done
```

Use `--emit-ir` instead of `--language` to inspect the language-neutral lowering. All emitters consume the same IR and implement the same structural parsing compatibility behavior.

The current `draft` snapshot permits integer request IDs and null response IDs. Generated codecs accept only integers that are safely interoperable across all supported SDKs. String-only request IDs require a future schema change; generation does not silently alter the schema.

## SDK synchronization

A push to `main` that changes schema snapshots, the generator, or draft conformance metadata runs `.github/workflows/sync-sdks.yml`. The workflow regenerates the TypeScript, Python, Go, and Rust SDKs and opens or updates one `automation/schema-sync` pull request in each SDK repository. Each SDK records the exact source commit, schema snapshot, manifest digest, and language in `ahp-codegen.lock.json`.

Cross-repository writes use a dedicated GitHub App. Configure `SDK_SYNC_APP_ID` as an Actions variable and `SDK_SYNC_APP_PRIVATE_KEY` as an Actions secret in this repository. The App requires **Contents: read and write** and **Pull requests: read and write** permissions. Each synchronization job mints a short-lived token scoped to its allowlisted target repository. Do not expose these credentials to pull-request workflows.

## Draft composite-schema support

The compiler lowers object/composition siblings as intersections, accepts JSON
Schema type arrays, and recognizes object-count constraints. Typed additional
properties remain preserved JSON in generated codecs; their value constraints,
cardinality, conditionals, formats, and permission rules require canonical schema
validation. This is not a compatibility mode or a relaxation of the wire grammar.

### Exact union selectors and presence predicates

Finite branch selectors must use literal alternatives rather than relying on an
open enum to exclude a sibling's known tag. Execution reasons are normalized this
way without changing canonical wire validity. Required-only schemas retain every
required member as presence-only Any fields; a missing member differs from null.

An inline oneOf may use `x-sdk-discriminator` when it deliberately has exact known
string tags plus a string-valued extension fallback. The compiler checks that the
selector is required in each branch and known literal values are unique. Codecs
select a known literal exactly; other tags are preserved as unknown variants.
Canonical validation still controls allowed extension syntax and required data.
This is not structural candidate scoring or a replacement for schema validation.
