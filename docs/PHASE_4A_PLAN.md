# USMAN JARVIS — Phase 4A Plan: Agent Orchestrator Core

## Scope and definition

Phase 4A turns the existing Phase 2/3 registry, policy, approval, filesystem, memory, and audit foundations into a bounded deterministic agent loop. It is planning-only at this checkpoint. The first implementation must work entirely with a deterministic/mock planner and must not require or add a cloud model provider.

The orchestrator is an application service, not a new executor. Every proposed operation still passes through the existing registry schema validation, risk policy, approval repository, safe-root checks, timeout handling, and audit path.

Out of scope: shell or subprocess execution, PowerShell, browser or Windows control, MCP, voice, accounts, credential management, autonomous internet access, arbitrary Python/module execution, deletion, and real AI provider integration.

## Current architecture findings

- `services/agent_api/app/main.py` owns the FastAPI lifespan and current HTTP routes.
- `services/agent_api/app/tools.py` contains `ToolRegistry`, typed Pydantic input models, `WorkspaceGuard`, and `ToolExecutor`. The registry currently exposes `system_time`, `echo`, `list_directory`, `read_file`, `write_file`, and `create_directory`.
- `services/agent_api/app/permissions.py` contains risk levels and approval policy helpers.
- `services/agent_api/app/memory.py` persists memories, activity events, and single-use approvals in SQLite.
- `services/agent_api/app/schemas.py` contains the existing chat, health, tool, and memory request contracts.
- `apps/desktop/src/App.tsx` renders chat, connection state, pending approvals, and activity; `api.ts` is the typed HTTP client.
- Existing tests cover Phase 1 chat/provider behavior, Phase 2 registry/permissions/memory, and Phase 3 approvals/filesystem safety.

The implementation should remain a small modular extension of this layout rather than introducing the larger Phase 0 folder tree prematurely.

## Proposed architecture

### Domain models

Create `services/agent_api/app/tasks.py` with immutable or validation-backed models:

- `TaskStatus`: `queued`, `running`, `waiting_for_approval`, `completed`, `failed`, `cancelled`.
- `Task`: task ID, original request, status, current step, timestamps, bounded error summary, and optional completion summary.
- `TaskStep`: task ID, ordinal, intent/description, selected tool, normalized arguments metadata, status, approval ID, result summary, timestamps, and error classification.
- `TaskLimits`: maximum steps, executions, repeated failures, and wall-clock duration.

Arguments and results must be treated as data. Do not persist full file contents or secret-like values in task records.

### Repository/service boundaries

Extend `MemoryRepository` or add a focused `TaskRepository` in `memory.py` only if that keeps responsibilities clear. Use SQLite tables for tasks and task steps with explicit status transitions and indexes by task ID. Keep approval rows and activity events compatible with Phase 3. Add an `AuditRepository` only if task/audit writes otherwise become coupled; it must reuse the existing redaction rules.

### Planner and selector interfaces

Create `services/agent_api/app/planner.py`:

```python
class Planner(Protocol):
    async def plan(self, request: str, context: ExecutionContext) -> list[PlannedStep]: ...

class ToolSelector(Protocol):
    def select(self, step: PlannedStep, registry: ToolRegistry) -> SelectedTool: ...
```

The Phase 4A implementation is `DeterministicPlanner`/`DeterministicSelector`. It should support a small explicit command map or test-injected plans, for example an echo request or a request represented by a predeclared tool name and arguments. It must never parse arbitrary Python, import modules, evaluate expressions, or infer an executable function from free-form text. A future Phase 4B provider can implement the protocol without changing the orchestrator.

### Execution context and result model

Create `services/agent_api/app/orchestrator.py` with small collaborators rather than a giant agent class:

- `ExecutionContext`: task ID, request, selected safe memory references, limits, cancellation signal, and correlation ID.
- `AgentOrchestrator`: task lifecycle and bounded loop.
- `ResultNormalizer`: maps existing executor dictionaries into a stable internal result.
- `RecoveryPolicy`: classifies failures and decides stop/wait/retry; it must never retry medium/high-risk operations automatically.

The internal result model uses a fixed status set: `success`, `failed`, `invalid_arguments`, `not_found`, `disabled`, `permission_denied`, `approval_required`, `rejected`, `expired`, `consumed`, `timed_out`, `cancelled`, and `internal_error`. It carries task/step IDs, tool name, safe summary, optional approval metadata, and a retry classification. API serialization may use the existing Phase 3 result shape plus task metadata.

## Task lifecycle/state machine

```text
create → queued → running
                     ├─ plan/select/validate
                     ├─ low-risk execute → observe → next step
                     ├─ medium/high approval_required → waiting_for_approval
                     │       ├─ approve → running → consume exactly once
                     │       ├─ reject/expire → failed (no execution)
                     │       └─ cancel → cancelled
                     ├─ bounded recoverable failure → next bounded attempt
                     ├─ limit/timeout/internal failure → failed
                     └─ no remaining steps → completed
```

