# Secure foundation implementation

This increment implements project execution scope, shared versioned initialization, action policy revisions, Windows credential protection, practical development re-pairing and bounded model dispatch. Full product delivery, Coding Studio, desktop packaging and live local-model coding acceptance are still pending. Independent review evidence is recorded separately.

## Project and permission boundaries

Register an existing directory in Settings and choose it in the dashboard Workspace selector. Tasks execute within that canonical project root. Memories, task details/resume/cancel, approvals and event queries require matching project scope. Personal legacy records remain Personal. Project roots are resolved again on each lookup; a changed root makes an existing exact approval invalid. Project instructions are bounded, labelled untrusted context and cannot grant permissions. Browser/MCP/computer tools are not automatically inherited by project runtimes.

An approval binds actor, process-local session, project, durable policy revision, category, normalized arguments, tool contract, root and resolved destination. Saving an action policy increments its revision and requires new approvals. Blocked categories cannot execute; read-only actions can also require approval. The executor checks current policy and session again after the scheduling boundary and immediately before invoking the handler. Cooperative filesystem resolution rechecks session validity. Cancellation cannot promise to undo an external service's already accepted effects.

Tasks and progress remain scoped after startup recovery. Event payloads have version 1, global durable IDs, scoped project/task identities and typed task/approval/tool events. Authenticated SSE reconnects with `Last-Event-ID`; protected data and tokens are not cached in browser storage. Workspace switches reset their cursor and pending view state.

## Persistence and recovery

All repositories use `migrations.py`. Schema version 1 adds the initial foundation; version 2 brings legacy tables into the same ledger and adds scope/policy tables. Existing unversioned/version-1 databases require explicit migration authorization. Before any upgrade, the backend creates a consistent SQLite backup in the selected database's `migration_backups` directory. DDL, ledger and checksums commit together. An incomplete current schema, changed checksum, discontinuous ledger or unsupported future version fails closed.

No actual user database was migrated during implementation. `JARVIS_ALLOW_DATABASE_MIGRATION=1` is an operator opt-in, not permission to migrate an arbitrary discovered database. Set it only after authorizing the chosen database upgrade. Remove it after the approved upgrade. If startup reports schema damage, close the backend and inspect/restore a verified backup; do not delete version entries to force a migration.

Windows defaults use `%LOCALAPPDATA%/USMANJARVIS/jarvis.db`. `DATABASE_PATH` explicitly selects a database; `JARVIS_DATA_DIR` selects portable persistence. If an old repository database exists without explicit data selection, startup refuses rather than silently abandoning it. Detected UNC/mapped network drives, OneDrive/Dropbox paths and configured sync roots are unsupported for active SQLite. Detection cannot identify every third-party sync provider. Portable packages must run from a local directory.

## Credentials, budgets and pairing

`CredentialVault` uses Windows user-scoped DPAPI and opaque `vault:<32 hex characters>` references. Native round-trip, corrupt-ciphertext rejection and revocation are tested on this Windows device. The authenticated credential endpoint returns only a reference, never a secret. The dashboard does not collect/display secret values. Model dispatch resolves the reference anew; revocation closes matching MCP clients. Environment references remain compatible. Other Windows accounts/machines cannot be assumed to recover protected credential files; backup/export must exclude them or use a separately designed encrypted recovery mechanism. [Microsoft DPAPI documentation](https://learn.microsoft.com/en-us/windows/desktop/api/Dpapi/nf-dpapi-cryptprotectdata)

One shared transport queue permits one inference at a time. The queue includes chat and planning. After waiting, each dispatch rechecks session, credential and budget/consent policy. Missing prices do not count as zero. Conservative cost reservations include failed/uncertain requests and are not provider invoices. Paid/cloud allowance defaults to disabled. A future command queue must separately enforce one command process; no command executor is implemented in this milestone.

Pair using the current ten-minute code from the controlling backend terminal. A successful pairing prints a fresh one-use code; periodic rotation supplies another after expiry. Reloading the dashboard requires the new code because its bearer token lives only in memory. Existing valid sessions remain active until logout, expiry or backend restart. Packaged private bootstrap is still pending for M3.

## Evidence and limits

Regression sources: `test_project_isolation.py`, `test_versioned_migrations.py`, `test_credentials.py`, `test_model_dispatch_queue.py`, existing Phase 0 security regressions and frontend workspace/session tests. Tests initialize temporary databases and fixture projects through `tests/conftest.py`; they do not start the application against real user persistence.

Live cloud/MCP/account verification, dashboard visual acceptance, packaged bootstrap, installer/portable packaging, local-model benchmarks and Coding Studio acceptance remain pending. Mock transport tests prove backend dispatch behavior only. Passing test counts do not substitute for those acceptance gates.
