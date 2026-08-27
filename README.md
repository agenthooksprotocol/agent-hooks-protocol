# Agent Hooks Protocol

Agent Hooks Protocol (AHP) is an open protocol project for defining interoperable hooks for agents. This repository contains the protocol work, its supporting artifacts, and the public process used to evolve it.

The project is at an early stage. All current protocol artifacts are Working Drafts. Do not treat unreleased material on the default branch as a stable compatibility commitment.

## Working Draft artifacts

- [Canonical Working Draft specification](spec/draft/index.md)
- [Stable requirement manifest](spec/draft/requirements.json)
- [Versioned JSON Schemas](schema/README.md)
- [Golden fixtures](fixtures/README.md)
- [Conformance profiles](conformance/README.md)
- [Dependency-free validation tooling](tools/README.md)
- [Schema-driven SDK generation](tools/sdk-codegen/README.md)

The canonical Working Draft and each machine-readable artifact use the literal snapshot and wire identifier `draft` until publication.

## Repository snapshots

The four parallel `draft/` trees form one logical, mutable snapshot:

- `spec/draft/` contains the Markdown specification and requirement manifest;
- `schema/draft/` contains the JSON Schemas and schema manifest;
- `fixtures/draft/` contains golden examples and their manifest; and
- `conformance/draft/` contains conformance profiles and their manifest.

A release freezes matching copies beneath the same publication-date key, such as `spec/2026-08-27/`, `schema/2026-08-27/`, `fixtures/2026-08-27/`, and `conformance/2026-08-27/`. There is no `latest/` alias: use `draft` for current work or an exact `YYYY-MM-DD` date for a published snapshot. The repository snapshot key and on-wire `protocolVersion` are identical: both are `draft` while mutable and both become the publication date when frozen.

Validate the mutable snapshot with `python3 tools/check_conformance.py`, or a frozen snapshot with `python3 tools/check_conformance.py --snapshot 2026-08-27`. See the [tooling guide](tools/README.md) for focused tests and the [release policy](docs/RELEASES.md) for the freeze procedure.

## Normative scope

AHP is language-neutral. Normative protocol requirements must be expressible without depending on a programming language, runtime, framework, or vendor.

Only repository material that an AHP release explicitly identifies as normative defines the protocol. All implementations, SDKs, bindings, examples, adapters, and generated artifacts are non-normative. This includes every TypeScript SDK or TypeScript implementation. If an SDK or example conflicts with the normative protocol, the normative protocol takes precedence.

Markdown and JSON Schema are the source artifacts in this repository. Generated documentation, SDKs, adapters, and reference implementations do not replace them as sources of truth.

## Participate

- Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a contribution.
- Follow the [Code of Conduct](CODE_OF_CONDUCT.md).
- Use the [AHP proposal process](governance/AHP-PROCESS.md) for material protocol or governance changes.
- Keep project decisions in public repository records under the [public decision rules](governance/PUBLIC-DECISIONS.md).
- Report vulnerabilities according to [SECURITY.md](SECURITY.md), not in a public issue.

## Project policies

- [Governance](GOVERNANCE.md)
- [Maintainers](MAINTAINERS.md)
- [Release and versioning policy](docs/RELEASES.md)
- [AHP proposal template](governance/AHP-TEMPLATE.md)

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
