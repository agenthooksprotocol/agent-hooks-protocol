# Agent Hooks Protocol

**Status:** Working Draft (`draft`)
**Canonical draft:** This document

This is the canonical, language-neutral Working Draft specification. It is not a final standard and must not be represented as stable. The Base Protocol, Server Features, and compatibility-adapter requirements are normative where they use BCP 14 terms; the abstract, rationale, open questions, and references are informative. JSON Schemas constrain representable JSON shape; this prose defines protocol semantics. A conflict between prose, schemas, requirements, and fixtures is a specification defect, not permission to choose whichever behavior is convenient.

**Protocol version:** `draft`
**Audience:** Agent-harness implementers, compatibility-adapter authors, and policy, security, approval, and runtime-middleware vendors

**Specification map**

- [Architecture](architecture.md)
- [Base Protocol](base/index.md)
  - [Transport bindings](base/transports/index.md)
- [Server Features](server/index.md)
- [Client Features](client/index.md)
- [Execution payloads](execution-payloads.md)
- [Interaction and MCP elicitation payloads](interaction-payloads.md)
- [Task, workspace, and lineage semantics](task-workspace-lineage.md)
- [Observation delivery](observation-disposition.md)
- [Content uploads](content-upload.md)
- [Capabilities, registration, and authentication](capability-auth.md)
- [Design rationale and references](rationale.md)
- [Open questions](open-questions.md)
- [Changelog](changelog.md)

## Abstract
The Agent Hooks Protocol (AHP) defines a vendor-neutral interface through which an agent harness can ask an external backend to inspect and control an impending runtime operation.
This draft defines capability-advertised lifecycle and operation boundaries, atomic compound effects, content delivery, and registration over stdio and HTTP. Implementations advertise the events, modes, targets, and operations they can faithfully support; the schema catalogue does not require every harness to implement every boundary.
AHP is primarily a control-plane protocol. AHP provides an optional one-way notification mode for control-adjacent audit and compatibility use cases, but observation is not required for minimum conformance to this protocol revision.
## Status of this document
The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHALL**, **SHALL NOT**, **SHOULD**, **SHOULD NOT**, **RECOMMENDED**, **NOT RECOMMENDED**, **MAY**, and **OPTIONAL** are to be interpreted as described in [BCP 14](https://www.rfc-editor.org/info/bcp14) when, and only when, they appear in all capitals.
