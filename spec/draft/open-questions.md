# Open questions

## Possible future revisions

These questions concern future revisions, not implementation choices in this draft.
Current normative requirements remain binding.
1. Is the coarse `tool.kind` taxonomy useful and stable enough to require, or should it be optional until a separate tool-projection profile exists?
2. Should globally unique `event.id` be required, or should uniqueness be defined as the CloudEvents-style pair `(source, id)`?
3. Should `timeoutMs` have a normative maximum rather than only a recommended supported range?
4. Should persistent stdio permit multiple outstanding requests in a later profile?
5. Is reverse-DNS naming sufficient for extensions and policy codes?
6. Should `native` live in the core envelope or be defined entirely as an extension?
7. Which fields need explicit maximum sizes in the first conformance schema?
8. Should authentication and registration remain in the same document as core protocol semantics or become separate bindings?
