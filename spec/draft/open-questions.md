# Open questions

## Open questions for draft review
1. Is the coarse `tool.kind` taxonomy useful and stable enough to require, or should it be optional until a separate tool-projection profile exists?
2. Should globally unique `event.id` be required, or should uniqueness be defined as the CloudEvents-style pair `(source, id)`?
3. Should HTTP notification success require `202`, permit `204`, or allow any successful 2xx status?
4. Should `timeoutMs` have a normative maximum rather than only a recommended supported range?
5. Should persistent stdio permit multiple outstanding requests in a later profile?
6. Is reverse-DNS naming sufficient for extensions and policy codes?
7. Should `native` live in the core envelope or be defined entirely as an extension?
8. Does a denied tool call need a separate optional `tool.denied` observation event?
9. Which fields need explicit maximum sizes in the first conformance schema?
10. Should authentication and registration remain in the same document as core protocol semantics or become separate bindings?
11. Which optional effect should be standardized next: `ask`, `replace_tool_input`, or `add_context`?