Only legal transitions are accepted by the task service. Terminal states (`completed`, `failed`, `cancelled`) cannot resume. Approval waiting is durable, so restart/reload can resume only the exact approved request through the existing continuation endpoint.

## Execution flow

1. Validate `TaskCreateRequest` and create a queued task with a generated UUID.
2. Build an execution context with fixed limits and explicitly selected memory references.
3. Move to `running`; ask the deterministic planner for a bounded plan.
4. Validate plan size, step shape, and allowed tool names against registry metadata.
5. For each step, discover metadata (name, description, input schema summary, risk, enabled state, approval requirement) and validate arguments through `ToolRegistry`.
6. Call `ToolExecutor.execute`; never call a handler directly. Low-risk work runs automatically; medium/high risk returns a server-side approval request and moves the task to `waiting_for_approval`.
7. On approval, the API resumes the task by request ID; `ToolExecutor.continue_approved` remains responsible for atomic single-use consumption and revalidation.
8. Normalize and persist the result, record task/step/activity metadata, and let the recovery policy choose next step, bounded retry, wait, or terminal failure.
9. Complete with a safe summary, or fail/cancel with a classified reason. Never include full sensitive file content in task/audit history.

## Tool discovery and selection

Add a registry metadata method that returns only public fields: tool name, description, risk, enabled state, timeout, approval requirement, and an input schema summary. Do not expose handler callables, module paths, closures, or internal filesystem root values.

The selector must:

- reject unknown or disabled tools;
- validate arguments with the registered Pydantic model;
- reject extra/unexpected arguments according to the model policy;
- return a structured invalid-selection result;
- have no dynamic dispatch outside the registry.

## Permission and approval integration

The orchestrator must not add an `approved` flag and must not create an alternate permission path. It invokes the existing executor, which applies low-risk auto-execution and creates action-bound medium/high approvals. A task approval view contains only the server-generated request ID and safe preview. Approval continuation uses the stored tool name and normalized arguments, not client-supplied replacements.

Rejection, expiry, cancellation, and replay are terminal non-execution outcomes. A dangerous operation is never automatically retried after a failure or approval interruption.

## Error recovery and loop safety

Default limits should be conservative and configurable only within bounded ranges, for example:

- maximum 8 plan steps;
- maximum 8 tool executions;
- maximum 2 repeated failures for the same step;
- maximum 60 seconds per task in Phase 4A.

Retry only deterministic, explicitly classified transient failures and only before any medium/high-risk execution. Do not retry invalid arguments, permission denial, rejection, expiry, disabled tools, malformed results, or unknown tools. Enforce an overall task deadline and cancellation check between steps and before continuation. Unexpected exceptions become `internal_error` with a generic user-facing message and a redacted audit detail.

## Activity, audit, and memory

Record safe events for task creation, state changes, step planned, tool selected, approval requested/decision, execution started/succeeded/failed, retry classification, task completion/failure/cancellation. Include task ID, step number, tool name, status, approval ID, correlation ID, and timestamps. Reuse Phase 3 event redaction; never store secrets, raw exception tracebacks, full file contents, or unbounded planner text.

Memory is optional context only. The task request may specify memory IDs or a bounded query; the orchestrator retrieves data through the repository, records provenance/IDs, and passes it to the planner as quoted reference data. Memory content cannot introduce tools, alter limits, grant approval, or bypass schema/policy checks. No implicit “remembered instruction” is executable.

## API contracts

Preserve all Phase 1–3 routes. Add typed contracts in `schemas.py` and routes in `main.py`:

### `POST /api/v1/tasks`

Request:

```json
{"request":"echo hello","memory_ids":[],"limits":{"max_steps":8}}
```

Response `201`:

```json
{"task_id":"...","status":"queued","created_at":"..."}
```

The server clamps or rejects limits outside safe bounds.

### `GET /api/v1/tasks/{task_id}`

Returns task status, current step, timestamps, safe summary/error, and pending approval metadata when applicable.

### `GET /api/v1/tasks/{task_id}/steps`

Returns ordered step summaries and safe result metadata. It never returns secret/full-file audit payloads.

### `POST /api/v1/tasks/{task_id}/resume`

Resumes only a `waiting_for_approval` task using its stored approval request. The body contains no replacement tool or arguments. A consumed/rejected/expired request returns a terminal structured result and does not execute.

### `POST /api/v1/tasks/{task_id}/cancel`

Cancels queued/running/waiting tasks where possible, revokes pending approval state, and guarantees no future step starts. Active handlers remain bounded by existing executor timeouts; cancellation behavior must be tested.

### `GET /api/v1/tasks`

Returns recent task summaries with pagination/limit bounds for dashboard history.

All task endpoints use local UI actor metadata and existing error mapping. WebSocket streaming is not required for the first Phase 4A implementation; polling is sufficient and preserves the current frontend architecture.

## Dashboard plan

