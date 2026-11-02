# USMAN JARVIS — Phase 3 Plan: Approved Workspace Filesystem Actions

## Scope

Phase 3 extends the Phase 2 tool foundation with a real approval workflow and a deliberately narrow filesystem capability. It includes `read_file`, `write_file`, and `create_directory` only. It does **not** introduce shell/PowerShell, application control, browser automation, Windows input, MCP, voice, account integrations, or autonomous network activity.

## Objectives and completion criteria

1. Medium-risk filesystem mutation tools cannot run without a single-use, action-specific approval.
2. The dashboard shows a complete preview and lets the user approve or reject it.
3. Filesystem targets always remain inside the configured `SAFE_WORKSPACE`, including after path normalization and symlink resolution.
4. Every request, approval decision, execution outcome, timeout, cancellation, and verification result is durably audited.
5. Existing low-risk tools and Phase 1 chat remain unchanged and passing.

## Approval workflow

```text
client requests tool execution
  → registry validates name + input schema
  → policy evaluates risk and workspace scope
  → low risk: execute
  → medium/high: create pending approval and return approval_required
  → UI renders normalized action preview
  → user approve once | reject | cancel
  → server atomically consumes matching, unexpired approval
  → executor revalidates input/scope/enablement, executes, verifies, audits
```

An approval is bound to the exact tool name, normalized arguments, request ID, risk level, and a short expiry. The client must never be able to pass `approved: true` or an arbitrary approval ID that authorizes a changed action. Rejection, expiry, cancellation, mismatch, or replay produces a terminal non-execution result.

### Proposed approval states

`pending`, `approved`, `rejected`, `expired`, `consumed`, `cancelled`.

Approval creation and consumption must use SQLite transactions so two competing calls cannot consume the same approval.

## Filesystem tools

| Tool | Risk | Input | Behavior | Verification |
| --- | --- | --- | --- | --- |
| `read_file` | Low | relative path; optional bounded byte limit | Reads a regular UTF-8 text file under the safe root | resolved target is a file; result has content/metadata within limit |
| `write_file` | Medium | relative path, text content, overwrite policy | Creates or replaces a UTF-8 file only after approval | re-read exact content and report whether created/replaced |
| `create_directory` | Medium | relative path | Creates missing directory only after approval | resolved target is a directory |

No delete, rename, move, binary write, file upload, globbing, absolute target, or recursive operation is included. `read_file` should reject binary/non-UTF-8 data rather than returning it. File content and previews need a conservative maximum size to prevent UI/API memory exhaustion. The write preview should show a content digest, byte length, target path, overwrite effect, and a truncated human-readable sample—not an unbounded payload.

## Safe-root enforcement

A shared `WorkspaceGuard` should be the single way filesystem tools resolve targets:

1. Reject empty, absolute, drive-qualified, UNC, and NUL-containing paths.
2. Join only a relative path to the resolved `SAFE_WORKSPACE`.
3. Resolve the candidate with `strict=False`, then verify `candidate.is_relative_to(root)`.
4. For existing files/directories, resolve symlinks and verify the final target still lies under the root.
5. For a new target, resolve and check the nearest existing parent; do not create a path before validation.
6. Require regular files for reads/writes and directories for `create_directory`; reject devices, junction escapes, and unexpected object types.

This helper replaces duplicate path logic in `list_directory` so all four filesystem tools use identical enforcement.

## Audit model

Add append-only `approval_events` and extend `task_events` or add a dedicated `tool_executions` table. Audit rows contain a generated request ID, tool name/version, normalized non-secret target metadata, risk, decision/status, approval ID, actor (`local_ui`), timestamps, timeout, result summary, and error classification. Do not store full file content, secrets, or raw exception tracebacks in audit rows.

Required API views:

- `GET /api/v1/approvals?status=pending` — pending previews for the UI.
- `POST /api/v1/approvals/{id}/approve` — consumes no action; changes one pending decision to approved.
- `POST /api/v1/approvals/{id}/reject` — terminal rejection with optional bounded reason.
- `POST /api/v1/tools/execute` — returns a pending approval payload for medium/high risk, or executes low-risk work.
- `POST /api/v1/tools/execute/{request_id}/continue` — consumes only the exact approved request and executes it.
- `GET /api/v1/activity` — returns safe audit summaries including approval/execution status.

