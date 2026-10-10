# Composition

## Composition
### Interceptor order

<a id="AHP-COMP-001"></a>
**AHP-COMP-001 — MUST.** Matching interceptors run serially in deterministic registration order and decision-making stops on denial, flow stop, interruption, or a fail-closed operational failure.

The portable registration document uses an ordered `hooks` array. For a given event, the harness MUST evaluate matching interceptors serially in array order.
For each interceptor:
1. The harness sends `hooks/intercept`.
2. If the result has no effects, the harness proceeds to the next interceptor.
3. After whole-response validation and staging, an accepted `deny` refuses the operation and an accepted flow stop ends decision-making.
4. If an operational failure occurs under `fail-open`, the harness records the failure locally and proceeds to the next interceptor.
5. If an operational failure occurs under `fail-closed`, the harness stops the chain and denies the operation.
This order is normative. Harnesses MUST NOT run interceptors concurrently in this protocol revision.
### Observers
Observer delivery MUST NOT participate in decision-making or delay settlement of the interceptor chain. Delivery after settlement MAY delay the return of the enclosing hook call, and therefore execution by a caller awaiting that call. A harness MAY dispatch observer notifications concurrently within that operation.

A hook call owns the deliveries it initiates. Normal completion means each owned delivery has completed or reached an explicit bounded failure outcome; implementations MUST NOT leave detached observer deliveries running after that completion. A host MAY run the entire call as a host-owned task, but MUST obtain the interception decision and satisfy any approval requirement before executing the gated operation. Observations cannot reopen a settled decision. Interruption stops new deliveries and cancels or safely retires pending I/O; it MUST NOT initiate further best-effort observations or wait for ordinary observer processing. Bounded cleanup needed to drain or retire I/O and reap owned processes is distinct from observer processing.
Dispatch observations after settlement using only accepted effective content. Remaining uncalled matching intercept subscriptions also receive `hooks/observe`; already-called interceptors receive no automatic second copy. See [observation delivery](../observation-disposition.md). Backends that make control decisions are responsible for exporting their own durable decision audit, preferably through OpenTelemetry or another dedicated audit pipeline.
### Overlapping subscriptions
Distinct matching subscriptions on the same backend remain independent. Track invocation by subscription ID, not backend ID; an explicit observe subscription is not deduplicated against interception.

## Atomic effect acceptance

<a id="AHP-EDIT-001"></a>
**AHP-EDIT-001 — MUST.** Within the exact advertised target and operation authority, list replacement substitutes the whole list, list merge appends in order without deduplication, and object merge is shallow only when the target and supplied value are objects; accepted modifications chain serially with whole-response atomic acceptance.

`result.effects` is ordered. Parse, validate and stage the whole response before
publishing any candidate, pending-state change, prompt, message or injection.
Unsupported effects, targets, operations, invalid values and exhausted continuation
allowances reject the entire response; do not salvage even a valid deny from an
invalid list. Apply the subscription failure policy once. Earlier accepted
responses survive a later fail-open rejection. Atomic acceptance does not roll
back already executed external work.

Modifications chain serially over the current effective value: each later
interceptor receives the accepted effective canonical content, not the original
content or an unaccepted candidate. For message lists and text-part lists,
`replace` substitutes the entire target list exactly and `merge` appends the
supplied entries in order, without deduplication or merging entries by identity.
Repeated entries remain repeated. Replacement does not preserve omitted entries
or attachment slots. A text string is not an object or a structured merge target.
For actual object targets, replacement removes omitted fields; merge is shallow,
replaces nested values, and treats null as a literal rather than deletion. Both
the current target and supplied merge value MUST be objects for object merge.
Input and workspace values require objects. Every replacement and merge MUST
satisfy the target’s schema; serialized structures inside text and attachment
bytes MUST NOT be interpreted as object targets, except for the explicitly
bound MCP elicitation answer field described below. Message values MAY contain
unchanged immutable attachment references only after runtime validation of their
existing identity and authorization. No effect may edit referenced bytes or
masquerade new text as an attachment. Removing or reordering a message does not
mutate its attachment. Text-part-list targets reject attachment parts.
A whole-list replacement MUST NOT be advertised or accepted when the target
contains text withheld by metadata/omit selection or a content gap. Changing IDs
does not grant authority to replace hidden text. Append merge preserves existing
entries, but incoming parts still require their own category and write authorization.

