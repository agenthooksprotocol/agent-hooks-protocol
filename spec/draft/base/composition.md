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
Observer delivery MUST NOT delay the interceptor chain or tool execution. A harness MAY dispatch observer notifications concurrently.
Dispatch observations after settlement using only accepted effective content. Remaining uncalled matching intercept subscriptions also receive `hooks/observe`; already-called interceptors receive no automatic second copy. See [observation delivery](../observation-disposition.md). Backends that make control decisions are responsible for exporting their own durable decision audit, preferably through OpenTelemetry or another dedicated audit pipeline.
### Overlapping subscriptions
Distinct matching subscriptions on the same backend remain independent. Track invocation by subscription ID, not backend ID; an explicit observe subscription is not deduplicated against interception.

## Atomic effect acceptance

`result.effects` is ordered. Parse, validate and stage the whole response before
publishing any candidate, pending-state change, prompt, message or injection.
Unsupported effects, targets, operations, invalid values and exhausted continuation
allowances reject the entire response; do not salvage even a valid deny from an
invalid list. Apply the subscription failure policy once. Earlier accepted
responses survive a later fail-open rejection. Atomic acceptance does not roll
back already executed external work.

Modifications chain serially over the current effective value. Replace removes
omitted fields; merge is shallow, replaces nested values, and treats null as a
literal rather than deletion. Input replacement and every merge require objects;
other replacements accept arbitrary JSON subject to the target’s schema.
Targets are input, output, prompt, request, response, content, instructions,
summary or workspace only where [capabilities](../capability-auth.md) permit them.
Workspace modification is not arbitrary native setting or task mutation. A native
merge-only interface cannot advertise replacement by silently approximating it.

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
`inject` appends context at `now` or `next_turn`, while `message` is user-facing
and diagnostics remain separate. Effect values travel in interception responses;
uploaded references carry harness-to-subscriber bodies. Observations return no
effects and cannot introduce a callback channel.
