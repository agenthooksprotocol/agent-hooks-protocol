# Public Decision Rules

## Public record

Project decisions are public by default. Use a repository issue, pull request, or AHP proposal that anyone with repository visibility can inspect. Meetings, private messages, and external forums can inform work, but they cannot produce a binding project decision until the material reasoning and outcome are recorded in the repository.

Sensitive security, conduct, personal-data, and legally restricted details can remain private. When safe and appropriate, record a redacted outcome and enough non-sensitive reasoning to explain the project action. Do not publish details merely to satisfy the public-record rule.

## Decision classes

### Routine

Editorial fixes, maintenance, tests, and implementation work that do not materially change protocol meaning, compatibility, governance, or release policy.

Routine changes use pull-request review, require approval from at least one eligible active maintainer, and have no fixed minimum review period. A maintainer can request broader or longer review.

### Material

Changes to normative protocol behavior, interoperability, compatibility, security expectations, or release semantics use an AHP proposal. The public review period is at least seven full calendar days after the proposal is marked `Review` and is complete enough to evaluate.

### Governance

Changes to governance rules, maintainer status or authority, the AHP process, or this decision policy use an AHP proposal. The public review period is at least fourteen full calendar days after the proposal is marked `Review` and is complete enough to evaluate.

Editorial changes that do not alter policy remain routine.

## Consensus and decisions

The project first seeks consensus: substantial objections are addressed, withdrawn, or documented, and no eligible maintainer maintains an unresolved reasoned objection.

If consensus is not reached after the applicable review period, an eligible maintainer can call for a decision in the public proposal record. Active, non-recused maintainers vote there. A strict majority of votes cast is required, with at least one eligible maintainer voting. A tie or absence of an eligible vote means the proposal is not accepted.

The decision record must include:

- the outcome and date;
- the deciding maintainers and any recusals;
- a concise rationale;
- significant unresolved objections or minority views; and
- required follow-up, if any.

Silence from contributors is not a vote. Maintainers should allow additional time when participation is unusually low, the proposal changes substantially during review, or affected communities have not had a reasonable opportunity to respond.

## Conflicts

Maintainers follow the conflict-of-interest rules in [GOVERNANCE.md](../GOVERNANCE.md). Administrative access must not be used to bypass review, voting, or recusal.

## Urgent action

A maintainer can take the smallest necessary action without the normal review period to address an actively exploited vulnerability, credible safety or abuse risk, accidental disclosure, service disruption, or legal restriction. Urgent action does not permanently settle the underlying policy.

As soon as it is safe and practical, the maintainer must open a public follow-up that records the action, non-sensitive rationale, duration or rollback condition, and any decision still required. Security, personal, privileged, or legally restricted details must remain private.

## Reconsideration

A decision can be reconsidered when material new evidence, implementation experience, or changed constraints emerge. Reconsideration uses the same decision class as the original subject. Repeated requests without new information can be closed with a reference to the existing record.
