# Execution payload encoding

This binding defines event payloads and execution/accounting semantics. The schemas are the canonical wire grammar.
Common envelope fields remain `id`, URI `source`, `type`, and timestamp `time`.
`trigger` names the session/turn initiation cause; `source` remains event identity.
Wire timestamps use RFC 3339 syntax and calendar constraints; source uses RFC 3986
absolute-URI grammar. Compact ISO timestamps, whitespace, invalid escapes and
bad IP literals are invalid. Format validation is not endpoint authorization.
Integer-valued JSON numbers such as `1.0` count as integers under Draft 2020-12.
Removed names are not additional standard events: `tool.error` (use
`tool.after.outcome`), `prompt.submitted`, `agent.stop`, `notification`,
`permission.request`, `compact.pre`, `compact.post`, `file.edited`, and `other`.
MCP inventory discovery is not required.

## Required event-specific payloads

An empty envelope is not a valid execution event. Additional optional fields and
conditional constraints are defined in the corresponding schema.

| Event | Required fields beyond the common envelope |
| --- | --- |
| `session.start` | `session`, `trigger`, `harness`, `permissionMode`, `manifest`, `items` |
| `session.end` | `session`, `outcome`, `reason` |
| `tool.before` | `call`, `tool`, `path` |
| `tool.after` | `outcome`, `execution`, `call`, `tool`, `path`, `items` |
| `turn.start` | `turn`, `trigger`, `items` |
| `turn.finish.before` | `turn`, `outcome`, `continuationCount`, `items` |
| `turn.end` | `turn`, `outcome`, `continuationCount`, `items` |
| `turn.progress` | `turn`, `item`, `delta`, `final` |
| `model.request.before` | `model`, `attempt`, `params`, `items` |
| `model.response.after` | `model`, `attempt`, `execution`, `items`, `finishReason` |
| `model.error` | `model`, `attempt`, `execution`, `error` |
| `model.switch.before` | `current`, `proposed`, `reason` |
| `model.switch.after` | `previous`, `current`, `reason` |
| `tool.permission.request` | `call`, `tool`, `path`, `suggestions`, `sandboxBypass` |
| `tool.permission.resolved` | `call`, `tool`, `path`, `decision`, `decidedBy` |
| `tool.progress` | `call`, `tool`, `path`, `partialOutput`, `backgrounded` |
| `tool.batch.after` | `batch`, `calls` |
| `context.compact.before` | `trigger`, `items` |
| `context.compact.after` | `summary`, `removed`, `execution` |

## Execution, calls, and model attempts

`call: {id}` identifies a tool invocation. `tool` requires verbatim `name`, parsed
object `input`, and `origin: native|mcp`; behavioral `kind` is optional. MCP origin
requires its self-contained `mcp` descriptor; native origin forbids it. MCP
connection transport is HTTP/SSE or stdio. Unavailable URL/command/args/cwd fields
use explicit field-path gaps rather than invented values. Descriptors never carry
credentials. Runtime validation must additionally sanitize addresses and arguments.
`path` identifies the covered tool path. `batch: {id, callIds}` lists unique sibling
call IDs. Correlation, batch membership, and one-completion guarantees are runtime
duties, not conclusions derived from validating individual messages.

Execution is either `{status: "executed"}`, or skipped with a reason.
`reason: "supplied_result"` requires `subscriptionId`; other reasons are `policy`,
`cancelled`, `timeout`, and `other` (optional `detail`). This is the only supplied
result spelling. Tool outcomes are `ok`, `error`, `denied`, `cancelled`, `timeout`.
Denial requires skipped/policy. Successful and failed supplied results retain
ordinary outcomes; neither establishes original execution. Error objects require
`class` and `message`, with optional code/status/native details. A supplied model
response or compaction summary does not fabricate provider usage or latency.

Models use `{id, provider}`. Provider attempts use `{id, number}` with a positive
attempt number. Provider-native request `params`, tool input, and native permission
`suggestions` remain intentionally opaque objects, not portable AHP languages.
Optional model-switch `pricing` has ISO-style uppercase three-letter `currency`
and nonnegative `inputPerMillionTokens` / `outputPerMillionTokens`; absent rates
are unknown. Pricing is not billed cost.

