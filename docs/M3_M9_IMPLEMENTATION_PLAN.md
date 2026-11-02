# M3–M9 implementation plan — 2026-10-14

The user requested code implementation first and a combined AI/verification pass afterward. M2 is retained as implemented with an open verification gate; it will not be rewritten or declared closed. Existing dirty files are preserved. No real database migration, account connection, paid request, publication, staging or commit is included.

## Order and concrete deliverables

1. **M3 desktop:** private stdio backend bootstrap, Tauri Windows shell with narrowly scoped native commands, owned process lifecycle, tray and explicit portable/installed data roots, Python sidecar build specification, installer/portable assembly, local model settings. Keep session tokens in memory; never place them in command arguments, URLs, files or logs. Start with the Python protocol; then implement the native supervisor before exposing bootstrap to the webview. MSVC and PyInstaller are missing prerequisites for producing executable packages, rather than reasons to invent build evidence.
2. **M4 agents:** persist jobs/dependencies and role outputs, project-bound coordinator/coder/researcher/reviewer workers, bounded queue and file ownership. Reuse ToolExecutor for actual actions. Model proposals do not constitute permission. Serialize conflicting work and require separate reviewed change-sets.
3. **M5 knowledge:** bounded scoped imports, cited source records, text retrieval, editable memories, versioned artifacts and safe exports. Retrieved content is untrusted. No cross-project search unless the user explicitly chooses personal scope.
4. **M6 decisions/scheduler:** persistent questions, answers, schedules and occurrence identities, restart recovery and notifications. Reminders may resume; consequential operations wait for current authorization. Do not retry an uncertain external outcome automatically.
5. **M7 control:** implement isolation configuration/evidence and scoped Windows automation workers plus emergency stop. Browser remains disabled until disposable native isolation verification passes. Job objects control processes; they do not provide a filesystem or network sandbox.
6. **M8 skills/accounts/work:** versioned declarative manifests, consent and revocation state, least-privilege account adapters and exact-payload approved sends, business records and content drafts. Disconnected adapters stay unavailable. Live login and publication are deferred to explicit user consent.
7. **M9 voice/recovery/release:** opt-in push-to-talk capture, local STT/TTS adapters and interruption; consistent local backups, bounded restore, encrypted optional cloud backup configuration and diagnostic redaction; release assembly and recovery instructions.

## Shared implementation rules

- Extend existing migration, policy, audit, project and provider services instead of adding parallel authorization or persistence systems.
- Add versioned migrations and regression fixtures as code; do not run them on user persistence.
- Keep existing React/orb design. Make navigation available when functionality is wired, and label unverified/native-unavailable states accurately.
- Write meaningful regression cases alongside security-sensitive code. Per the current instruction, execution of tests, live inference, frontend build, packaging acceptance and full verification is deferred.
- No sub-agent implementation delegation is included. Prior authorization was for an independent read-only reviewer.

## Combined verification, after implementation

Run isolated backend/frontend regressions, lint/type/build, then independent security review and actual disposable Windows acceptance. Verify model quality/resource use, desktop process cleanup, conflicting agent work, scheduler restart, isolation, connector payload/revocation, voice privacy and restore. Installer and portable artifacts are complete only after launch and recovery tests. Any failed or unverified gate remains open; code presence is not product completion.

## Implementation ledger

- M3 started: backend private bootstrap protocol, sidecar entry point, Windows Job supervisor, Tauri shell source/configuration, frontend session bridge and PyInstaller specification. Compilation, packaging, tray/model configuration and acceptance remain pending.
- M4 implemented increment: durable project role jobs/dependencies, competing claims, file-claim serialization, bounded worker slots, executor-based dispatch, job-bound approvals, cancellation and recorded review evidence. Automatic coordinator graph generation and independent edit workspaces remain pending.
- M5 implemented increment: approved bounded text imports, scoped literal search with snapshot references, versioned text artifacts and source lineage. Live web research, rich document import/export and the broader learning workflow remain pending.
- M6 implemented increment: scoped questions/answers, durable timezone-aware reminders, unique occurrences, restart-safe missed-reminder summarization, cancellation and notification inbox. Desktop notifications and full daily briefing remain pending; consequential task execution waits for explicit review.
- M7 implemented increment: durable emergency stop, policy revision invalidation, cancellation hooks and bounded session-cleanup reporting. OS browser isolation and scoped native UIA worker containment are not implemented/verified; browser stays disabled.
- M8 implemented increment: versioned project declarative manifests with disabled install/explicit activation, protected-token account configuration/revocation, exact-payload GitHub issue/Gmail/Microsoft mail adapters, duplicate operation ledger, business/content records. OAuth sign-in/refresh, calendar and social publishing adapters remain pending.
- M9 implemented increment: opt-in ephemeral push-to-talk/local transcription, installed-local-voice output, bounded consistent database bundles, new-directory restore, optional user-bound DPAPI encryption, redacted diagnostic metadata and explicit release assembly recipe. Portable-key encrypted cloud backup and clean-machine release acceptance remain pending.
- Tests/live verification deliberately deferred at the user's request.

References: [Tauri sidecars](https://v2.tauri.app/develop/sidecar/), [Tauri capabilities](https://v2.tauri.app/security/capabilities/).
