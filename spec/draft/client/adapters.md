# Compatibility adapters

## 22. Compatibility-adapter requirements
An adapter translating a native hook system into AHP MUST:
- Hide provider-specific exit codes and response shapes from the AHP backend.
- Preserve the native tool name.
- Parse tool input into a JSON object.
- Assign a v0.1 `kind` or use `other`.
- Synthesize stable call IDs when the provider omits them.
- Advertise only effects the current native event can enforce.
- Translate `deny` into the provider's actual blocking mechanism.
- Apply configured failure policy honestly.
- Never claim fail-closed enforcement for an event the provider cannot block.
- Keep provider-specific fields in `native` or namespaced extensions.
An adapter MUST NOT silently discard an unsupported effect. If the native harness cannot enforce `deny` for `tool.before`, the adapter does not conform to the Tool Interception profile for that path.
