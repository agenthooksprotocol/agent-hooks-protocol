# Required SDK integration check

Configure branch protection (or a repository ruleset) to require the **SDK
integration** job from `.github/workflows/sdk-integration.yml`. The workflow runs
on every pull request and push without path filters, and can be dispatched
manually. Adding this file does not itself change repository branch protection.

## What runs

The proposed spec checkout is `agent-hooks-protocol/`; `python-sdk/`,
`typescript-sdk/`, `go-sdk/`, and `rust-sdk/` are its siblings. GitHub checks out
the pull request's proposed merge tree, not the default branch's generator.

1. Read `interop/sdk-revisions.json` and check out all four SDKs at full commit
   SHAs. Repository names and checkout paths are validated against the expected
   SDKs; floating branches and tags are rejected.
2. Set up Python 3.11, Node 20 with pnpm 10.18.3, Go 1.24, and Rust 1.88.0 with
   rustfmt. Install the Python harness's JSON Schema dependency.
3. Run `python3 tools/generate_sdk.py --all` from the proposed spec. This writes
   generated codecs, canonical schemas, and source locks into those siblings.
4. Create the Python SDK’s `.venv` (used by its adapter commands), install and test Python; install, build, and test the pnpm workspace; build,
   vet, and test Go; and build all Rust targets and run Rust tests with its
   committed lockfile. TypeScript uses `pnpm install --frozen-lockfile` and the
   repository's ordered workspace build, not guessed `dist` paths.
5. Run `python3 tools/run_sdk_integration.py --reports-dir PATH --jobs 4`.
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

## Pin maintenance

Update `revision` values in `interop/sdk-revisions.json` to published SDK commits
and review the resulting integration reports. Keep full 40-character SHAs.
Coordinated protocol changes can pin implementation commits from SDK pull
requests before those requests merge. CI uses only committed SDK contents.

The workflow uses ordinary `pull_request`, read-only `contents` permissions,
non-persistent checkout credentials, no publishing credentials or repository
secrets, and no `pull_request_target`. It intentionally executes proposed code
only on a GitHub-hosted runner. Dependency installs need network access. Language
versions include patch-floating selectors and most package ecosystems here do
not have complete dependency locks, so SDK commit pinning is not a claim of fully
hermetic dependency resolution.

## Local reproduction

Use the same sibling directory layout and dependency commands from the workflow.
Check out the manifest's commits in **disposable clean SDK clones**, not working
trees containing unpublished work. Then, from the spec root:

```sh
python3 tools/generate_sdk.py --all
# Install/build/test each sibling as in the workflow before running the matrices.
python3 tools/run_sdk_integration.py --reports-dir ../reports --jobs 4
```

Regeneration deliberately modifies the SDK clones. Do not use `--check` instead:
this job tests the proposed protocol against pinned SDK implementations, rather
than testing whether those SDK commits already include the proposed artifacts.
