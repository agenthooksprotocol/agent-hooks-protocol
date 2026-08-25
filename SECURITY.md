# Security Policy

## Supported versions

Supported release lines are identified in release notes under the [release and versioning policy](docs/RELEASES.md). If release notes do not identify multiple supported lines, only the latest release is supported. Before the first release, the default branch is development material and is not a supported release.

Support is provided on a best-effort basis. This policy does not promise a response or remediation time.

## Report a vulnerability privately

Do not open a public issue, pull request, discussion, or AHP proposal for a suspected vulnerability.

1. Open this repository's **Security** tab.
2. If **Report a vulnerability** is available, use GitHub private vulnerability reporting.
3. If it is not available, ask an active maintainer listed in [MAINTAINERS.md](MAINTAINERS.md) to establish a private channel. Do not include vulnerability details, exploit code, affected users, or sensitive logs in that public request.

No private security email address is published for this project. Do not guess an address or send details to an unverified account.

Include, when available:

- the affected version or commit;
- the affected component and configuration;
- reproduction steps or a minimal proof of concept;
- likely impact and attack prerequisites;
- possible mitigations; and
- whether and where the issue has already been disclosed.

Remove credentials, personal data, and unrelated secrets from reports.

## Handling and disclosure

Maintainers should limit report access to people needed to investigate and should acknowledge, assess, remediate, and coordinate disclosure as capacity permits. A reporter and maintainers should avoid public disclosure until a fix or mitigation is available, or until they agree that disclosure is appropriate.

The project cannot guarantee confidentiality, eligibility for a bounty, embargo length, a fix, or a particular response time. Unless a separate program explicitly says otherwise, there is no bug bounty program.

After disclosure is safe, maintainers should create an appropriate public advisory or release note with credit if the reporter wants credit. Sensitive exploit details, credentials, and personal data must remain private.

Security concerns about a non-normative SDK or implementation should also be reported to that component's owner when it is maintained outside this repository. SDK behavior, including TypeScript SDK behavior, does not redefine the language-neutral protocol.
