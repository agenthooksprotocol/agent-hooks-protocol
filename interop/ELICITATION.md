# Offline MCP elicitation wire matrix

The pinned MCP 2025-11-25 request params and result are complete, unchanged JSON
bodies in ahead-of-time §4 uploads. Normalized AHP metadata contains `server`,
`mode`, `request` / `result` references and result `action`. Bodies are never
renamed to `schema`, flattened, or replaced by arrays of content descriptors.

## Run

From the workspace root (the parent of the five repositories):

```sh
(cd typescript-sdk && pnpm --filter @agenthooksprotocol/sdk build)
(cd go-sdk && go build -o /tmp/ahp-elicitation-go ./cmd/elicitation)
(cd rust-sdk && cargo build --bin elicitation)
python3 agent-hooks-protocol/interop/elicitation_matrix.py
(cd agent-hooks-protocol/interop && python3 -m unittest test_elicitation_matrix)
```

Python adapter uses `python-sdk/.venv/bin/python` and the installed `jsonschema`
package. All adapters read canonical schemas from the checkout. No network MCP
server, model, credentials, or LLM inference is needed.

## Evidence and limits

Each of the 16 directed language pairs starts a separate authenticated HTTP
receiver, executes the sender SDK's actual HTTP stack, uploads exact UTF-8 octets,
and transmits real `hooks/intercept` envelopes. The receiver itself validates the
canonical envelopes, complete MCP JSON bodies, metadata equality, ownership,
size, SHA-256, immutable references, and request/result pairing. Exact receiver
receipts include the received envelope, selected body bytes and decoded result.
The oracle holds expected outcomes; `/receipts` reads receiver evidence. Uploads are scoped to the isolated authenticated
subscription. Wrong tokens and wrong subscriptions are rejected.

The matrix covers explicit/default form mode, form accept/decline/cancel, URL
accept/decline/cancel, URL ID/address preservation, missing mode-specific fields,
invalid actions and content, submitted form data that fails required/type/enum/number constraints, forbidden URL/decline/cancel content, metadata
mismatches, missing uploads, bad digests and immutable references. Metadata/omit
cases for both modes prove zero per-scenario uploads and no raw prompt, schema,
URL, result text or body bytes in events/receipts. Selected gaps fail closed.
Upstream default annotations are preserved without being applied or tested as
submitted data (including a fractional integer-property default and an enum
default outside the listed options). URL `accept`
never reports external completion. The authenticated principal comes from the
receiver's out-of-band bearer configuration, not asserted event/source/server
fields. Result binding additionally requires an explicit `parentEventId`
equal to the request event ID, the same event source, and matching session-ID
scope, checked before resolving bodies or staging result effects. Pending
requests are indexed by `(source, request event ID)`, never session alone.
Duplicate live identities are rejected without replacement. Failed result
correlation does not consume a pending request. The wire grid includes all scope
mismatches for body/metadata/omit and two requests outstanding in the same
session, settled in reverse order with distinct form schemas. Unmodified results are labelled `mcp`, **not verified human**.

The separate offline `check` entry point tests all four SDK helpers for canonical
return/deny/modify effects, per-request grants, modify target/operation grants,
and trusted hook provenance. The atomic application helper stages
before-request `return` / `deny` and result content replace/merge on snapshots.
Only a fully validated final MCP/form result is returned for publication. Invalid
later effects, ungranted operations, conflicting terminal effects, or invalid
submitted form data reject the whole list with no partial result. Inputs and
uploaded bytes remain unchanged; callers upload the complete returned JSON before
emitting a body-selected result event. Metadata/omit cannot authorize execution.
The HTTP matrix also executes the sender SDK's atomic helper, independently
checks its output, then uploads and delivers that actual staged output to every
receiver SDK. Before-return, before-deny, result replace/merge and URL return/deny
are published through the wire matrix. Failed atomic lists publish no result.

Form/url registration is optional and independent. Per-request mode validation
requires the corresponding advertised mode. An absent capability supports neither;
AHP `{}` also supports neither. Only explicit MCP-origin legacy `{}` normalizes to
`{form:{}}`; it never grants URL support or leaks into AHP registration.

The host-provided canonical validator callback also handles `form-answer` values
`{schema, value}` with a standards-compliant JSON Schema validator. This validates
submitted values against the original `requestedSchema` without altering schema
annotations/defaults or coercing submitted values. Body-selected form accepts require this validation. Metadata/omit receipts explicitly mark body
validation as `not-selected`; they do not claim to validate unavailable bytes.

This matrix covers HTTP bearer. Coverage excludes OAuth/workload/mTLS,
stdio, browser/OAuth completion, host UI, cancellation races, and production
server hardening. MCP structural and submitted form-schema validation are enforced on selected
bodies. The shared upload and lifecycle matrices cover
other transport/authentication and scheduling behaviors independently.

## Upstream provenance

`upstream/mcp/2025-11-25/pin.json` records version-tagged official TypeScript and
JSON Schema URLs and full-file SHA-256 digests. `tools/mcp_elicitation.py` verifies
deterministic extraction offline; explicit `--refresh` downloads that pinned
version for reviewed updates, not automatic tracking of future MCP revisions.
Extraction preserves primitive and enum variants and default types, restoring
TypeScript numeric types where the upstream JSON generator narrowed numbers.
The published requested-schema vocabulary is intentionally closed, unlike
upstream extension-open objects; MCP params/results remain open. See the
[interaction payload specification](../spec/draft/interaction-payloads.md) and
[public boundary APIs](../docs/accepted-boundary-apis.md).
