# Observation delivery after settlement

This document defines observation delivery in the current draft. Observations do not carry a generic disposition field.

Resolve the serial interception pipeline before dispatching observations. The
`hooks/observe` notification contains `protocolVersion` and the
resulting effective `event`, with the same logical `event.id` and `source`.
There is no generic disposition, decision summary, response receipt, downgrade
flag, or `/view` fallback.

When decision-making short-circuits, each remaining uncalled matching intercept
subscription receives this same boundary through `hooks/observe`. Use its existing
content permissions and selections: downgrade grants no additional access.
Already-called interceptors get no automatic second copy. Explicit observation
subscriptions still receive the final view, including when their backend also
owns a called intercept subscription. Track invocation by subscription, not backend.

The host's settlement controller supplies only accepted effective content. Pending
or discarded modifications must not leak into observations; interruption preserves
already-accepted changes but immediately ends pending decision requests. Scheduling
or processing best-effort observers must not delay interruption or keep interrupted
execution alive. Observers may run in any order and cannot return effects or reopen
the decision. Selected authorized uploads must finish before each notification;
failed observation transfer never reopens interception.

An observed `tool.before` remains a proposal, not evidence of execution. Resolved
invocation outcomes, including denial, belong to `tool.after`; other completion
and failure boundaries retain their own semantics. There is no invented arbitration
or summary for combined internal decisions such as denial and stop.

Observation establishes neither model consumption nor later dispatch. Delivery is
one-way and best effort, without acknowledgement, replay, receipt, persistence, or
session-wide arrival-order guarantees. Logical item identity remains stable across
permission-filtered subscription views.
