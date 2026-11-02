# M2 project creation, AI proposals and durable review — 2026-10-12

Current increment: **PASS** for independently reviewed execution boundaries. The full product and live-model gate remain incomplete.

Selected-file AI proposals use the configured compatible transport, retain exact source hashes, and require separate diff approval before apply. Authorization is checked after waiting for inference capacity and before transport. Mock mode cannot supply AI execution evidence. Durable per-file proposals can be reopened after reconnect; delayed reviews cannot replace a newer editor draft. There is no atomic multi-file batch claim.

New empty projects require exact personal-workspace approval. Independent review reproduced a Medium race in the initial CreateDirectoryW implementation: replacing its directory before handle acquisition caused a later user directory to be registered. The corrected implementation atomically creates with NtCreateFile FILE_CREATE and holds its no-delete-sharing handle through registration, while parent routes remain pinned. The permanent regression attempts the original substitution at that boundary and verifies OS refusal. Independent review passed 16 backend tests and separately observed sharing violation 32 while held, with rename succeeding after close. Registration failure retains an unregistered directory for inspection; no cleanup deletes it.

Final isolated verification for this increment:

- Backend: **377 passed**, one existing Starlette/AnyIO deprecation warning.
- Frontend: **48 passed** across seven files. Single worker and bounded 30-second per-test allowance; DOM readiness allowance is five seconds. Earlier five-second runs under resource contention timed out; assertions were retained.
- Ruff, ESLint, TypeScript and production build passed; git diff --check passed with configured line-ending warnings.
- Actual Windows Edge dashboard/backend: **20 workflow checks passed**, including exact new-project approval, native empty-directory registration, multiple durable proposal review, apply, selective rollback, recovery, commands, Git inspection and project/session isolation. Evidence: `evidence/m2-project-windows-acceptance.json`. These checks use temporary persistence and mock models; they do not prove live inference or production browser network isolation.

The official standalone Ollama archive was fully SHA-256 verified and safely extracted to managed local app data. No global service, PATH or autostart was changed. The small coding model download and live CPU fixture benchmark remain pending. Installer, portable desktop and M3–M9 remain pending.

## Benchmark approval review, 2026-10-13

Before live execution, independent review reproduced a High flaw in the fixture runner's string-only AST check: a Python encoding declaration could hide an executable statement that appears only when the importer parses actual file bytes. The runner now detects source encoding, permits UTF-8 only, and compares the actual encoded-byte AST with the exact pure-addition fixture. Seven meaningful safety tests passed independently, including the original string/bytes parser mismatch without executing generated code. Independent closure verdict: PASS. This validates the benchmark approval restriction, not model quality or live inference. The earlier 377-test full-suite result predates these seven additional tests; it is not a claim of a new 384-test full run.

Preservation comparison covered all 88 baseline files: 61 unchanged, 27 attributed implementation/documentation changes, no missing baseline file. No unrelated Git operation or real persistence migration occurred.

An earlier automatic approval review rejected deletion of a disposable test directory; it was retained and no alternate deletion was attempted. Real persistence was never opened or migrated. No Git reset, staging, commit or push occurred.
