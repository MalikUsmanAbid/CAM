# Phase 0 — task correctness and approval integrity

Implemented against the existing FastAPI/React architecture. Existing dashboard changes are preserved; this phase does not add Coding Studio, API authentication, desktop packaging, or live integrations.

## Changes

- Ordinary deterministic requests resolve to an existing enabled echo capability. Explicitly matched disabled/unavailable capabilities cannot fall through to echo.
- Approval expiry returns an expired record instead of slicing a dataclass. Conditional status transitions prevent stale approval decisions from reviving consumed/cancelled grants.
- SQLite approval consumption returns an executable claim only to the successful transaction winner. Expiry and task ownership/status are rechecked in that transaction.
- New task approvals are bound to their task and step in the same transaction as creation. Direct tool continuation rejects task-owned approvals; legacy ownership is discovered from saved task steps.
- Terminal task transitions revoke outstanding task grants. Cancelled/failed/completed tasks cannot be resurrected by a later update.
- SQLite task claims serialize run/resume across orchestrator instances. Restart recovery clears abandoned claims and never replays running work.
- Dispatch rechecks tool enablement, risk, registered identity and task state. New grants also bind to a hash of the tool's public contract; legacy grants retain existing compatibility checks.
- Cancelling an awaiting HTTP operation cleans up its coroutine child, marks interrupted work failed and releases the task claim. This cannot undo an external operation already accepted.
- Previews contain actual risk, target, effect, timeout and tool-contract hash.
- MCP schema conversion enforces numeric bounds, exclusive bounds, positive multiples, strict primitive types, finite numbers, string limits and valid defaults. It rejects contradictory bounds and unsupported semantics rather than silently ignoring them. Only explicitly closed primitive object schemas are supported; enum, pattern, nested objects, arrays and composition remain unsupported.
- All Python tests automatically use temporary database/workspace paths and mock providers unless a test explicitly overrides them. Async tests use the application's asyncio backend.

## Storage compatibility

Two additive tables, `approval_tasks` and `task_claims`, are created by existing initialization. Existing tables/rows are not rewritten. Tests verify initialization against a legacy fixture with persisted notes and task approvals. No migration or startup has been run against the user's existing application database.

## Verification

`tests/test_foundation_integrity.py` covers ordinary requests through the real API, expiry, concurrent continuation, disabled/changed tools, task-owned route denial, cancellation, concurrent resume, restart claims, interrupted-request cleanup, preview risk, MCP constraints and legacy initialization.

Verified results: full Python suite 141 passed; frontend suite 19 passed; Ruff, Python compilation, ESLint, TypeScript and Vite production build passed. Python reports one upstream Starlette/AnyIO deprecation warning. These checks do not establish real provider, MCP server, Windows UIA or GPU behavior.

Validation commands:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check services tests
.\.venv\Scripts\python.exe -m compileall -q services
cd apps\desktop
npm test -- --pool=forks --maxWorkers=1
npm run lint
npm run build
```

## Review boundaries and remaining limitations

The implementation diff has been inspected and exercised through concurrency/security regressions. A separate independent reviewer has not signed off on this change.

The deployment still supports one backend process. Startup recovery must not run concurrently with another backend using the same database. Durable worker leases and authenticated local sessions belong to the following foundation phase.

Cancellation stops coroutine dispatch but cannot guarantee termination of a blocking Windows UIA worker or reverse an accepted remote action. No browser isolation or OS sandbox claim is introduced. Broader secret redaction, contextual consequential-action policy, verified filesystem checkpoints and project-aware Coding Studio remain planned work.

No accounts were connected, packages installed, integrations enabled, or code pushed during this phase.
