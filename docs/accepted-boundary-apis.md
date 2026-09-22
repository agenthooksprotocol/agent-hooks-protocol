# Draft elicitation, observation, and compaction APIs

These pre-release APIs implement the mutable draft’s
[elicitation](../spec/draft/interaction-payloads.md),
[observation](../spec/draft/observation-disposition.md), and
[compaction](../spec/draft/capability-auth.md#event-semantic-capability-bounds)
boundaries. Generated models/codecs preserve structure; canonical validation and
trusted host configuration remain necessary.

| SDK | Elicitation exchange / effect staging | Compaction |
| --- | --- | --- |
| Python | `agent_hooks_protocol.elicitation.validate_exchange`, `apply_effects` | `agent_hooks_protocol.compaction.run_compaction` |
| TypeScript | `validateElicitationExchange`, `applyElicitationEffects` | `runCompaction` |
| Go | `interop.ValidateElicitationExchange`, `interop.ApplyElicitationEffects` | `interop.RunCompaction` |
| Rust | `elicitation::validate_exchange`, `elicitation::apply_effects` | `compaction::run_compaction`, `run_compaction_observed` |

## Elicitation

Supply canonical request/result envelopes, an authorized immutable-body resolver,
a canonical validator, and the authenticated supplier identity. Do not derive
that identity from event fields. Validators enforce parent request, source and
session correlation before resolving content or staging effects. Receiver pending
state uses source-scoped request identity, not one slot per session.

Body-selected request/result descriptors refer to complete unchanged MCP
2025-11-25 params/ElicitResult JSON. Metadata/omit views do not resolve or upload
a body. Requested body gaps fail closed in these helpers. Form data is checked
against requestedSchema; defaults remain annotations and are not injected.
`validate_mode` / `validateElicitationMode` / `ValidateElicitationMode` enforce
independent optional form/URL support. Only an explicit MCP-origin translation
interprets legacy empty capabilities as form support.

Effect application validates the complete effect list and request-local grants
before publishing a candidate. Request return/deny and result content replace/merge
are distinct operations; failed lists leave the input unchanged. A supplied
result carries authenticated hook provenance, never fabricated human provenance.
URL accept does not mean external success or trigger a completion event.
After accepted changes, upload new complete JSON bytes and construct new immutable
references before delivering body-selected content to the next subscriber.

## Compaction

Configure ordered hooks with supplier identity, failure policy and callbacks,
plus a real host generator (tests use a deterministic synthetic generator).
Before hooks can replace instructions; after hooks can replace the summary.
Capabilities advertise only implemented operations. Whole-list staging preserves
earlier accepted changes and prevents leakage from a rejected compound response.
A later changed input invalidates an earlier supplied summary. A return bound
to the effective input skips generation but still runs all after controls.

The returned summary handles are internal runtime handles, not canonical wire
descriptors. Preserve their logical item ID; upload exact bytes and produce
subscription-scoped ContentItems before wire dispatch. Changed bytes require a
new reference. The canonical compaction wire fixture demonstrates that binding.

Observation-only callbacks have no effect or failure-policy authority and do not
gate downstream settlement. They receive isolated snapshots. Rust detached
observers must be owned `CompactionObserver` callbacks passed to
`run_compaction_observed`; borrowed callbacks cannot be detached safely.

## Observation dispatch APIs

| SDK | Entry point / scheduling |
| --- | --- |
| Python | `lifecycle.dispatch_observations`, daemon workers |
| TypeScript | `draft-runtime.dispatchObservations`, host-supplied scheduler; `interop/settlement.mjs` supplies an unreferenced Node immediate scheduler |
| Go | `interop.DispatchObservations`, independent goroutines |
| Rust | `observation::dispatch_observations`, detached workers |

Supply an already-settled snapshot, matching subscriptions, and the IDs of
interceptors already called. The required `prepare` callback enforces host content
policy and confirms selected uploads; `notify` sends the one-way notification.
For a native boundary without interception, use an empty called set. The host owns
cancellation and shutdown of transport resources; stopping execution never joins
observer workers. Test-controller receipt rendezvous such as `/wait-observed` are
not protocol acknowledgements or production interruption gates.

### Chain integration

All four lifecycle adapters invoke chain dispatch from acquired and validated
responses. Fail-open continues decisions; deny, flow stop, fail-closed and
interrupt end decisions and dispatch remaining uncalled intercept subscriptions
as `hooks/observe`. Already-called interceptors do not get a second copy absent
a separate explicit observe subscription. Per-subscription preparation applies
permissions, selection and upload confirmation before notification dispatch.
There is no disposition, downgrade flag, response receipt, or observation effect.

## Reproduction and limits

See [elicitation](../interop/ELICITATION.md),
[compaction](../interop/COMPACTION.md), and
[lifecycle](../interop/LIFECYCLE-MATRIX.md) for tests and exact scope. These
synthetic exchanges establish SDK behavior, not native harness event occurrence,
production trust federation, browser completion or durable content storage.

## Task lineage reference checker

Instantiate `interop/task_lineage.py`’s `TaskLineage` per receiving connection.
Use `accept_wire(bytes_or_text)` for full JSON-RPC frames or `accept(message)`
after strict JSON decoding. Do not combine unrelated sources or connections’
subscription-delivery retry state. `require_known_parents=True` is a producer-history
check, not a requirement for filtered subscribers. This isolated checker does not
add an adapter control protocol; task IDs do not establish delivery-byte equality
or capability enforcement. See the [lineage binding](../spec/draft/task-workspace-lineage.md).

## Codec and evidence limits

Generated codecs are loss-preserving structural parsers, not full validators.
Required-only properties enforce presence without injecting defaults; explicit
null is allowed only if all applicable schemas allow it. Known MCP transports use
exact structural dispatch; extension transports remain unknown variants requiring
canonical validation of their naming, location and gaps. The generator’s
`x-sdk-discriminator` annotation does not change JSON Schema validity or declare
native transport support.

Validate canonical schemas before runtime authorization and enforce cross-message
rules separately. URI/time format validity does not establish safe endpoints;
open envelopes and opaque native/input/output fields do not prove the absence of
credentials or duplicated model content. Synthetic tests do not establish native
content completeness, human approval, effective-destination reauthorization,
model admission, task commits, managed-policy enforcement, production identity or
delegation, replay, durability, or rollback. Independent HTTP upload-origin tests
do not establish HTTPS client-certificate noninheritance.
