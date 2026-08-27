# Architecture

## Problem
Current agent harnesses expose incompatible hook systems. They differ in event names, payload shapes, tool identities, process lifecycles, output codecs, timeouts, ordering, permission decisions, and failure behavior. Some use exit codes, some parse structured stdout, some invoke long-lived plugins, and some expose only fire-and-forget events.
As a result, an external policy backend cannot reliably answer a simple question—such as whether a tool call should execute—without provider-specific logic. A timeout may continue execution in one harness and stop it in another. An apparent `allow` response may mean “this hook has no objection,” “skip the host permission prompt,” or nothing at all.
AHP standardizes the boundary between a harness and a control backend. Compatibility adapters translate existing native hook dialects into AHP; conforming harnesses can implement AHP directly.
```text
Existing harness ─> compatibility adapter ─> AHP backend
Conforming harness ────────────────────────> AHP backend
```
## AHP at a glance
AHP is a small, vendor-neutral control-plane protocol that sits in an agent harness's runtime path. It gives the harness a standard way to ask external middleware for a bounded decision before an operation continues. The protocol standardizes the conversation between the harness and that middleware.
AHP creates an admission-control boundary for agent operations:
```text
Agent requests a tool call
            │
            ▼
Harness creates a canonical tool.before event
            │
            ▼
Configured AHP backends evaluate it in order
            │
            ├── deny ──> Harness stops the tool call
            │
            └── no effect from every backend
                         │
                         ▼
Harness applies its own permissions and sandbox
            │
            ▼
Tool executes
```
The harness remains the execution and security boundary. It owns the tool call, decides which capabilities to offer, enforces deadlines and failure policy, validates responses, and applies any effect. An AHP backend is external runtime middleware: for example, a policy engine, DLP scanner, approval service, or organization-specific security control. A compatibility adapter can translate an existing vendor hook API into the same AHP contract.
What we want to build is a small set of interoperable pieces:
- **A shared event model:** A canonical envelope and lifecycle events with stable event, session, and tool-call identity.
- **A decision protocol:** JSON-RPC methods for synchronous interception and optional one-way observation.
- **Explicit effects and capabilities:** A bounded response from the backend and an explicit list of effects the harness can enforce.
- **Predictable composition and failure behavior:** Deterministic backend ordering, deadlines, response validation, and fail-open or fail-closed behavior.
- **Portable bindings and registration:** stdio and HTTP transport rules plus a common way to configure backends and subscriptions.
- **Compatibility and conformance tooling:** Adapters for existing hook dialects and executable tests that verify equivalent behavior.
For the minimum control path defined by this protocol revision, a harness is about to execute a tool call and emits `tool.before` through `hooks/intercept`. The request says that `deny` is available. Each matching backend returns either no effect or one `deny` effect. No effect means that backend has no objection; it does not bypass the harness's own permission checks. A denial stops the tool call. If a backend times out, crashes, or returns an invalid response, the harness applies that backend's configured failure policy rather than treating the failure as a decision.
AHP also defines optional `hooks/observe` notifications for lifecycle events that do not require a response. This mode supports control-adjacent audit and compatibility use cases.
This is a starting point, not the full standard. Later profiles can add effects such as requesting approval, replacing tool input, or injecting context, but only when a harness can advertise and enforce those semantics consistently.
## Scope
### Goals
This protocol revision aims to:
- Provide portable interception of tool calls before execution.
- Define one mandatory and unambiguous control effect: `deny`.
- Distinguish an explicit denial from a hook transport or protocol failure.
- Let the harness state which effects are valid for each request.
- Specify deterministic composition of multiple interceptors.
- Support process-per-event and persistent stdio backends.
- Support bounded HTTP request-response backends.
- Preserve stable event, session, and tool-call identity.
- Permit provider-specific fidelity data without making it portable contract data.
- Make minimum conformance small and executable.
### Non-goals
This protocol revision does not:
- Replace OpenTelemetry or define metrics, logs, traces, exporters, or telemetry semantic conventions.
- Replace MCP, ACP, A2A, a tool protocol, or an agent-client protocol.
- Standardize prompts, model requests, token streams, or model responses.
- Define a universal tool-input schema.
- Replace the harness's built-in permission and sandbox systems.
- Define an effect that bypasses host permissions.
- Standardize tool-input mutation, output replacement, context injection, or user approval prompts.
- Guarantee durable audit delivery through `hooks/observe`.
- Require a daemon, service discovery system, streaming transport, or browser authorization flow.
- Standardize native provider quirks such as exit codes, environment variables, or provider-specific response objects.
## Relationship to adjacent standards
### OpenTelemetry
OpenTelemetry is the preferred observability standard. AHP does not prescribe whether or how harnesses emit metrics, logs, or traces; implementations that provide observability can use OpenTelemetry independently of AHP.
AHP is complementary because OpenTelemetry does not define a bounded request-response decision that pauses a tool call and applies a control effect. AHP implementations MAY carry W3C Trace Context in a namespaced extension for correlation. Core AHP conformance MUST NOT depend on OpenTelemetry support.
A future companion document MAY define an AHP-to-OpenTelemetry mapping for hook requests, denials, failures, and latency. Such a mapping is not part of this protocol revision.
### MCP, ACP, and A2A
- MCP connects agents or clients to tools, resources, and context providers.
- ACP connects editors or clients to coding agents.
- A2A connects agents to other agents.
- AHP connects an agent harness to external runtime-control middleware.
AHP must work when a harness runs directly, so it is not defined as an ACP extension. AHP may intercept calls to MCP tools, but it does not change the MCP tool protocol.
### Prior-art principles adopted by this protocol revision
This protocol revision adopts the following design guidance:
- From Kubernetes admission webhooks: distinguish an intentional rejection from a webhook failure, and configure explicit failure behavior.
- From CloudEvents: use stable event identity, typed events, timestamps, extensibility, and duplicate-aware delivery.
- From OpenFeature hooks: make hook order and lifecycle timing normative.
- From MCP transports: use UTF-8 JSON-RPC over newline-delimited stdio and keep protocol output separate from diagnostics.
- From ACP: use JSON-RPC requests for operations that expect decisions and notifications for one-way events.
- From existing cross-harness adapters: retain the native tool name and payload while exposing a small normalized projection and explicit capabilities.