Extend the current dashboard without redesigning it:

- add a task composer action or task mode alongside existing chat;
- show active task ID, status, current step, selected tool, and bounded progress;
- reuse the approval card for `waiting_for_approval`;
- show completed/failed/cancelled summaries and a compact recent task history;
- disable duplicate resume/cancel actions while requests are pending;
- refresh task state/activity after approval and continuation.

The UI displays server-provided state only; it never evaluates risk or decides whether a tool is safe. No credentials or raw planner prompts are displayed.

## Proposed files

Create:

- `services/agent_api/app/tasks.py` — task/step models, statuses, transition validation.
- `services/agent_api/app/planner.py` — planner/selector protocols and deterministic implementation.
- `services/agent_api/app/orchestrator.py` — execution context, bounded loop, result normalization, recovery policy.
- `tests/test_phase4a.py` — lifecycle, loop, limits, selector, approval, recovery, memory, and audit tests.

Change minimally:

- `services/agent_api/app/memory.py` — task/step persistence and safe task event queries.
- `services/agent_api/app/schemas.py` — task request/response contracts.
- `services/agent_api/app/main.py` — task routes and lifecycle error mapping.
- `services/agent_api/app/tools.py` — public discovery metadata only if needed; preserve existing executor behavior.
- `apps/desktop/src/api.ts`, `App.tsx`, `styles.css`, and `App.test.tsx` — task progress/history UI and polling.
- `docs/SECURITY.md`, `docs/TOOL_SYSTEM.md`, `docs/ROADMAP.md`, and `README.md` — only after implementation and verification, to document the completed Phase 4A behavior.

Do not add dependencies unless a demonstrated implementation/test need exists.

## Testing strategy

Backend unit/integration tests should cover:

- task creation, IDs, timestamps, legal/illegal transitions, restart persistence;
- deterministic planning and registry discovery metadata;
- valid, unknown, disabled, and malformed tool selections;
- low-risk auto-execution and medium/high approval waiting;
- approve/continue once, rejection, expiry, cancellation, and replay prevention;
- tool failure, timeout, malformed result, unexpected exception, and safe recovery;
- retry, repeated-failure, step, execution, and wall-clock limits;
- memory references remain data and cannot alter plan/tool authorization;
- task/step/audit metadata is persisted without secrets or full file contents;
- all Phase 1–3 tests remain passing.

Frontend tests should cover task creation, progress states, approval reuse, completion/failure/cancellation rendering, and regression of chat/activity/approval UI. Run pytest, Ruff, compile checks, frontend tests, ESLint, TypeScript, production build, and runtime smoke tests for health, chat, Phase 2/3 tools, task creation, approval continuation, rejection, replay, cancellation, and limits.

## Implementation order

1. Add task/step domain models and legal transition tests.
2. Add SQLite task/step repositories and safe event queries.
3. Add planner/selector protocols plus deterministic fixtures.
4. Add result normalization and recovery/limit policy.
5. Implement orchestrator lifecycle using `ToolExecutor` only.
6. Add schemas/routes for create/status/steps/resume/cancel/history.
7. Add backend integration/security/regression tests.
8. Add compact dashboard task state/history and approval continuation handling.
9. Run full verification, review diff and secret scan, then create a dedicated Phase 4A checkpoint only after approval.

## Risks and edge cases

- Concurrent resume requests must be serialized by the existing single-use approval transaction and task state transition.
- A task may be restarted after process failure while an approval is pending; it must remain waiting, not re-plan or duplicate execution.
- Planner output may be malformed, overlong, or contain unknown tools; reject it as data.
- A result may be missing required fields or contain oversized content; normalize to a safe failure.
- Cancellation races with a handler start; task state and approval consumption must ensure no new step begins after cancellation.
- Memory may contain prompt-injection text; quote it as reference data and never treat it as instructions.
- Polling can observe stale UI state; server status remains authoritative and buttons must be idempotent.
- SQLite is durable local history, not tamper-proof forensic storage; retain the Phase 0 audit limitation.

## Exact definition of done

Phase 4A is complete only when:

1. Tasks and steps have durable IDs, statuses, timestamps, bounded limits, and legal transitions.
2. A deterministic/mock planner completes a safe multi-step lifecycle without a cloud API key.
3. Every selected tool is discovered and validated through the existing registry.
4. Low-risk tools auto-execute; medium/high tools pause for the existing server-side approval flow with no bypass.
5. Approve, reject, expire, replay, cancel, timeout, and failure paths are safe and audited.
6. Structured results and bounded recovery prevent infinite loops and dangerous blind retries.
7. Memory is optional quoted context and cannot grant execution authority.
8. Task APIs and dashboard state/history work while all Phase 1–3 APIs remain compatible.
9. Backend/frontend tests, lint, type checks, build, startup, runtime smoke, and security checks pass.
10. Documentation is updated only after implementation, and a reviewed local Phase 4A checkpoint is created.
