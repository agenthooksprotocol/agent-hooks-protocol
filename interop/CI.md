# Required SDK integration check

Configure branch protection (or a repository ruleset) to require the **SDK
integration** job from `.github/workflows/sdk-integration.yml`. Every pull
request runs a cheap scope check. SDK compatibility runs when the PR changes
`spec/`, `schema/`, `fixtures/`, `conformance/`, `interop/`, `tools/`, or the
`ci.yml`, `sdk-integration.yml`, or `sync-sdks.yml` workflows, or shared
`.github/actions/` setup actions. Manual dispatch
always runs compatibility.

Documentation-only and website changes skip SDK resolution and compatibility jobs.
The required aggregate check still runs and succeeds only if the scope check
succeeded and those jobs were deliberately skipped. Scope-check failures and
required shard failures remain failures, not successful skips. Workflow-level
path filters are not used, so unrelated PRs do not leave a pending required
check. SDK releases and branch pushes do not trigger this workflow.

## What runs

The proposed spec checkout is `agent-hooks-protocol/`; `python-sdk/`,
`typescript-sdk/`, `go-sdk/`, and `rust-sdk/` are its siblings. GitHub checks out
the pull request's proposed merge tree, not the default branch's generator.

1. **Resolve SDK revisions** snapshots all four SDK `main` heads once. Every
   job uses the same validated full commit SHAs and retains `sdk-revisions.json`.
   Matrix runners verify checkout HEADs against that manifest.
2. **Generate proposed SDK sources** regenerates all four SDKs, checks byte-for-byte
   reproducibility, and uploads their source archive. Dependency and compiler
   caches reuse downloads/builds, not successful test outcomes.
3. SDK validation runs in parallel: one isolated Python retention regression plus five Python test-ID shards, two TypeScript
   file shards, Go build/vet/tests, and Rust all-target/all-feature build/tests.
   Python shards partition the complete discovered test inventory by test ID.
   TypeScript preserves both existing test-file globs. Go disables its test-result
   cache and retains the explicit full vet step.
4. **Build shared integration adapters** builds the TypeScript workspace and
   native Go/Rust adapters once. A tar archive preserves executable modes.
   Consumers install runtime dependencies and reuse this run's binaries without
   repeating native compilation or SDK unit tests.
5. Integration suites run independently. Lifecycle has four disjoint group
   shards; observation has three. Each retains every scenario applicable to its
   assigned language-pair/transport/auth group. Interop unit tests have three
   duration-balanced test-ID shards. The protocol matrix has two pair shards;
   catalogue, elicitation, compaction, compaction-wire,
   authentication, and sender isolation each run once. Protocol tool tests run
   once in the separately required **Conformance artifacts** check.
6. **SDK integration** requires every preparation, SDK-validation, and integration
   job to succeed. It also verifies all unit-test inventories and lifecycle/
   observation scenario inventories, requiring complete, disjoint shard coverage
   and matching scenario hashes. Missing artifacts, skipped required jobs,
   incomplete execution, and failed tests keep the check red.

Matrix jobs use `fail-fast: false` so a failing partition does not cancel unrelated
partitions. Reports upload unless the run is cancelled and remain available for
14 days. Generated sources and executable bundles remain available for one day.
SDK-validation and integration jobs have 15-minute safety limits; Python/interop
unit processes and individual integration suites have ten-minute deadlines,
except the isolated Python retention regression. That test retains all 4097
operations in one session and has a 40-minute process deadline within a 45-minute
job. Its existing SDK canonical-schema validation is the remaining end-to-end
latency bottleneck; more CI shards cannot shorten that indivisible test.
These limits are safeguards, not a claim of measured end-to-end latency. The
integration runner kills its active suite process group on termination. New runs
cancel superseded runs for the same ref.

### Local partition reproduction

The default runner still builds adapters and runs all suites. Existing
`--suite-group core` and `--suite-group extended` invocations remain supported.
For the CI layout, build the Rust lifecycle/feature binaries and TypeScript
workspace as above, then prepare the shared interop bundle:

```sh
python3 tools/run_sdk_integration.py --prepare-only --adapter-dir /tmp/ahp-run-bin \
  --reports-dir /tmp/ahp-build-reports --sdk-revisions /tmp/sdk-revisions.json
python3 tools/run_sdk_integration.py --suite lifecycle --shard-index 0 --shard-count 4 \
  --adapter-dir /tmp/ahp-run-bin --reports-dir /tmp/ahp-lifecycle-0 \
  --sdk-revisions /tmp/sdk-revisions.json
```

Run all four lifecycle indices and all three observation indices to reproduce
complete coverage. Reusing `--adapter-dir` fails rather than rebuilding if any
required executable is absent. Unit test shards emit per-test durations. Interop uses
`tools/ci-interop-durations.json`, measured from Actions run 38451541255, to
balance expensive tests; unknown new IDs default to one second and remain in
the full inventory. Refresh the timing hints when tests materially change.
Python uses `--isolate-test` for its indivisible 4097-call regression, then
distributes every other discovered test over the remaining five shards.

## Revision selection

By default, compatibility runs use the latest SDK `main` commits observed at
the start of the resolver job. The resolver contains the fixed four SDK repository names
and sibling paths. Each run generates its own `sdk-revisions.json`; the runtime
artifact records the exact commits used by every shard.

### Coordinated proposed SDK revisions

A coordinated protocol/SDK PR may supply `tools/sdk-revisions.json` using the
same version-1 manifest format. The resolver validates exactly four approved
repositories and sibling paths, full lowercase 40-character commit IDs, and
that GitHub can resolve each commit in its declared repository. Invalid or
unavailable proposed revisions fail the run; they never fall back to `main`.
Without this file, the existing `main` resolution behavior is unchanged. The
validated manifest is retained in the same run artifact and consumed by every
shard; no checks, fixtures, or success conditions are bypassed.

Use the manifest while dependent SDK changes are under review. After those
changes land, remove it and run compatibility against SDK `main` before merging
the protocol PR. Use commit IDs, not branch names, and update them when the SDK
changes. To validate a proposed manifest locally:

```sh
python3 tools/resolve_sdk_revisions.py --proposed tools/sdk-revisions.json \
  --output /tmp/sdk-revisions.json
```

The workflow uses a read-only token, ordinary `pull_request`, and non-persistent
checkout credentials. Proposed code executes only on GitHub-hosted runners.
Dependency installs need network access. Language versions include patch-floating
selectors and some ecosystems lack complete dependency locks; snapshotting SDK
commits does not claim fully hermetic dependency resolution.

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
