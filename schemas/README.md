# JSON Schemas

**Status: Working Draft.** These artifacts describe draft protocol revision `0.1.0-draft.1`; they are not a stable compatibility promise.

Working Draft schemas may change in place while the protocol is under active development; manifest hashes identify their exact contents. A protocol release freezes the schema revision it publishes, and later corrections use a new release. All `$ref` values are relative file references or same-document fragments so validation can run offline. Unknown object fields remain allowed unless the Working Draft explicitly forbids a known field in a particular context.

These schemas encode the current draft shape so prototypes can interoperate. Where the canonical specification lists an open question, the corresponding schema choice is provisional evidence for review, not a resolution of that question.

Run the dependency-free checks from the repository root:

```sh
python3 tools/check_conformance.py
```

The checker implements the JSON Schema subset documented in `tools/README.md`. A full Draft 2020-12 validator may also be used.
