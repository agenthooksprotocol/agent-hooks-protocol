# Typed payload contracts

Known protocol subsections have named schemas and generated wire types. The
request event, effect target, and operation select the value contract; there is
no additional wire discriminator. A caller selects a request-correlated
`*InterceptResponse` schema from `intercept-response.schema.json` after matching
the pending JSON-RPC request. The standalone `Effect` schema is not an authority
or a substitute for this contextual validation and the advertised capabilities.

## Generated consumer surfaces

Generated consumer facades expose schema-owned models, validators, domain
namespaces, and content-aware input types. Raw wire models remain available for
advanced integrations. Content-aware inputs project to those canonical models;
they do not define a second wire contract.

Attachment ownership and source extraction are runtime assembly concerns.
Projection plans, source paths, extraction helpers, and assembly macros are not
normal consumer APIs. Runtime integrations use dedicated internal seams rather
than discovering attachment ownership through arbitrary caller methods. Public
facades do not re-export internal assembly symbols through wildcard exports.

This separation does not change JSON-only parsing, validation, opaque extension
properties, caller-owned generic contracts, attachment lifecycle, or scheduling.

## Modification values

| Target and boundary | Value contract |
| --- | --- |
| `prompt`, `request`, `response`, `output` | `CanonicalMessages` |
| `content` at `user.message.outbound` | `CanonicalMessages` |
| `instructions`, `summary` | `TextParts` |
| `workspace` at `workspace.change.before` | `WorkspaceChange` (`cwd`, `workspaceRoots`) |
| `content` at `user.elicitation.result` | `FormAnswers`, further validated against the requested form |
| `input` at `tool.before` | The tool-owned declared input object contract |

Workspace replacement and shallow merge accept a nonempty fragment of the known
workspace contract. Canonical list merge appends in order and preserves duplicates.
Object merge is shallow: literal null is a value, never a deletion directive,
and each nested value replaces the entire nested value. A generic SDK can use a
temporary JSON object to perform a declared object merge, but MUST deserialize
and validate the merged value against the declared contract before publishing
any part of the response. This does not permit recursive JSON edits.

## Return and pending candidates

| Boundary | `return.value` and non-null `state.candidate.value` |
| --- | --- |
| `model.request.before` | `CanonicalMessages` |
| `context.compact.before` | `TextParts` |
| `user.elicitation.request` | The complete MCP `ElicitResult` with the same answer contract |
| `tool.before` | The declared tool result contract |

These are the only return boundaries. A pending candidate at another boundary
is an opaque, caller-declared operation result; its presence does not authorize
`return` there. `candidate: null` means no result is pending. A candidate whose
`value` is null is a present result and is valid when the declared tool result
contract accepts null. Optional provenance is caller-owned opaque metadata;
it does not establish identity, authorization, or authenticated claims.

`state.injections` contains full `InjectEffect` objects, including `type`,
`target`, `operation`, `deliverAt`, and canonical message `value`. The list
retains order and scheduling. Its presence is not proof that delivery occurred.

## Forms and caller-owned contracts

The canonical form fallback has `requestedSchema.properties` values typed as
`PrimitiveSchemaDefinition`, and `ElicitResult.content` typed as `FormAnswers`:
a dictionary of strings, numbers, booleans, or lists of strings. The caller's
single answer type `T` determines both the restricted MCP requested schema and
the decoded answer type. An explicit form contract can derive or accept the
schema and validate answers as `T`; unsupported MCP form shapes MUST be rejected,
not approximated. Runtime adapters must use generated canonical protocol models,
not independently copied wire shapes.

Tool inputs/results, provider parameters, task change/prior state, native
permission rules, discovery descriptors, and native/extension metadata can use
a declared caller codec or schema. When no contract is declared, forward them
opaquely. Unknown properties, including unknown properties on objects with a
closed canonical schema, remain opaque in generated codecs for forward
compatibility; canonical validation still governs their acceptance. Do not
interpret, traverse, or reshape opaque values. Known schema-valued dictionary
entries are typed and validated, not treated as untyped extension bags.

## Neutral responses and host counters

An omitted `result.effects` and `result.effects: []` are both valid neutral
responses. Missing effects default to an empty list for composition, without
changing wire presence or adding an effect directive. Neither form grants
permission. Present effects retain their typed ordered-list contract.

Optional `session.counters` members are nonnegative integers:

* `turns`: completed host turns;
* `modelRequests`: actual provider attempts, including retries;
* `toolCalls`: actual tool executions, including failures;
* `inputTokens` and `outputTokens`: provider-reported totals, when known.

Additional counters obey the same nonnegative integer contract. The host
supplies counters; SDKs do not infer them. Unknown or omitted differs from zero.
No schema maximum is imposed. Codecs must preserve integer fidelity and reject
lossy conversions rather than round values.
