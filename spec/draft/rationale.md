# Design rationale and references

## Design rationale
### Why only `deny`?
Cross-harness research shows that denial is the smallest broadly meaningful control capability. Approval prompts, forced allow, input mutation, output replacement, context injection, and agent continuation differ significantly across harnesses.
Starting with one effect makes conformance meaningful. It also prevents the first release from standardizing ambiguous semantics merely because similar provider fields exist.
### Why capabilities when this protocol revision has one effect?
Capabilities make requests self-describing and establish the evolution mechanism needed by heterogeneous harnesses. They also prevent a backend from assuming behavior from a provider name.
The small redundancy in this protocol revision is preferable to adding provider detection or an incompatible handshake later.
### Why no `allow`?
Existing systems use “allow” for incompatible concepts. An empty result cleanly means “this backend does not deny,” while the harness remains responsible for all other authorization.
### Why JSON-RPC?
JSON-RPC already provides request, response, error, notification, and correlation semantics. MCP and ACP demonstrate that it can be used across local and remote agent transports. AHP needs only a bounded subset and does not copy MCP's streaming HTTP behavior.
### Why ordered serial composition?
Control decisions must be deterministic. Serial order is easy to explain and test. It avoids races between denials and prepares for future mutation effects, where each backend may need to see the prior backend's accepted changes.
### Why is observe optional?
Many harnesses already support OpenTelemetry, and telemetry should converge there. AHP observe mode remains useful for compatibility and control-adjacent audit, but making it part of minimum conformance would blur the protocol's purpose and create a competing event-export path.
### Why keep native payloads?
Adapters need lossless diagnostics when providers drift or expose events that the portable model does not represent. Making native data optional and off by default preserves that escape hatch without allowing it to become the contract.
## References
- [Speakeasy ](https://github.com/speakeasy-api/agenthooks/blob/main/DESIGN.md)[`agenthooks`](https://github.com/speakeasy-api/agenthooks/blob/main/DESIGN.md)[ design](https://github.com/speakeasy-api/agenthooks/blob/main/DESIGN.md)
- [DeepSeek shared Claude Code and Codex hook-protocol note](https://github.com/deepseek-ai/deepseek-harness/blob/master/.agents/notes/implemented/feature/2026-06-30-hook-protocol-lib.md)
- [JSON-RPC 2.0 specification](https://www.jsonrpc.org/specification)
- [Model Context Protocol: transports](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
- [Model Context Protocol: authorization](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)
- [Agent Client Protocol overview](https://github.com/agentclientprotocol/agent-client-protocol/blob/main/docs/protocol/v2/overview.mdx)
- [Kubernetes dynamic admission control](https://kubernetes.io/docs/reference/access-authn-authz/extensible-admission-controllers/)
- [CloudEvents specification](https://github.com/cloudevents/spec/blob/main/cloudevents/spec.md)
- [OpenFeature hooks](https://openfeature.dev/specification/sections/hooks/)
- [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)
- [W3C Trace Context](https://www.w3.org/TR/trace-context/)
- [BCP 14 / RFC 2119 and RFC 8174](https://www.rfc-editor.org/info/bcp14)
