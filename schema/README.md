# JSON Schemas

**Status: Working Draft.** These artifacts describe protocol snapshot `draft`; they are not a stable compatibility promise.

The mutable schema snapshot lives beneath `schema/draft/`; frozen releases use matching exact `YYYY-MM-DD` directories. Public `$id` values mirror the selected snapshot under `https://agenthooksprotocol.org/schemas/<snapshot>/`: `draft` while mutable and the publication date after freezing. All `$ref` values are relative file references or same-document fragments so validation can run offline. Unknown object fields remain allowed unless the selected protocol revision explicitly forbids a known field in a particular context.

These schemas encode the current draft shape so prototypes can interoperate. Where the canonical specification lists an open question, the corresponding schema choice is provisional evidence for review, not a resolution of that question.

Run the dependency-free checks from the repository root:

```sh
python3 tools/check_conformance.py
```

The checker implements the JSON Schema subset documented in [`../tools/README.md`](../tools/README.md). A full Draft 2020-12 validator may also be used.
