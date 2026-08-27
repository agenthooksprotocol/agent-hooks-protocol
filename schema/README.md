# JSON Schemas

**Status: Working Draft.** These artifacts describe draft protocol revision `0.1.0-draft.1`; they are not a stable compatibility promise.

The mutable schema snapshot lives beneath `schema/draft/`; frozen releases use matching exact SemVer directories. Public `$id` values retain the `https://agenthooksprotocol.org/schemas/0.1.0-draft.1/` namespace independently of repository paths. All `$ref` values are relative file references or same-document fragments so validation can run offline. Unknown object fields remain allowed unless the Working Draft explicitly forbids a known field in a particular context.

These schemas encode the current draft shape so prototypes can interoperate. Where the canonical specification lists an open question, the corresponding schema choice is provisional evidence for review, not a resolution of that question.

Run the dependency-free checks from the repository root:

```sh
python3 tools/check_conformance.py
```

The checker implements the JSON Schema subset documented in [`../tools/README.md`](../tools/README.md). A full Draft 2020-12 validator may also be used.
