# Required SDK integration check

Configure branch protection (or a repository ruleset) to require the **SDK
integration** job from `.github/workflows/sdk-integration.yml`. It runs on every
pull request, pushes to `main`, manual dispatch, and `sdk-released` repository
dispatch events. Branch pushes do not duplicate the pull-request integration
run. There are no path filters or scheduled polls. Adding this file does not
itself change repository branch protection.

## What runs

The proposed spec checkout is `agent-hooks-protocol/`; `python-sdk/`,
`typescript-sdk/`, `go-sdk/`, and `rust-sdk/` are its siblings. GitHub checks out
the pull request's proposed merge tree, not the default branch's generator.

1. A single **Resolve SDK revisions** job snapshots all four SDK `main` heads
   through the GitHub API before the matrix starts. For `sdk-released` events,
   it resolves the latest stable releases instead (see below). Both shards use
   the same immutable outputs, never separate floating-ref lookups. Repository
   names, paths, and full 40-character SHAs are strictly validated. The runtime
   `sdk-revisions.json` is uploaded both by the resolver and in each shard's
   reports. The runner checks SDK checkout HEADs against that runtime manifest.
2. Set up Python 3.11, Node 20 with pnpm 10.18.3, Go 1.27, and Rust 1.88.0 with
   rustfmt. Install the Python harness's JSON Schema dependency.
3. Run `python3 tools/generate_sdk.py --all` from the proposed spec. This writes
   generated codecs, canonical schemas, and source locks into those siblings.
4. Create the Python SDK’s `.venv` (used by its adapter commands), install and test Python; install, build, and test the pnpm workspace; build,
   vet, and test Go; and build all Rust targets and run Rust tests with its
   committed lockfile. TypeScript uses `pnpm install --frozen-lockfile` and the
   repository's ordered workspace build, not guessed `dist` paths.
5. Run `python3 tools/run_sdk_integration.py --reports-dir PATH --jobs 4 --suite-group GROUP` in each of the disjoint `core` and `extended` shards. The aggregate **SDK integration** job requires both shards to succeed; local runs may omit the group to run all suites.
   The runner builds Go elicitation, compaction, and compaction-wire adapters
   from the sibling sources into a fresh temporary directory, exports that
   directory only to its test processes, and removes it on exit. Build failures
   stop matrix execution and are recorded in `summary.json` prerequisites and
   `build-go-*.log`; no global `/tmp/ahp-*` executable is reused. Standalone
   elicitation/compaction matrices use `go -C <sdk> run ./cmd/<adapter>` when no
   prepared directory is supplied. The runner also builds Rust's interop binary
   and selects it for the matrix's client/server commands, avoiding nested Cargo
   startup or build-lock contention inside the SDK's unchanged discovery deadline.
   Its build result is recorded in prerequisites and `build-rust-interop.log`.
   The runner discovers siblings relative to the spec, runs the cross-language
   matrices and harness unit suites, writes reports, aggregates failures, and
   must exit nonzero for missing adapters. It must not silently reduce the
   language matrix when an SDK is incomplete.
6. Always attempt to upload `reports/`, including revision metadata, generation
   output, SDK build/test logs, and runner reports. Retain artifacts for 14 days.

SDK steps and the runner use `!cancelled()` so one failing suite does not prevent
other suites from producing diagnostics. There is no `continue-on-error`; Bash
pipefail preserves failures through `tee`. A failing setup, generator, SDK build,
SDK test, missing adapter, or matrix keeps the job red. Cancellation stops further
work. Jobs have a 60-minute limit, individual expensive steps have limits, and
new runs cancel older runs for the same ref. Forced termination can prevent an
artifact upload from completing even though its step uses `always()`.

## Revision selection and release events

Pull requests, pushes to `main`, and manual runs use the latest SDK `main`
commits observed at the start of the resolver job. These commits need not be
released. The checked-in `interop/sdk-revisions.json` provides the fixed
repository/path configuration; its historical revisions are not CI pins. No
workflow updates that file or opens a pin-update PR.

For `repository_dispatch` type `sdk-released`, the resolver reads all four SDKs
and selects the highest stable `vX.Y.Z` GitHub release for each SDK
(`agenthooksprotocol-vX.Y.Z` for TypeScript), excluding drafts and prereleases.
Lightweight and annotated tags resolve to immutable commits. Each requires a
successful push-to-`main` `release.yml` run at that SHA, including a successful
`publish` job for TypeScript, Python, and Rust or `release-please` for Go. Missing
or unfinished release evidence fails resolution instead of silently falling
back to unrelated revisions. SDK senders notify from separate `workflow_run`
workflows after successful release completion. Dispatch payloads are not trusted
as evidence: the resolver verifies GitHub metadata for the allowlisted SDKs.

The receiver uses only the read-only workflow token, ordinary `pull_request`,
and non-persistent checkout credentials. It needs no app token, publishing
credentials, or write permissions and uses no registry probes or publication
checks. Proposed code executes only on GitHub-hosted runners. Dependency installs
need network access. Language versions include patch-floating selectors and
some ecosystems lack complete dependency locks; snapshotting SDK commits does
not claim fully hermetic dependency resolution.

## Local reproduction

Download `sdk-revisions.json` from the resolver artifact or either shard's
reports. Check out those exact commits in **disposable clean SDK clones** with
the same sibling layout, and check out the corresponding proposed spec revision.
Install dependencies as in the workflow, then run from the spec root:

```sh
python3 tools/generate_sdk.py --all
# Install/build/test each sibling as in the workflow before running the matrices.
python3 tools/run_sdk_integration.py --sdk-revisions /path/to/sdk-revisions.json \
  --reports-dir ../reports --jobs 4
```

`--sdk-revisions` validates and records the supplied snapshot; it never fetches
latest refs or changes SDK checkouts. Omit it for local exploratory runs against
your current sibling checkouts. Retrying failed shards reuses the resolver's
existing outputs; rerunning the entire workflow resolves a new snapshot.

Regeneration deliberately modifies the SDK clones. Do not use `--check` instead:
this job tests the proposed protocol against the resolved SDK implementations,
rather than whether those commits already include the proposed artifacts.
