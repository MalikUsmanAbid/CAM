# Completion verification — 2026-10-20

Status: release acceptance remains open. This verification resumes the existing
implementation; it does not certify the complete Windows product.

## Fixes from actual verification

- Knowledge forgetting and skill uninstall supplied an extra approval-preview
  field named `effect`, which collides with the executor's protected contract.
  Rename only that extra field to `removal_effect`; retain reserved-field rejection.
  Existing API regressions exercise exact approval and continuation.
- Bound `Skill.version` to 60 characters before numeric semantic-version comparison.
  The regression submits an oversized numeric component.
- Import `HTTPException` for unavailable computer and browser-scope endpoints.
  The computer API regression verifies an HTTP 503 rather than an uncaught NameError.
- Distinguish disabled paid publishing from a changed budget revision. The
  transport remains denied in both cases; no paid request was sent.
- Use reactive hold state for the voice button and update workspace scope refs in
  a layout effect. Keep synchronous cancellation/session guards. The delayed
  microphone-permission regression verifies release remains possible while
  permission is pending and that late capture is discarded.
- Move Bluesky record-key generation to a separate utility, retaining its tests.
- Include Node types for the existing crypto-based download regression during
  TypeScript checking. No dependency version changed.
- Update dashboard regressions to open Chat before submitting and to check the
  current gated voice panel instead of its obsolete placeholder.
- Make command race fixtures wait for an explicit release signal instead of a
  short sleep; publish complete PID markers by atomic rename. Advance the fake
  expiry clock beyond both grants. These changes retain assertions against actual
  Windows processes, forbidden side effects and interrupted command records.
- Full-suite cancellation failures persisted after removing fixture timing races.
  Windows job termination is asynchronous: waiting only on the launcher or job
  accounting does not establish that a child process handle has signalled. Pin
  current job-member handles before requesting termination, wait on those handles
  and job membership, and preserve bounded cleanup. A regression retains the
  fixture child's handle before cancellation and requires it to be signalled
  when cancellation completes. Cleanup-confirmation errors now report inspection
  required even when the original operation was cancelled; a new injected-failure
  regression checks this behavior. Suspended children are still terminated if
  assignment or group inspection fails. No claim extends to external services.
- Correct import ordering and reported Python statement-format lint violations.

## Evidence

- Frontend: **27 files, 99 tests passed**, Vitest thread pool, one worker, normal
  per-file isolation retained. Duration 281.00 seconds. A prior fork-pool attempt
  stalled and was stopped; it is not counted as a pass.
- Frontend ESLint: passed.
- TypeScript and Vite production build: passed; 51 modules transformed.
- Backend Ruff: passed.
- Targeted Windows process and Studio command regressions: **30 passed**, one
  dependency deprecation warning, 38.14 seconds before final cleanup hardening.
  Those counts are intermediate evidence; final full-suite evidence follows.
- Final full backend rerun: **608 passed**, one Starlette/Anyio dependency
  deprecation warning, **185.37 seconds**. This run includes final cleanup
  hardening and its new failure-reporting regression.
- Earlier full backend runs found failures (605/607, then 606/607). Those are
  investigation results, not successful final acceptance evidence.

All API tests use the autouse temporary database/workspace fixture in
`tests/conftest.py`; process tests execute only disposable fixture programs in
temporary directories. Mock HTTP/model tests do not demonstrate live providers.
No real user database migration, external publishing, account connection, OS
network-policy deployment, package installation, staging, commit or push ran.
Existing tracked and untracked work was retained; the aggregate Git diff contains
substantial work predating this verification and is not attributed to this turn.

## Release blockers and limits

The read-only release inventory finds no PyInstaller in the project environment,
no Rust lockfile, and no Cargo, rustc, CMake or cl.exe on the current PATH. The
standard user Cargo path is also absent. MSVC availability from a separate
developer environment and Windows SDK compatibility remain unverified.
No installer, portable ZIP, Python sidecar or native browser broker was built.
Required tool installation/dependency resolution must precede those builds.

The loopback Ollama service responds, but `/api/tags` reports **zero installed
models**. No live coding inference or quality/memory benchmark was performed.

`ACCEPTED_BROWSER_BUILDS` remains empty. Computer tool registrations remain
disabled pending owned-worker Windows acceptance. Native AppContainer/WFP,
privileged deployment, loopback exception management and crash recovery require
real Windows acceptance and security review; mocked transport tests cannot open
these gates. Account publishing adapters likewise still need consented live
acceptance. A passing application suite is insufficient to declare M0–M9 complete.
