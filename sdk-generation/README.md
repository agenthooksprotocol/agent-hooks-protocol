# SDK generation

**Status: non-normative reference tooling.** The AHP specification and the released JSON Schemas remain authoritative. Implementations may use this generator, another generator, or handwritten models.

Each versioned directory pins one immutable schema revision and supplies only SDK projection metadata: public roots, stable names, and discriminator locations. It does not restate wire fields or constraints. Generated source belongs in each SDK repository together with a lock recording the protocol tag, schema-set digest, and generator version.

## Compatibility model

Parsing and canonical validation are separate operations. A parser can preserve data that is unknown to, or invalid under, its selected schema; accepting that data as valid AHP still requires validation against the selected Draft 2020-12 schema and the applicable semantic requirements. Shipping a full JSON Schema evaluator may be an optional SDK feature, but every official SDK must run canonical validation and the compatibility corpus in CI.

Generated codecs must:

- select a schema revision explicitly, then verify the payload's `protocolVersion`;
- preserve unknown object properties recursively;
- preserve unknown string enum values as raw strings;
- preserve an unknown discriminated variant as its complete raw object;
- distinguish an absent property, explicit `null`, a decoded value, and an invalid raw value where those states matter;
- select a known discriminator branch exactly;
- preserve zero-match or ambiguous untagged unions as invalid raw data;
- serialize semantic JSON without discarding unknown data; and
- let a known field replace the same key from preserved unknown data.

Generated codecs must not coerce scalar values, apply defaults while decoding, fabricate required data, structurally score union candidates, or use declaration order as protocol semantics. A malformed known discriminator branch remains a malformed known branch; it must not fall back to another or to an unknown variant. Responses require request correlation or an explicitly selected response root.

The round-trip promise is semantic JSON preservation. It does not preserve whitespace, object key order, escape spelling, number spelling, or duplicate keys.

## Commands

From the repository root:

```sh
cargo run --manifest-path tools/sdk-codegen/Cargo.toml -- \
  generate --revision 0.1.0-draft.1 --language typescript --output /tmp/ahp-types.ts
```

Use `--emit-ir` instead of `--language` to inspect the language-neutral lowering. The first emitter is TypeScript; later emitters must consume the same IR and compatibility corpus.

Draft `0.1.0-draft.1` permits integer request IDs and null response IDs, and its generated models retain those forms. The TypeScript structural codec accepts only safely representable JSON integers; an arbitrary-precision parser is required to losslessly ingest larger draft.1 integers from text. String-only request IDs require a future schema revision; code generation does not silently change an immutable schema.
