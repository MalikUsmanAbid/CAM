# M0/M1 independent security review

Final verdict: **PASS for the inspected foundation changes**, after corrections. This is not a full-product or desktop-release verdict. The authorized reviewer performed read-only code/diff inspection and temporary regression checks; it edited no files.

The initial snapshot failed with two High issues: task creation committed before its project scope, allowing personal-scope disclosure after interruption; approval consumption checked expiry but delayed dispatch could still execute after the deadline. Medium findings covered extra schema columns accepted by positional repositories and connections left open until garbage collection. A Low frontend race allowed an old task-detail rejection to populate the new workspace's error state.

Corrections atomically persist task/scope/memory inputs/initial event, revalidate approval deadlines at dispatch and enforceable filesystem effects, verify ordered complete schema-column contracts, close transactional connections explicitly, and suppress stale detail errors. Re-review found that column-set equality still accepted reordered scope columns; that was also fixed before the final PASS. Tests preserve malformed database bytes/rows while failing closed.

Independent evidence: 29 targeted checks on the initial snapshot; 37 after the first corrections; 15 after the final schema-order correction. The exact frontend stale-rejection regression independently passed. Passing counts were not used to excuse reproduced defects.

Regression references: `tests/test_m1_review_fixes.py`, `tests/test_project_isolation.py`, `tests/test_versioned_migrations.py`, `tests/test_model_dispatch_queue.py`, `tests/test_credentials.py` and `apps/desktop/src/App.test.tsx`. Actual Windows dashboard fixture acceptance is separately recorded in `docs/evidence/m1-windows-acceptance.json`.

Remaining release gates: Coding Studio; live local inference; packaged session bootstrap; installer/portable lifecycle; later automation/account/voice/recovery acceptance. Existing dirty work was preserved using the implementation agent's baseline record; Git alone could not establish historical attribution.
