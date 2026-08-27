# Agent Hooks Protocol v0.1

**Status:** Working Draft (`0.1.0-draft.1`)
**Canonical draft:** This document

This is the canonical, language-neutral Working Draft specification. It is not a final standard and must not be represented as stable. The Base Protocol, Server Features, and compatibility-adapter requirements are normative where they use BCP 14 terms; the abstract, rationale, open questions, implementation plan, and references are informative. JSON Schemas constrain representable JSON shape; this prose defines protocol semantics. A conflict between prose, schemas, requirements, and fixtures is a draft defect, not permission to choose whichever behavior is convenient.

This canonical draft retains the protocol rationale, every open question, and the proposed implementation plan. SDKs and implementation repositories, including the TypeScript SDK, are non-normative.

**Protocol version:** `0.1`
**Audience:** Agent-harness implementers, compatibility-adapter authors, and policy, security, approval, and runtime-middleware vendors

**Specification map**

- [Architecture](architecture.md)
- [Base Protocol](base/index.md)
  - [Transport bindings](base/transports/index.md)
- [Server Features](server/index.md)
- [Client Features](client/index.md)
- [Design rationale and references](rationale.md)
- [Open questions](open-questions.md)
- [Proposed implementation plan](implementation-plan.md)
- [Changelog](changelog.md)

## Abstract
The Agent Hooks Protocol (AHP) defines a vendor-neutral interface through which an agent harness can ask an external backend to inspect and control an impending runtime operation.
AHP v0.1 focuses on one portable control point: intercepting a tool call before execution and returning either no effect or a denial. The protocol standardizes the event envelope, capability declaration, effect semantics, interceptor ordering, timeout behavior, failure policy, stdio and HTTP bindings, and a portable registration format.
AHP is primarily a control-plane protocol. AHP provides an optional one-way notification mode for control-adjacent audit and compatibility use cases, but observation is not required for minimum v0.1 conformance.
## Status of this document
This document is a Working Draft specification, not a final standard. It deliberately resolves enough questions to support prototypes and conformance tests while leaving broader lifecycle control, mutation, approval UI, and telemetry mapping to later versions or optional profiles.
The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHALL**, **SHALL NOT**, **SHOULD**, **SHOULD NOT**, **RECOMMENDED**, **NOT RECOMMENDED**, **MAY**, and **OPTIONAL** are to be interpreted as described in [BCP 14](https://www.rfc-editor.org/info/bcp14) when, and only when, they appear in all capitals.
