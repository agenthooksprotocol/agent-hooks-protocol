# Contributing

Thank you for helping improve Agent Hooks Protocol (AHP). Contributions can include issues, design feedback, documentation, tests, protocol proposals, and implementation work.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Before you start

- Search existing issues, pull requests, and AHP proposals before starting duplicate work.
- Discuss material or potentially breaking work in a public issue before investing in an implementation.
- Do not disclose a suspected vulnerability publicly. Follow [SECURITY.md](SECURITY.md).
- Keep changes focused. Separate unrelated editorial, governance, protocol, and implementation changes.

A small correction can normally use a pull request. A material protocol, compatibility, release-policy, or governance decision uses the [AHP proposal process](governance/AHP-PROCESS.md).

## Public collaboration

Use repository issues and pull requests for project work. A conversation in private chat, a meeting, or an external forum is not a project decision until the relevant reasoning and outcome are recorded publicly under the [public decision rules](governance/PUBLIC-DECISIONS.md).

Security, conduct, personal data, and legally sensitive details can require private handling. Record a suitably redacted outcome when it is safe and appropriate to do so.

## Making a change

1. Open or reference an issue when the change needs discussion.
2. Fork the repository or create a branch.
3. Make the smallest coherent change.
4. Add or update relevant tests and documentation when applicable.
5. Run the checks relevant to the files you changed and report them in the pull request.
6. Sign off every commit as described below.
7. Open a pull request that explains the problem, approach, compatibility impact, and validation.

Reviewers can ask for changes, additional evidence, or an AHP proposal. Approval and merge authority follow [GOVERNANCE.md](GOVERNANCE.md); CODEOWNERS is only a review-routing mechanism.

## Working with protocol snapshots

Current protocol work lives in four parallel directories that make up one logical snapshot: `spec/draft/`, `schema/draft/`, `fixtures/draft/`, and `conformance/draft/`. Keep changes across those roots consistent, including repository-relative paths, version metadata, hashes, and manifest entries.

Use the draft snapshot for unpublished changes. Refresh expected artifact hashes only when the underlying draft changes are intentional:

```sh
python3 tools/check_conformance.py --update-manifests
```

Then run the focused checker tests and validate the draft:

```sh
python3 -m unittest discover tools/tests
python3 tools/check_conformance.py
```

Directories named with exact SemVer, such as the four matching `0.1.0/` directories, are frozen release snapshots. Do not edit them or run `--update-manifests` against them. Corrections require a new release under the [release policy](docs/RELEASES.md). To inspect a frozen snapshot, pass its exact SemVer key, for example `python3 tools/check_conformance.py --snapshot 0.1.0`.

Changes to protocol behavior or release policy still require the applicable public discussion and AHP proposal. A repository-only refactor that preserves normative text, requirements, and behavior can use the normal contribution process, but its pull request must state and verify that preservation. Markdown and JSON Schema remain the source artifacts; generated documentation, SDKs, adapters, and reference implementations are not substitutes for changes to those sources.

## Developer Certificate of Origin

This project uses the [Developer Certificate of Origin 1.1](https://developercertificate.org/). Add a `Signed-off-by` trailer to every commit:

```text
Signed-off-by: Your Name <you@example.com>
```

Git can add the trailer from your configured author identity:

```sh
git commit -s
```

The sign-off certifies the statements in DCO 1.1 for that contribution. Use a name and email address by which you can be identified; an email address permitted by your contribution platform, including an appropriate privacy-preserving address, is acceptable if it identifies your account.

A DCO sign-off is not a copyright assignment. It does not by itself prove provenance, resolve third-party rights, or create a legal guarantee from the project. Maintainers can ask for provenance or authorization information and can decline a contribution when its rights are unclear.

## Intellectual-property guidance

By contributing, you represent through the DCO that you have the right to submit the work under the project's license. In particular:

- Submit your own work or work that you are authorized to contribute.
- Preserve required copyright, attribution, notice, and license information for third-party material.
- Identify third-party code or content in the pull request and explain its source and license.
- Do not submit confidential information, trade secrets, or material covered by an obligation that prevents publication.
- Confirm that an employer or other rights holder has authorized the contribution when their rights can apply.
- Treat AI-assisted or generated material like any other contribution: review it, verify its provenance as far as reasonably possible, and accept responsibility for submitting it.

The Apache License 2.0 includes terms concerning copyright and patent licenses, but no contribution process can guarantee that material is free of all third-party claims. This guidance is operational, not legal advice. Contributors and maintainers should obtain their own legal advice when needed.

## Pull request checklist

- The change is focused and linked to relevant public discussion.
- Normative and non-normative material are clearly distinguished.
- Compatibility, security, privacy, and release effects are described when relevant.
- Tests or checks were run, or the reason they were not run is stated.
- Third-party material and its license are identified.
- Every commit has a DCO sign-off.
