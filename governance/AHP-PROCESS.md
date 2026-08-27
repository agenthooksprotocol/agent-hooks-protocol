# AHP Proposal Process

An AHP proposal is the lightweight design and decision record for a material change to Agent Hooks Protocol or its governance. Proposals use identifiers in the form `AHP-NNNN`, where `NNNN` is a four-digit decimal number.

## When an AHP proposal is required

Use an AHP proposal for:

- a new or materially changed normative protocol capability;
- a breaking or deprecating compatibility decision;
- a material security, privacy, or interoperability rule;
- a change to release/versioning policy;
- a change to governance, decision rules, or the proposal process; or
- adding, removing, or materially changing the authority of a maintainer.

Do not require an AHP proposal for typo fixes, non-semantic clarification, tests, routine maintenance, or changes confined to a non-normative implementation. A maintainer can reclassify a change when its effects are disputed.

## Location and numbering

Use [AHP-TEMPLATE.md](AHP-TEMPLATE.md) to draft a proposal. Proposal documents belong in `proposals/` and are named `AHP-NNNN.md`; create the directory when the first proposal is needed.

Open a public tracking issue before requesting a number. An active maintainer assigns the next unused number after checking open and merged proposals. Numbers are never reused, including for withdrawn or rejected proposals. Assignment prevents identifier collisions; it does not imply endorsement.

## Statuses

- `Draft`: being prepared; no decision requested.
- `Review`: complete enough for the applicable public review period.
- `Accepted`: approved as a project decision.
- `Rejected`: considered and not approved.
- `Withdrawn`: withdrawn by its authors before a decision.
- `Superseded`: replaced by a named later AHP proposal.

The status in the proposal document is authoritative. A proposal can return from `Review` to `Draft` after substantial revision.

## Lifecycle

1. **Discuss:** Open a public issue describing the problem, scope, and why an AHP proposal is needed.
2. **Draft:** Copy the template, request a number, and open a pull request for `proposals/AHP-NNNN.md`.
3. **Refine:** Address compatibility, security, privacy, language neutrality, alternatives, and release effects. Record significant objections rather than hiding disagreement.
4. **Review:** An eligible maintainer marks the proposal `Review` when it is complete enough to evaluate. Material proposals receive at least seven full calendar days; governance proposals receive at least fourteen, as defined by the [public decision rules](PUBLIC-DECISIONS.md).
5. **Decide:** Seek consensus, then use the public voting rule if needed. Record the result and rationale in both the pull request and proposal.
6. **Follow through:** Land implementation and normative text through separately reviewed changes. Reference the accepted proposal.
7. **Release:** Apply the [release and versioning policy](../docs/RELEASES.md). Acceptance alone does not publish or version a protocol change.

If review reveals that a proposal is routine, record that conclusion and continue through normal pull-request review without assigning a new AHP number.

## Normative effect

A Draft or Review proposal is non-normative. An Accepted proposal records an approved direction but does not itself alter a released protocol unless a release explicitly incorporates its requirements into the normative release set.

AHP is language-neutral. A proposal must not make a specific SDK or programming language normative. All SDKs, including TypeScript SDKs, remain non-normative even when they demonstrate or test a proposal.

## Amending a decision

Substantive changes to an Accepted proposal require a new AHP proposal that names and supersedes or amends it. Editorial corrections that preserve the decision can use routine review. Rejected, Withdrawn, and Superseded proposals remain available as historical records.
