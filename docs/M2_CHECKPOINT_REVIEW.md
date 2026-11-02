# Independent checkpoint review

Scope: existing-file proposals, exact approvals, copy-on-write Windows apply/rollback and legacy writes. Final verdict: **PASS for this increment**, not full M2.

The independent read-only reviewer reproduced High defects involving outside hard links, pathname cleanup after handle release, and a one-character rename-buffer allocation. A Medium finding identified original ACLs being copied after staging content. Each defect was corrected with a regression that failed against the defective behavior. The reviewer independently verified **28 native checkpoint tests** after the last correction.

Verified boundaries: current scope and expiry checks, competing grants, immutable checkpoint validation, original file locking, late-link preservation, owned-handle cleanup, protected ACL/owner/group/creation-time preservation, short-name compensation and refusal of unsupported streams/read-only files.

Two-phase rename is not an atomic transaction. Interrupted operations or refused cleanup retain recovery data and uncertain checkpoint states; recovery UI and crash acceptance remain pending. Commands, live model coding, new/multiple-file patches and the full M2 gate remain separate work. Reviews modified no files and used only disposable Windows fixtures.
