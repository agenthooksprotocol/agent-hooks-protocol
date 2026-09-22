# Actual cross-language matrix

Run from any directory (Python standard library only):

```sh
python3 agent-hooks-protocol/interop/matrix.py --output agent-hooks-protocol/interop/matrix-results.json
(cd agent-hooks-protocol/interop && python3 -m unittest test_matrix -v)
```

SDK build/runtime prerequisites remain those of each `interop/adapter.json`.
The runner discovers sibling SDK manifests, executes commands in SDK working
directories, and appends `--config`. Optional `--clients python rust` and
`--servers go typescript` restrict discovery. `--jobs` bounds concurrent pair
groups; `--timeout` bounds each readiness/client wait (default 120 seconds).
Independent language pairs run concurrently; combinations within a pair run
sequentially. No protocol effect application occurs in the Python controller.

## Coverage and counts

Four clients × four servers × two transports × five auth labels produce
160 combination records. HTTP runs all five modes (`none`, `bearer`, `oauth`,
`mtls`, `workload`); stdio runs process trust (`none`) only. The other 64
stdio combinations are explicit **inapplicable**, never passed or executed.
Scenario execution totals depend on the generated scenario set; read the report
for executed and inapplicable counts rather than treating grid dimensions as passes.
HTTP OAuth uses the shared synthetic issuer; mTLS uses shared test certificate
fixtures; workload uses the shared signed assertion configuration. This is
local synthetic trust, not production federation. Authentication rejection
cases are outside this positive matrix and require the separate auth suite.

## Integrity and lifecycle

Each report must identify the correct client language and contain every scenario
ID exactly once, with no unknown IDs. Every expected key is independently
compared against actual results; arrays/order and nested objects are exact,
while unasserted top-level result fields are permitted. JSON booleans are not
numbers. An adapter claiming `passed` with the wrong result fails. Unsupported,
inapplicable, missing, and failed adapter results never pass applicable rows.
Nonzero process exits, missing reports, malformed JSON, receipt failures,
watchdog expiration, startup failure, or cleanup failure fail a combination.

Control receipts must contain each scenario ID exactly once and the canonical
`hooks/intercept` method. Full-envelope receipts are compared against the exact
request; compact receipts retain the adapter's own receipt-level visibility.
The runner does not invent full request evidence for compact receipts. If an
`accepted` field is present it must be boolean `true`; explicit rejection is
never receipt evidence. Omitting that field is permitted by the contract.
Expected rejection evidence supports the existing adapters' `actual: null` or
`actual: {"rejected": true}` forms, together with a passed report and receipt.
These attestations do not independently prove rollback or the rejection stage;
SDK-local invariant tests remain required. A dishonest adapter fabricating both
report and receipts cannot be detected by this control contract alone.

HTTP servers run in matrix-owned process groups. Stdio clients launch a
transparent runner relay, which launches the actual selected server in that
client-owned process group (some adapters create a nested group, which the
relay registers for explicit matrix cleanup). The relay forwards request/response bytes unchanged, without
an extra JSON-RPC envelope. It snapshots the control receipts **before forwarding
the final intercept response**, so client shutdown cannot race receipt capture.
Capability discovery requests do not increment the scenario counter. All groups
are terminated and then killed during cleanup, including surviving descendants
of an exited launcher. Readiness polls an atomic readiness file and verifies the
control health endpoint; no sleeps order scenario execution or race tests.

## Artifact

The JSON artifact contains sorted combination records with client, server,
transport, auth, status, errors and per-scenario expected/actual/status data.
Ordering and fields are deterministic; no timing, temporary path, endpoint,
credential or raw adapter logs are included. Summary counts separately enumerate
passed, failed and inapplicable combinations/scenarios. Any combination-level
integrity, receipt, exit, or cleanup error conservatively marks every scenario in
that combination failed, even when its reported actual matches expected. Actual
values and adapter statuses are retained for diagnosis. Summary passes therefore
count only independently verified rows from integrity-clean combinations. The command exits nonzero if any
applicable combination fails. Output paths should be outside the repository
unless deliberately publishing an artifact.
