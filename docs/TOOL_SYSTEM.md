# USMAN JARVIS — Tool and Skill System

## Tool contract

Every tool is registered through a stable manifest plus an implementation adapter. The registry owns discoverability and schema validation; the policy engine owns authorization; the executor owns execution.

```text
ToolManifest
  id, version, display_name, description
  input_schema, output_schema
  risk_level, timeout_seconds, idempotency, supports_cancel
  required_capabilities, required_scopes, verification_strategy
  handler reference, health status
```

Tool inputs and outputs use JSON Schema and are normalized before audit logging. A result is `success`, `failed`, `cancelled`, or `timed_out`, with structured evidence, retry classification and safe user-facing summary. Tools must neither return raw secrets nor silently broaden a target scope.

## Invocation lifecycle

`discover → validate input → policy decision → approval (if required) → execute with deadline/cancellation → normalize result → verify → audit/event`.

Initial logical families are filesystem, terminal, application launcher, Windows control, browser, Git, GitHub, research, notifications, calendar, memory, database and voice. They become implementations only in later phases.

## Skills

Skills package task expertise rather than unrestricted code. A skill declaration lists capabilities, required tools, permissible risk ceiling, typed inputs/outputs, configuration, version and tests. The orchestrator may use a skill to propose a plan, but each tool invocation remains independently schema- and policy-checked. Skills can be disabled without removing tool implementations.

Example concept: `developer-workspace` requires workspace/Git/terminal tools and has no authority outside the user-selected project. `business-post-draft` can produce a draft but needs high-risk approval before any publication action.

## Reliability rules

Handlers are time-bounded, cancellation-aware and idempotent where feasible. Retry only transient, classified failures, with limited attempts and backoff. Verification is explicit: an app launch checks its process/window, a code change checks diff/tests, and a file move checks source/destination state. Reversible operations are preferred; otherwise the preview states that rollback is unavailable.

## Phase 2 implementation

The registry contains only low-risk `system_time`, `echo`, and `list_directory`. Directory requests are resolved from `SAFE_WORKSPACE` and paths outside that root are rejected. Execution validates input, checks enabled state and permission before invoking a handler, applies a timeout, records sanitized activity, and returns structured results. No shell, filesystem write/delete, browser, Windows-control, or MCP tool exists in this phase.
