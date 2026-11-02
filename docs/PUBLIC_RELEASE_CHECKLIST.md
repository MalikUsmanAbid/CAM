# Public-source and product-release checklist

Public source and a stable Windows product are separate gates. The owner authorized MIT licensing and public publication on 2026-11-02. Product release acceptance remains separate.

## Source presentation

- [x] Current README, local banner, documentation index and setup instructions.
- [x] Explicit development-preview status and limitations; no fabricated success badges.
- [x] Contributor guidance, bug/feature forms, PR template and security-reporting policy.
- [x] Existing source snapshot excludes personal persistence, credentials, caches and installers.
- [x] Owner authorized public source visibility.
- [x] MIT license selected; third-party component licenses remain separate.
- [ ] Verify that public history contains only GitHub noreply commit identities; preserve previous private history separately.

## Public security preparation

- [x] Scan current files and the published snapshot history for common credential patterns; no matches. This is not an exhaustive secret audit.
- [x] Inspect the new screenshots and entry-point docs for personal data and unsupported claims; fixture provenance is explicit.
- [ ] Complete owner review of all source/history before public visibility; revoke any real credential if found rather than merely deleting it.
- [ ] Enable available GitHub secret scanning, push protection and Dependabot alerts after checking repository eligibility.
- [ ] Enable private vulnerability reporting when available, and verify the reporting link.
- [ ] Decide branch protection/review and required checks appropriate to the maintainer workflow.
- [ ] Confirm dependency and third-party asset notices before distributing binaries.

## Product release

- [ ] Full isolated backend/frontend verification and security review.
- [ ] Fresh CAM installer and portable build with hashes, unsigned/signing status and exact source revision.
- [ ] Disposable Windows launch, upgrade/data preservation, shutdown and restore verification.
- [ ] Live AI and connector acceptance with explicit account/paid-use consent.
- [ ] Browser/OS isolation and UI automation gates; unsupported paths stay disabled.

See [STATUS.md](STATUS.md). No checked documentation item substitutes for these execution gates.