## Content and accounting

Model-bound bytes use normalized content items and the binary upload binding;
progress deltas, partial outputs, and compaction summaries are descriptors, not
inline strings. Common item arrays may be empty for legitimately absent or omitted
content; adapters MUST NOT use emptiness to conceal known authorized content.

Usage carries `kind: amount|total`, `scope: attempt|turn`, completeness
`complete|partial|unknown`, and provenance `provider|estimate|mixed`. Attempt usage
is an amount with provider/estimate provenance; turn usage is an aggregate total
and may be mixed. Nonnegative input/output/cache-read/cache-write token counts are
optional: missing is unknown, not zero. Optional cost requires nonnegative amount,
three-letter currency, and basis `billed|reported|estimated`.

Publish at most one finalized amount per provider attempt. Deduplicate delivery
retries and subscription views. Never add a turn total to its constituent amounts.
Failed attempts can have usage without a fabricated success. A model error is
observation, not a retry instruction. Retry/fallback scheduling remains host-owned.

## Enforcement limits

JSON Schema validates individual wire objects, not actual execution, final
settlement, upload authorization, content completeness, cross-message ID equality,
acyclic ancestry, aggregate accounting, native effect support, or delivery order.
The static manifest and per-request capabilities MUST describe actual enforceable
coverage. Generated codecs preserve JSON and are not canonical validators.

## Identity and native boundary duties

Event identity is source-scoped. Optional sessions on ordinary tool and catalogue
events do not justify fabricated sessions or turns for detached work. Session
resumption retains identity; fork/clear origins use known links. Calls retain
identity across before, permission, progress and completion; delegated calls have
their own IDs. Event, session, turn, call, task, attempt/batch and content identities
accept a boolean `synthesized` marker: synthesized identities carry true and stay
stable for the entity’s lifetime. Parentage follows the [lineage binding](task-workspace-lineage.md).

Retries preserve the exact subscription payload and identities; they are not new
logical occurrences or provider attempts. Subscription-side-effect deduplication
is distinct from logical occurrence identity. Separate explicit observe and
intercept subscriptions on one backend are not duplicate deliveries. New provider
attempts have distinct identities. No retry rule promises replay or durability.

Modified items retain logical identity while changed bytes require a new reference;
new injections and newly created summaries receive new item identities. Startup
instructions belong to `session.start`; expanded prompts and attachments belong
to `turn.start`. Explicit skills and file reads use ordinary tools; implicit loads
belong to the responsible lifecycle boundary. Report actual reads without reading
additional files merely to report them. Reasoning stays attached to its owning
assistant message and distinguishable from answers and summaries; provider-private
reasoning, full transcripts and streaming are not required. Injection requests do
not prove admission, sending or model consumption.

`tool.before` precedes invocation side effects and the permission stage. One
`tool.after` represents a resolved call, whether executed, denied or supplied;
error outcomes require event-level error and other outcomes exclude it. Output
and error cannot coexist. `tool.batch.after` is one aggregate boundary after its
calls resolve, not generic multi-event framing. Background launch completion and
later delegated work are distinct. `turn.finish.before` is not `turn.end`; a turn
ends only on real completion. Continuation into a new turn links the known
requesting parent. Selected-model switches are not every per-call model choice;
refusing a switch does not guarantee the previous model remains usable.

Tool kind is descriptive, not origin or authority; absent or unfamiliar kinds must
not block processing or authorization. MCP descriptors preserve exact tool and
server identity and runtime-selected destinations, not inferred name prefixes,
gateway upstreams or cached inventories. Remove credentials, URL userinfo,
headers/cookies and secret environment/argument values with honest gaps. Any
retargeting requires effective-destination reauthorization.

Turn accounting includes known retries without double-counting. Supplied/skipped
provider work does not fabricate usage, latency or file side effects. Errors do not
promise another attempt. These are producer duties, not facts a receiver can infer
from well-formed telemetry.
