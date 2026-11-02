# USMAN JARVIS — Implementation Roadmap

Each phase ends with a small commit/checkpoint, a reviewed threat-model update, automated tests, and a manual acceptance pass. Do not start the next phase with known critical permission, data-loss, or secret-handling defects.

## Phase 0 — Architecture and specification

- **Objective/components:** Establish contracts, architecture, security, tool/MCP designs, roadmap and tests.
- **Expected files:** `docs/ARCHITECTURE.md`, `SECURITY.md`, `TOOL_SYSTEM.md`, `MCP_ARCHITECTURE.md`, `ROADMAP.md`.
- **Dependencies:** None. **Tests:** document review and architecture decisions. **Security:** threat model and risk levels defined.
- **Complete when:** these documents are approved. **Failure points:** premature framework lock-in or unscoped OS authority.

## Phase 1 — Core application, provider abstraction and basic UI

- **Objective/components:** Tauri/React shell, FastAPI local API, task model, WebSocket event stream, provider interface, configuration and audit skeleton.
- **Expected modules:** `apps/desktop`, `services/agent_api/app/{api,domain,models,audit}`, `packages/contracts`.
- **Dependencies:** Phase 0 approval. **Tests:** API/UI smoke, provider fake contract, task-state transitions, WebSocket event contract. **Security:** loopback-only API, authenticated local session, config validation/redaction.
- **Complete when:** chat request produces a non-tool, streamed model response and durable task history. **Failure points:** UI/backend contract drift, provider coupling, unbounded streaming.

## Phase 2 — Tool registry, permissions and memory

- **Objective/components:** registry, policy/approvals, SQLite repositories/migrations, task evidence and initial memory CRUD/retrieval.
- **Expected modules:** `tools`, `policy`, `memory`, `infrastructure/db`, `contracts`.
- **Dependencies:** Phase 1. **Tests:** schemas, policy matrix, cancelled/expired approvals, migrations, audit redaction, memory scope. **Security:** OS secret vault interface and no tool execution before authorization.
- **Complete when:** a fake safe tool follows validate/approve/audit/verify lifecycle. **Failure points:** approval bypass, memory prompt injection, schema ambiguity.

## Phase 3 — Windows tools

- **Objective/components:** scoped app launcher, windows/process query, clipboard/screenshots and controlled terminal foundation.
- **Expected modules:** `integrations/windows`, `tools/windows`, policy rules.
- **Dependencies:** Phase 2. **Tests:** mocked controller, manual Windows VM/sandbox checks, cancellation/timeout, target-scope tests. **Security:** no global shell authority; confirmation for commands and write effects.
- **Complete when:** approved scoped actions execute and return verification evidence. **Failure points:** focus/race conditions, unsafe input injection, elevation assumptions.

## Phase 4 — Developer Agent

- **Objective/components:** project selection, repository inspection, Git status/diff, bounded commands, edits, test/build and change report.
- **Expected modules:** `developer_agent`, `workspace`, `tools/git`, `tools/terminal`.
- **Dependencies:** Phase 3 and selected-workspace UI. **Tests:** fixture repositories, failed build recovery, diff/report snapshots, path escape tests. **Security:** workspace sandboxing, medium-risk edits, explicit dependency/install approval.
- **Complete when:** it completes a simple fixture bug fix with tests and a reviewable diff. **Failure points:** shell injection, unintended edits, false success after failed tests.

## Phase 5 — Browser Agent

- **Objective/components:** search, page reading/extraction, navigation and controlled automation; API-first adapters.
- **Expected modules:** `browser_agent`, `integrations/browser`, research skill.
- **Dependencies:** Phase 2 policy and browser runtime. **Tests:** local test pages, navigation/extraction contracts, injection fixtures, authenticated-action denial. **Security:** isolate browser profile; approval for authenticated/submitting actions.
- **Complete when:** it completes bounded research with cited evidence and no account side effects. **Failure points:** page changes, prompt injection, fragile selectors.