`prompt` maps to `turn.start.items` or
`user.message.inbound.message.messages`; `request` maps to
`model.request.before.items`; `response` maps to `model.response.after.items` or
`turn.finish.before.items`; at `user.message.outbound`, `content` maps to
`message.messages`. These targets take canonical message lists, not arbitrary
provider-native JSON. At `user.elicitation.result`, `content` instead maps to the
parsed MCP result's `content` answer object (not the whole result, action, or
text-part wrapper). It requires an accepted form result and body selection;
replace substitutes that object and merge is shallow over its answer fields.
The effective answer MUST satisfy the pinned MCP content definition and the
requested form schema. Nested values are replaced whole, not recursively merged.
The standalone effect schema permits a message list or object for `content`
because it has no boundary context: hosts MUST reject objects at outbound
message boundaries and lists at elicitation result boundaries. `request` remains
the canonical model message-list target; `user.elicitation.request` does not
permit modification of request params. It permits deny, return, and message,
not `modify(request)`. No new modification authority is inferred from a text
part containing structured JSON. `instructions` maps only to
`context.compact.before.instructions` and `summary` to
`context.compact.after.summary`; both take text-part lists. `session.start`
permits injection, not instruction modification. `output` maps only to
`tool.after.items`, an ordered canonical message list. Replacement substitutes
the list; merge appends in order without deduplication. Objects, primitive JSON,
and bare part lists are invalid for this target. There is no standard native
`tool.output` property. Structured tool results are application-serialized text
in canonical message parts; they do not create an object-merge target.
Targets are input, output, prompt, request, response, content, instructions,
summary or workspace only where [capabilities](../capability-auth.md) permit them.
Workspace modification is not arbitrary native setting or task mutation. A native
merge-only interface cannot advertise replacement by silently approximating it.

A supplied model result uses canonical messages; a supplied compaction summary
uses a text-part list; a supplied tool result retains the actual JSON tool-result
value. Validate supplied values against the active boundary, not merely the
generic effect envelope.

Within one response, apply modifications before binding its supplied result to the
effective operation. A later changed operation invalidates an earlier candidate;
a later return replaces that candidate. A supplied result skips original execution,
not policy, approval or applicable post-result controls. Deny wins over ask and
allow; ask persists despite later allow. Deny, stop and fail-closed prevent a
candidate from bypassing the settled decision. No effect is not permission to run.

Authorization is bound to effective arguments and destination, not just call ID.
Relevant changes invalidate prior approval and require renewed authorization;
refuse the path if reauthorization is unavailable. Allow suppresses an ordinary
prompt, never managed/access policy. Supplied results cannot bypass applicable
pending access checks, even if an execution-only prompt is no longer necessary.
Trusted host authority must remain separate from untrusted request state.

Stop wins over continue and prevents pending execution and further model work;
it implies neither rollback, session closure nor cancellation of background work.
Multiple continuation instructions accumulate in order but request one step and
consume one allowance. Continuation is distinct from injection or task creation.
`flow.stop` has a required reason; `flow.continue` may carry an instruction.
`inject(context).value` is a canonical message list, not arbitrary JSON, and
appends context at `now` or `next_turn`, while `message` is user-facing
and diagnostics remain separate. Effect values travel in interception responses;
ordinary text stays inline in canonical content and uploaded references carry
only immutable non-text, non-JSON media attachments. Observations return no
effects and cannot introduce a callback channel.
