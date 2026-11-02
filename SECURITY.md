# Security policy

CAM is a development preview. No stable version currently has a security-support commitment. Do not use it for unattended high-impact actions or expose the local API to a network.

## Report a vulnerability privately

Do **not** open a public issue containing exploit details, API keys, user files or diagnostic data.

When enabled, use [GitHub's private vulnerability reporting](https://github.com/MalikUsmanAbid/CAM/security/advisories/new). If that form is unavailable, contact the maintainer through a private channel listed on their [GitHub profile](https://github.com/MalikUsmanAbid). If no private contact is available, open a minimal issue requesting a private reporting channel, without technical exploit details.

Developer security contact: [usman234dev@gmail.com](mailto:usman234dev@gmail.com). Send only a minimal, sanitized reproduction; never include real credentials or personal databases.

Include the affected commit/version, expected boundary, observed behavior, a minimal reproduction using disposable data, and the potential impact. Never send real secrets or personal databases. Response times are best effort; no SLA or bounty is offered.

## Boundaries to preserve

- Authenticated loopback API with Host and Origin checks.
- Canonical project/workspace scopes, exact approvals and current-policy validation at dispatch.
- Windows-protected credential references; credentials must not enter logs, prompts, Git or frontend storage.
- Temporary persistence for tests and explicit authorization for existing user database migrations.
- Fail-closed unsupported automation. Browser isolation and Windows UI automation require their documented platform verification.

Cancellation cannot guarantee reversal of side effects already accepted by external services. See [architecture](docs/ARCHITECTURE.md), the [historical permission model](docs/SECURITY.md) and [current status](docs/STATUS.md). Architecture documents and passing unit tests are not a security certification.
