# Installed upgrade verification closure — 2026-10-26

**PASS for the current-device 0.1.0 → 0.1.1 upgrade and preservation scope.** Version 0.1.1 remains registered and the installed shell/backend hashes match the accepted release. The installer was not rerun; no package installation, source change, rebuild, migration, restore or API key setup was necessary in this continuation.

## Existing completed upgrade evidence (2026-10-24)

The actual installer ran in `/S /UPDATE /NS` mode against the existing installation and exited successfully. Installed payload hashes match the release, allowing only Tauri's exact NSIS marker transform. Before first launch, all **373 original data/settings/runtime files (4,434,694,612 bytes)** matched their baseline hashes. Autostart was preserved. The actual installed window reached **Connected**, **Backend online**, **Mock (local)** and **Approval gated**, verified through Windows accessibility. After original-data launch, all **51 database tables' rows were unchanged**, integrity OK, schema 13 unchanged. No migration was performed. [Actual upgrade evidence](evidence/user-upgrade-2026-10-24.json).

A recoverable snapshot remains at `%LOCALAPPDATA%/USMANJARVIS-upgrade-backups/2026-10-24-0.1.0-to-0.1.1`: original application files, database, consistent SQLite backup, WebView profile/settings, workspace, runtimes and manifests. Access is restricted to the current Windows user and SYSTEM. Reproducible development build/release directories and the ephemeral backend lock file were excluded. Never copy a database over a running application during recovery.

## Remaining check completed (2026-10-26)

The **actual installed backend executable** was exercised in an owned Windows job using dedicated private pipes and entirely temporary database/workspace paths. Four checks passed:

1. Private loopback startup.
2. Unauthorized access returns 401; authenticated real tool registry is available.
3. Private session renewal invalidates the previous token while the new session works.
4. Private shutdown exits successfully and the owned process tree is cleaned up.

[Installed-backend evidence](evidence/installed-backend-upgrade-2026-10-26.json). Session tokens remained in memory and were not logged. No model request was submitted. Current real database bytes and native model-settings hashes were compared immediately before/after this continuation and are unchanged. No current user edits were reverted to the older backup. [Closure evidence](evidence/upgrade-closure-2026-10-26.json).

## Scope and remaining limits

No new frontend/backend suite or previous completed build was repeated. The prior source checks and keyless provider UI acceptance remain recorded in [the cloud release report](CLOUD_RELEASE_2026_10_24.md). No real Gemini/Claude response, account access or billing is claimed without API keys. The earlier Windows screenshot/coordinate helper failure did not prevent accessible-window launch confirmation; no screenshot or provider-settings click success is claimed for that original-data launch. Clean-machine release, browser isolation, UIA and complete M0–M9 acceptance remain separate gates.
