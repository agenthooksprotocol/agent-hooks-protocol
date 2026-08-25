# Governance

## Purpose

Agent Hooks Protocol (AHP) is governed as an open, language-neutral protocol project. Governance exists to preserve interoperability, transparent evolution, contributor trust, and a clear separation between the normative protocol and non-normative implementations.

The project seeks consensus in public and uses maintainer decisions when consensus cannot be reached. Authority is exercised for the project rather than for a maintainer's employer, customer, or preferred implementation.

## Principles

- **Public by default:** project reasoning and decisions are recorded in repository issues, pull requests, or AHP proposals.
- **Language neutrality:** normative requirements do not depend on a particular language or SDK.
- **Implementation independence:** all SDKs and implementations, including TypeScript SDKs, are non-normative.
- **Compatibility:** material compatibility changes receive explicit review and follow the release policy.
- **Open participation:** anyone can propose, review, or implement work subject to project policies.
- **Documented authority:** maintainer status and decision rules are public.

## Participants and contributors

Anyone following the [Code of Conduct](CODE_OF_CONDUCT.md) can participate. Contributors have no obligation to become maintainers and do not gain governance authority solely through contribution volume, employment, sponsorship, or authorship of an AHP proposal.

## Maintainers

Active maintainers are listed in [MAINTAINERS.md](MAINTAINERS.md). Maintainers are expected to:

- review and merge contributions;
- apply the public decision and AHP processes consistently;
- protect the normative and non-normative boundary;
- coordinate releases and security handling;
- enforce the Code of Conduct fairly;
- disclose relevant conflicts of interest; and
- steward access with least privilege.

A CODEOWNERS entry routes review requests. It does not by itself confer maintainer status, decision authority, repository access, or ownership of contributed copyright.

## Decision authority

The [public decision rules](governance/PUBLIC-DECISIONS.md) define decision classes, review periods, records, voting, conflicts, and urgent exceptions.

Routine changes require approval from at least one eligible active maintainer. Material protocol, compatibility, governance, maintainer, and release-policy changes require an [AHP proposal](governance/AHP-PROCESS.md) and the applicable public review. Acceptance of an AHP records a decision; it does not by itself change the normative protocol or create a release.

Maintainers can delegate tasks, but they remain accountable for decisions made with maintainer authority. No private discussion is binding until its non-sensitive reasoning and result are recorded publicly.

## Conflicts of interest

A maintainer must disclose a financial, employment, personal, or other interest that a reasonable observer could view as materially affecting a decision. Proposal authorship or employment alone does not automatically disqualify a maintainer, but relevant interests must be disclosed.

A maintainer with a direct material conflict must recuse themselves from the deciding vote and must not use administrative access to determine the outcome. If recusals leave no eligible decision-maker, the proposal remains undecided while the project seeks a neutral qualified reviewer or additional maintainer through the public process.

## Adding maintainers

Maintainer candidates should demonstrate sustained, constructive participation, sound judgment, reliable review, respect for language neutrality, and understanding of project security and conduct duties.

Any contributor can nominate a candidate with the candidate's consent. The nomination uses an AHP proposal, includes evidence relevant to the role, and follows the governance review period. Eligible active maintainers decide under the public decision rules. The decision updates [MAINTAINERS.md](MAINTAINERS.md) and access separately; neither change should be treated as implying the other has already occurred.

## Inactive and former maintainers

A maintainer can resign at any time. A maintainer who expects to be unavailable can ask to be marked inactive. Inactive maintainers do not count in decisions and should not retain access that is unnecessary.

After at least six months without project participation, the project can propose inactive status after making a reasonable public attempt to contact the maintainer. Removal for conduct, security, or persistent failure of maintainer duties can happen sooner through a proportionate process. The affected maintainer cannot decide their own status. Private personal or conduct details must be omitted from the public record, but the outcome and a non-sensitive rationale should be recorded when possible.

Former maintainers can return through the same public process used to add maintainers.

## Changes to governance

Changes to this file, maintainer authority, the public decision rules, or the AHP process require an AHP proposal and the governance review period. Editorial fixes that do not change meaning can use routine review.
