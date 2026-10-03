# Design rationale and references

## Design rationale
### Why is `deny` the minimum tool control?
Denial is the smallest broadly meaningful tool-control capability. Mutation,
approval, supplied results, and continuation require event-specific grants that
reflect what the harness can actually enforce.
### Why per-request capabilities?
Capabilities make requests self-describing and prevent backends from inferring
support from a provider name or the size of the event catalogue.
### Why distinguish no effect from `allow`?
An empty result requests no change. An advertised `allow` can suppress an ordinary
prompt, but cannot override deny, ask, managed policy, or access restrictions.
### Why JSON-RPC?
JSON-RPC already provides request, response, error, notification, and correlation semantics. MCP and ACP demonstrate that it can be used across local and remote agent transports. AHP needs only a bounded subset and does not copy MCP's streaming HTTP behavior.
### Why ordered serial composition?
Control decisions must be deterministic. Serial order is easy to explain and test. It avoids races between denials and lets each backend see the prior backend's accepted changes.
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
