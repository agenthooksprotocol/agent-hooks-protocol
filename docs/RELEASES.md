# Release and Versioning Policy

## Scope

This policy governs releases of the language-neutral Agent Hooks Protocol. It does not make an SDK, binding, example, adapter, or implementation normative. Every SDK, including every TypeScript SDK, is non-normative and can use its own separately documented release cycle and version numbers.

## Version numbers

Protocol releases use Semantic Versioning 2.0.0 in the form `MAJOR.MINOR.PATCH` and Git tags in the form `vMAJOR.MINOR.PATCH`.

- **MAJOR:** incompatible changes to released normative requirements.
- **MINOR:** backward-compatible additions or deprecations.
- **PATCH:** backward-compatible corrections and clarifications that do not add a requirement or change intended behavior.

Before `1.0.0`, the protocol is initial development. A `0.MINOR.0` release can contain incompatible changes, which must be called out prominently. Once `1.0.0` is released, incompatible normative changes require a new major version.

A change is classified by its effect on the released normative contract, not by file type, line count, or implementation difficulty. When classification is uncertain, use the more conservative version increment and explain it in the release record.

## Normative release set

Each release must identify the exact repository revision and the language-neutral protocol documents or artifacts that form its normative release set. Material on the default branch is unreleased unless a released document explicitly says otherwise.

AHP proposals explain and authorize project direction. Tests, examples, generated artifacts, SDKs, bindings, adapters, and implementations can provide evidence, but they remain non-normative and are not part of the normative release set. This includes TypeScript SDKs and implementations. An implementation cannot override normative protocol material by behaving differently.

## Proposal and release relationship

Material protocol and release-policy changes require an Accepted AHP proposal. Acceptance is not publication: a decision becomes part of a released protocol only when its normative changes are incorporated, reviewed, and included in a release.

Substantive changes after AHP acceptance must follow the amendment rules in the [AHP process](../governance/AHP-PROCESS.md).

## Release process

An active maintainer coordinates each release in a public issue or pull request. The release record should:

1. identify included Accepted AHP proposals and other changes;
2. classify compatibility and select the version;
3. update normative version references and migration or deprecation guidance;
4. run and record checks relevant to the release;
5. identify the normative release set and source revision;
6. publish release notes describing compatibility, security fixes, and known limitations; and
7. create the corresponding Git tag and repository release.

A release must not silently change after publication. Correct it with a new release using the appropriate version increment. Repository hosting metadata can be repaired without changing release contents if the repair is documented.

## Deprecation and removal

A deprecation must state the affected behavior, replacement or migration path, and earliest release in which removal is allowed. After `1.0.0`, removal of released normative behavior requires a major release unless the original contract explicitly bounded that behavior to a shorter lifetime.

Security fixes can require accelerated change. Use the urgent-action rule for private handling, then document compatibility and version impact as soon as disclosure is safe.

## Support and cadence

The project has no fixed release cadence. Release notes identify supported lines when more than the latest release is supported. Unless they say otherwise, only the latest release receives fixes. This policy does not guarantee a release date, maintenance duration, or security response time.
