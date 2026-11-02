# Phase 0 independent-review fixes

Scope: the six review areas A–F only. This follow-up supersedes the approval-scope, legacy-grant compatibility and cancellation statements in `PHASE_0_IMPLEMENTATION.md`. No next-phase implementation is included.

## Implementation and regression evidence

| Area | Change | Evidence |
| --- | --- | --- |
| A: execution scope | New approvals include canonical workspace root, resolved filesystem destination, argument hash and tool-contract hash. Continuation and actual dispatch revalidate them. WorkspaceGuard also checks the approved scope when the file handler resolves its target. Unbound legacy grants require fresh approval. | Workspace A → B task resume is denied without writing either destination. Temporary Windows junction redirects are denied before continuation, at dispatch and inside the handler. Changed stored arguments and legacy unbound grants are denied. |
| B: dispatch policy | Every handler invocation checks the current risk and permission policy after the asyncio scheduling boundary. An unapproved action that now needs permission produces a fresh approval; an existing grant denied by current policy cannot execute. | Deterministic dispatch hooks raise LOW → HIGH, change permission policy for a LOW action, and deny an already-approved action. No handler effect occurs. |
| C: capability lookup | Explicit `tool:` requests and namespace-qualified capability identifiers are handled before ordinary-text fallback. Tool requests match registered IDs or exact `available_tools`, preserving MCP capability IDs. | Missing `read_file` requests with echo registered wait for capability. An explicit MCP task resolves its registered capability, respects disabled state, requests approval and invokes the transport after approval. |
| D: MCP optional arguments | Optional fields without explicit defaults remain absent through normalization, persistence and transport. Explicit defaults are retained. The transport handler revalidates the outgoing dictionary using the discovered input model. | Recording transport receives `{size: 2}` for omitted count, and `{count: 3, size: 2}` for supplied count. Invalid null/type/bound values and mutated models never reach transport. |
| E: cancellation | A process-wide coordinator keys active actions by canonical database path and task ID. Cancellation is sent to the owning event loop and cleaned up after each action. An OS-backed database sidecar lock guards the complete API lifespan, including startup recovery. | A different orchestrator/repository cancels both running and approval-resumed cooperative handlers before a released side effect. Unrelated databases remain unaffected. Another process is denied the startup lock, and blocked startup does not recover a live task. |
| F: regression evidence | Added transaction-boundary expiry and synchronized competing resume tests. Replaced the browser metadata-only completion assertion with an explicit fake browser executor and invocation/step assertions. | Expiry after the approved read cannot win consumption. Competing resumes start together before the database claim; one wins and one effect occurs. The browser test asserts the requested fake executor actually ran. |

Before implementation, the initial new regression set produced **11 failures and 8 passes** against the reviewed code. The failures directly reproduced the workspace, risk/policy, missing-capability, MCP omission/outgoing validation, cancellation and argument-binding defects. The expiry and synchronized claim protections already worked; their new tests add missing race coverage.

A later handler-boundary junction regression also failed before the execution-local guard check was added. Tests use temporary Windows junctions inside their own workspace and remove only those junctions.

## Supported boundary and limitations

- One backend process per canonical database on the local filesystem is supported. Multiple orchestrator instances inside that process share cancellation. The application acquires an OS lock before any persistence initialization/recovery and holds it until shutdown; the OS releases it if the process exits. The sidecar must not be deleted/replaced while a backend is running.
- Repository methods alone are not a distributed worker system. Independent processes must not bypass the application lifespan or directly invoke startup recovery against a live database. Hard-link database aliases and distributed/network-filesystem locking are unsupported.
- Cancellation can interrupt a cooperative coroutine at its cancellation points. It cannot reverse already-accepted remote effects, forcibly stop arbitrary blocking Windows UIA work, or undo synchronous effects that have already happened. Cross-thread cancellation is delivered through the owning event loop.
- Filesystem scope is checked during dispatch and target resolution. This is not an OS sandbox or a guarantee against arbitrary external filesystem mutation after the final check; atomic filesystem transactions/checkpoints remain outside this task.
- MCP still supports only the previously documented closed primitive schemas. No external MCP server, live browser, provider account or UIA integration was enabled to verify these fixes.
- Existing unbound approvals are deliberately unusable; reissue the action and review a fresh approval. A denied task continuation fails without replaying its action.

## Verification

All API test persistence is isolated by `tests/conftest.py`; repository and integration fixtures use `tmp_path`. Providers/transports are mocked. Subprocess lock tests receive only temporary database paths. No startup or migration was run against user persistence.

Final results:

- Focused backend run after the handler-scope fix: **50 passed**, one upstream Starlette/AnyIO deprecation warning.
- Full isolated backend suite on the final code: **170 passed**, one upstream Starlette/AnyIO deprecation warning, in 43.67 seconds. This includes **29 new review regression cases**.
- Ruff: **passed** (`python -B -m ruff check --no-cache services tests`).
- Frontend: **19 passed**, two test files (`npm test -- --pool=forks --maxWorkers=1`).
- ESLint: **passed** (`npm run lint`).
- TypeScript and production build: **passed** (`npm run build`, which runs `tsc -b` and Vite). No frontend source was changed by this task.
- Python source syntax parsing and `git diff --check`: **passed**.

Backend commands used `-B` and `-p no:cacheprovider`. No regression remains failing in A–F; the limits listed above are not claims of distributed cancellation, filesystem atomicity or verified live integrations.

The initial preservation snapshot covered 78 tracked/untracked nonignored files. Only five backend source files and `tests/test_phase51.py` changed among those existing files. The other 72 files, including the existing README, frontend changes, earlier Phase 0 regression tests and implementation notes, retained their exact byte hashes. New files are this report, the backend boundary module and the review regression suite.

No package installation, dependency/configuration change, staging, commit, push, authentication, new UI, Coding Studio, multi-agent feature or integration implementation was performed.
