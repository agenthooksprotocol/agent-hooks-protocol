# Release and Versioning Policy

## Scope

This policy governs releases of the language-neutral Agent Hooks Protocol. It does not make an SDK, binding, example, adapter, or implementation normative. Every SDK, including every TypeScript SDK, is non-normative and can use its own separately documented release cycle and version numbers.

## Version numbers

Protocol releases use publication dates in exact `YYYY-MM-DD` form. The release tag, the four repository snapshot directory names, public schema `$id` namespace, manifest `snapshotVersion`, and on-wire `protocolVersion` all use the same date without a `v` prefix.

Dates identify protocol revisions; they do not imply compatibility ordering. Implementations use exact protocol-version matching unless a future published revision defines negotiation. Compatibility effects, migration guidance, deprecations, and security impact must be stated in each release record.

The mutable working snapshot is identified by the literal `draft` in all four repository roots, public schema `$id` values, metadata, examples, fixtures, and on-wire `protocolVersion`. Publication replaces `draft` with the selected date throughout the copied release snapshot. `draft` is not a release tag.

For release `2026-08-27`, the matching snapshot roots are `spec/2026-08-27/`, `schema/2026-08-27/`, `fixtures/2026-08-27/`, and `conformance/2026-08-27/`, and the Git tag is `2026-08-27`. Snapshot keys have no suffixes or aliases such as `latest`. A date must be a real calendar date with zero-padded month and day.

A change is assessed by its effect on the released normative contract, not by file type, line count, or implementation difficulty. A later date can be incompatible with an earlier date; that fact must be called out prominently.

## Normative release set

Each release must identify the exact repository revision and the language-neutral protocol documents or artifacts that form its normative release set. Material on the default branch is unreleased unless a released document explicitly says otherwise.

AHP proposals explain and authorize project direction. Tests, examples, generated artifacts, SDKs, bindings, adapters, and implementations can provide evidence, but they remain non-normative and are not part of the normative release set. This includes TypeScript SDKs and implementations. An implementation cannot override normative protocol material by behaving differently.

Publication freezes all four parts of the logical repository snapshot, even when the release record identifies only a subset as normative: specification and requirements, JSON Schemas, golden fixtures, and conformance profiles. Markdown and JSON Schema remain source artifacts. A release does not replace them with generated documentation, MDX, TypeScript definitions, SDKs, adapters, or reference implementations.

## Proposal and release relationship

Material protocol and release-policy changes require an Accepted AHP proposal. Acceptance is not publication: a decision becomes part of a released protocol only when its normative changes are incorporated, reviewed, and included in a release.

Substantive changes after AHP acceptance must follow the amendment rules in the [AHP process](../governance/AHP-PROCESS.md).

## Release process

An active maintainer coordinates each release in a public issue or pull request and, before freezing, must:

1. identify included Accepted AHP proposals and other changes;
2. describe compatibility and select the publication date;
3. update and review intended status or version references, migration guidance, and deprecations in the mutable `draft` snapshot;
4. ensure `spec/draft/`, `schema/draft/`, `fixtures/draft/`, and `conformance/draft/` describe one matching revision;
5. run and record the focused checker tests and draft validation:

   ```sh
   python3 -m unittest discover tools/tests
   python3 tools/check_conformance.py
   ```

6. identify the normative release set and source revision; and
7. prepare release notes describing compatibility, security fixes, and known limitations.

To freeze and retarget the prepared draft, run:

```sh
VERSION=2026-08-27
python3 tools/freeze_snapshot.py "$VERSION"
```

The release tool accepts only a real `YYYY-MM-DD` calendar date and refuses any pre-existing destination. It validates the draft before copying, stages all four roots, and retargets the defined status, metadata, repository paths, schema `$id` values and protocol constants, Markdown examples, fixture wire values, conformance profile markers, and hashes. It then validates the staged date snapshot before installing it. Relative schema `$ref` values and protocol semantics remain unchanged.

Validate the selected frozen snapshot before tagging:

```sh
python3 tools/check_conformance.py --snapshot "$VERSION"
python3 tools/check_frozen_snapshots.py
```

The conformance checker validates the selected snapshot without updating it. The frozen checker selects the newest reachable released date tag and compares every date snapshot that existed there against its complete current tree across `spec/`, `schema/`, `fixtures/`, and `conformance/`. It permits a wholly new date set before that date is tagged but rejects modification, deletion, rename, replacement, or addition beneath an already released snapshot. With no reachable date tag, it treats the repository as a first-release bootstrap and still runs full internal validation.

Review the copied paths and complete release diff, merge the release commit, and create the exact tag `$VERSION` at that commit only after both validations pass. Release date tags are immutable and must never be moved or reused. CI must fetch full history and tags so Python can select the authoritative baseline. Never use `--update-manifests` with a date snapshot. Correct a released protocol by publishing a new date snapshot.

## Deprecation and removal

A deprecation must state the affected behavior, replacement or migration path, and earliest dated release in which removal is allowed. Removal of released normative behavior requires a new dated release and explicit compatibility guidance unless the original contract bounded that behavior to a shorter lifetime.

Security fixes can require accelerated change. Use the urgent-action rule for private handling, then document compatibility and version impact as soon as disclosure is safe.

## Support and cadence

The project has no fixed release cadence. Release notes identify supported lines when more than the latest release is supported. Unless they say otherwise, only the latest release receives fixes. This policy does not guarantee a release date, maintenance duration, or security response time.