Exact routes/types may be refined during implementation, but approval decisions must be server-side, single-use, and independently auditable.

## UI plan

Keep the existing dashboard layout. Add a compact `Permission request` card above/replacing the empty activity region only when a pending item exists. It displays tool, target, risk, planned effect, overwrite warning, expiry, and Approve once/Reject buttons. No global “always allow” control is planned. After a decision, update the activity list with `approved`, `rejected`, `executing`, `success`, `failed`, or `timed_out`.

The UI sends typed tool requests and typed approval decisions only; it never determines risk or trusts a model response to choose a permission outcome.

## Proposed implementation files

| File | Phase 3 change |
| --- | --- |
| `services/agent_api/app/permissions.py` | Approval entities, policy decision, expiration/consume/reject behavior |
| `services/agent_api/app/memory.py` | SQLite schema initialization and repositories for approvals/audit events |
| `services/agent_api/app/tools.py` | `WorkspaceGuard`, filesystem schemas/handlers, executor continuation and verification |
| `services/agent_api/app/schemas.py` | Typed approval, execution, and filesystem request/response schemas |
| `services/agent_api/app/main.py` | Approval and continuation endpoints; safe error mapping |
| `apps/desktop/src/api.ts` | Typed approval/activity APIs |
| `apps/desktop/src/App.tsx` | Minimal pending-approval card and activity refresh |
| `apps/desktop/src/styles.css` | Small approval-card styles only |
| `tests/test_phase3.py` | Unit/integration coverage for approval, workspace guard, tools, audit |
| `tests/test_api.py` | API-level approval/continuation and Phase 1 regression coverage |
| `apps/desktop/src/App.test.tsx` | Approval preview/decision rendering tests |
| `README.md`, `docs/SECURITY.md`, `docs/TOOL_SYSTEM.md`, `docs/ROADMAP.md` | User setup, limits, security behavior, and Phase completion notes |

No new third-party dependency is expected: Python standard-library `pathlib`, `sqlite3`, `hashlib`, and the existing FastAPI/Pydantic/React stack are sufficient.

## Test strategy

### Permission and audit tests

- Low-risk tool executes without approval.
- Medium/high tool produces `approval_required` and its handler has not executed.
- Approve-once executes only the exact normalized request.
- Reject, cancel, expiry, mismatched arguments, and replay do not execute.
- Approval and execution events persist after repository/application restart.
- Audit rows contain expected metadata but no raw content/secrets/tracebacks.

### Workspace/filesystem tests

- Read allowed in-root text file; reject missing, directory, binary, oversized, absolute, and traversal paths.
- Reject `..`, drive paths, UNC paths, symlink/junction escape, and a new path whose existing parent resolves outside the root.
- Write/create require approval; deny/reject/expiry leave no file or directory behind.
- Write verification detects expected content; overwrite preview and result distinguish create from replace.
- Timeouts/failures return safe structured errors and activity records.

### Regression and UI tests

- Run all Phase 1/2 backend tests, compilation, Ruff, frontend tests, ESLint, strict TypeScript build, and production build.
- Exercise backend startup, health, mock chat, low-risk tools, pending approval, approve/continue, reject, and audit API smoke paths.
- UI test renders pending approval, disables duplicate submission during decision, reflects rejection, and shows completed execution in activity.

## Architectural concerns to resolve during implementation

1. **Local client identity:** Phase 1 has loopback CORS but no durable local session identity. Phase 3 should label the actor as `local_ui`; stronger per-install authentication belongs in a focused hardening step before remote/mobile access.
2. **TOCTOU filesystem races:** Validation and subsequent write are separate OS operations. Keep operations local, revalidate immediately before execution, avoid following symlinks where available, and document that hostile concurrent local processes are outside Phase 3’s threat boundary.
3. **Audit integrity:** SQLite is sufficient for local history, not tamper-proof forensic logging. Use append-only application behavior now; consider hash chaining/export only when the product requires stronger guarantees.
4. **Content privacy:** Approval previews and activity must describe changes without storing/displaying full sensitive file contents. Keep byte limits and redaction rules explicit.

## Out of scope

No shell/PowerShell commands, process launch, Windows automation, browser activity, MCP, voice, account integration, autonomous task planning, deletion, or recursive filesystem operations are permitted in Phase 3.