## Phase 6 — MCP Manager

- **Objective/components:** registration, discovery, enablement, health, tool mapping and server permission UI.
- **Expected modules:** `mcp` services/repositories/adapters and settings views.
- **Dependencies:** Phase 2 registry/policy. **Tests:** fake MCP server contracts, offline recovery, revoke/disable behavior. **Security:** per-server tool grants, isolated config/secrets.
- **Complete when:** an approved fake server's one safe tool is usable and revocable. **Failure points:** discovery trust, protocol/version failures, leaking configuration.

## Phase 7 — Voice system

- **Objective/components:** STT/TTS adapters, session state machine, interruption, push-to-talk and visualisation; optional wake word.
- **Expected modules:** `voice`, audio UI, provider adapters.
- **Dependencies:** Phase 1 events and Phase 2 task pipeline. **Tests:** audio fixtures, interruption state tests, provider fallback. **Security:** opt-in recording, clear listening indicator, local preference.
- **Complete when:** push-to-talk request yields an interruptible spoken response. **Failure points:** latency, transcription errors, accidental activation.

## Phase 8 — Automation and notifications

- **Objective/components:** scheduler abstraction, reminders, recurring tasks, notification adapters, history.
- **Expected modules:** `scheduler`, `notifications`, schedule UI.
- **Dependencies:** Phase 2 and OS notification integration. **Tests:** clock-controlled schedule tests, duplicate/missed job recovery, notification contracts. **Security:** scheduled tasks retain their original risk rules and cannot inherit broad approval.
- **Complete when:** reminders and safe recurring jobs execute reliably with records. **Failure points:** timezone/DST, duplicate execution, stale permissions.

## Phase 9 — Account integrations

- **Objective/components:** OAuth account manager and scoped adapters for Google, GitHub, email/calendar/social/business services.
- **Expected modules:** `accounts`, `integrations/<service>`, credential vault adapter.
- **Dependencies:** Phase 2 secrets/policy and Phase 6 where MCP is used. **Tests:** mocked OAuth/API contracts, disconnect/revoke, scope denial. **Security:** PKCE, least scopes, no passwords/MFA bypass, high-risk confirmation for sending/publishing.
- **Complete when:** one OAuth integration connects, performs a safe read, and cleanly revokes. **Failure points:** token lifecycle, API quota, accidental write actions.

## Phase 10 — Work, earning and business skills

- **Objective/components:** opportunity research/tracking, draft preparation, workflow organization and marketing support.
- **Expected modules:** skills and domain repositories/UI.
- **Dependencies:** Browser, accounts and automation as applicable. **Tests:** source attribution, draft-only defaults, approval gates. **Security:** prohibit impersonation, spam, fabricated credentials and financial commitments.
- **Complete when:** it tracks opportunities and produces clearly labelled drafts. **Failure points:** unreliable sources, deceptive output, platform policy violations.

## Phase 11 — Mobile companion architecture

- **Objective/components:** API compatibility review, mobile client prototype, secure pairing/notification relay design.
- **Expected modules:** `apps/mobile` and pairing contracts, only after design validation.
- **Dependencies:** stable Phase 1 contracts and remote-access security design. **Tests:** contract compatibility, pairing/revocation, offline state. **Security:** device-bound credentials, encrypted transport, no unauthenticated LAN exposure.
- **Complete when:** a paired prototype can view status/send a bounded request securely. **Failure points:** remote attack surface, notification privacy, platform background limits.

## Cross-phase testing strategy

Unit-test domain/policy logic; use contract tests for providers, tools and MCP; integration-test SQLite/API/event boundaries; use fixture repositories and pages for developer/browser work; reserve end-to-end Windows tests for disposable environments. Security regression tests must cover approval escalation, path traversal, secret redaction, cancellation and prompt-injection-resistant behavior. Manual usability/permission review is required before expanding risk scope.
