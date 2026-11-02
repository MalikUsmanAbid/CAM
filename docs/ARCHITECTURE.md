# USMAN JARVIS — Architecture

## Purpose and scope

USMAN JARVIS is a Windows-first personal assistant. The repository contains a runnable local FastAPI backend and React dashboard, with durable tasks and memory, bounded planning, approval-gated tools, and an audit trail. This document also describes longer-term target architecture; it does not imply that every listed subsystem is implemented.

The current development deployment runs one API process and SQLite database locally and serves the React dashboard in a browser. Tauri packaging is pending. Protected API routes require a process-local paired session in addition to loopback, Host and Origin checks. Health and pairing bootstrap are public local endpoints. Remote models require explicit transmission consent, configured pricing and positive budgets; cloud allowance defaults to zero.

Projects have canonical roots and separate memory/context, tasks, approvals and event queries. Personal legacy records remain in personal scope. Project execution uses its own filesystem registry rather than inheriting personal MCP/computer capabilities. Approvals bind session, project, current policy revision, arguments, tool contract and resolved execution scope. The executor rechecks permission at dispatch; project instructions are bounded untrusted context.

All repository initialization uses the same transactional migration ledger and checksums. Existing databases require explicit migration authorization and receive a consistent SQLite backup before upgrade. Current-schema damage fails closed. Default Windows persistence is `%LOCALAPPDATA%/USMANJARVIS`; `JARVIS_DATA_DIR` supports portable data, and active databases on detected cloud-synced or network paths are rejected. Detection covers known OneDrive/Dropbox directories and configured roots; it cannot discover every third-party sync provider.

Windows credentials are protected using user-scoped DPAPI in backend credential files, referenced by opaque `vault:` identifiers. Environment references remain supported. Model credentials are resolved again before transport dispatch, and revocation disconnects matching MCP transports. These credentials are excluded from ordinary backup/export scope; moving them to another Windows account does not provide a supported recovery method.

## System overview

```text
Dashboard (React/TypeScript, Vite; Tauri packaging deferred)
  ├─ Chat, task history/results, approvals, saved memory, activity
  └─ HTTP commands and status queries
             │
Local Agent API (Python/FastAPI)
  ├─ Task service ── Orchestrator ── Model router/providers
  ├─ Permission service / policy engine
  ├─ Tool registry ── native tools / MCP gateway
  ├─ Memory service ── repository layer ── SQLite (initially)
  ├─ Scheduler / notification adapters
  └─ Structured audit and health services
             │
Controlled executors
  ├─ Windows computer-control adapter
  ├─ Browser adapter
  ├─ Developer-workspace adapter
  └─ External APIs and MCP servers
```

## Component responsibilities

| Component | Responsibility | Must not do |
| --- | --- | --- |
| Desktop UI | Render state, collect requests, show approvals and stop controls | Directly execute OS actions or store secrets |
| API/task service | Enforce paired sessions and loopback/Host/Origin checks, validate requests, bind project scope and create/cancel tasks | Assume loopback proves user authorization or interpret arbitrary model output as executable work |
| Orchestrator | Run the bounded plan/act/observe/verify loop and persist task state | Bypass policy or call the OS directly |
| Model router | Classify work and choose/fail over provider adapters | Own tool permissions or retain secrets in prompts |
| Tool registry | Validate schemas, expose metadata, route approved tool calls, normalize errors | Grant permissions itself |
| Policy engine | Evaluate risk, scope, approvals, cancellation and time limits | Execute actions |
| Executors | Carry out tightly scoped filesystem, Windows, browser, developer, or MCP requests | Accept free-form shell/model text without validation |
| Memory service | Store/retrieve scoped, attributable memories | Treat untrusted retrieved text as instructions |
| MCP gateway | Register, discover, health-check and proxy approved MCP tools | Trust a server's tool metadata as safe |

## Core data flow

1. The UI submits a typed `TaskRequest`; the API validates it and creates a durable task record.
2. The orchestrator obtains relevant task context and explicitly selected memory, then asks the model router for a proposed plan.

For model-assisted task planning, the bounded flow is ContextBuilder → BrainProvider →
ModelPlanner → existing Orchestrator → Permission/Approval → ToolExecutor → verification
and audit. ModelPlanner validates every proposed step against the Tool Registry and input
schemas; it never executes tools, grants permissions, or bypasses approval. Provider failure
or invalid model output falls back to the deterministic planner.

## Phase 5.1 capability foundation

The capability registry provides bounded metadata for currently registered built-in tools.
The capability resolver performs deterministic lookup and returns `waiting_for_capability`
when a requested capability is unavailable; it never executes or grants permissions. The
skill registry validates reusable skill metadata and required registered capabilities, but
skills receive no implicit authority and cannot alter policy. Tasks may pause in
`waiting_for_capability` and resume after a capability is registered. Live MCP server
configuration, browser and computer-control adapters, voice, and autonomous self-extension
remain future work. Phase 6 adds a separately configured, approval-gated MCP adapter as described below.
3. It validates the plan against task limits and translates each proposed operation into a registry tool invocation.
4. Policy evaluates the invocation's risk, scope, server/tool status, and any valid approval. If needed, the UI receives an action preview and a pending-approval event.
5. An executor performs the approved, time-bounded call and returns typed evidence.
6. The orchestrator observes and verifies the evidence. It retries only according to a bounded recovery policy; otherwise it pauses, fails safely, or asks the user.
7. Events, tool calls, approvals and artifacts are written to the audit/task history. A final report is sent to the UI.

## Agent execution loop

```text
queued → understand → plan → validate-plan → select-tool
       → approval-pending? → execute → observe → verify
       ↘ recover/replan (bounded) ──────────────────────┘
       → completed | failed | cancelled | needs-user-input
```

Task state is durable. Each state transition has an event timestamp, actor, correlation ID, and reason. Plans are advisory; only schema-valid registry calls approved by the policy engine can run. Important actions require an independent verification step (for example, check a process opened, test command exit status, or inspect a Git diff).

## Provider and event abstractions

`ModelProvider` should expose capabilities, availability, `generate_plan`, `generate_response`, and optional streaming—without leaking a provider-specific message format to the rest of the application. Routing uses configured availability, locality/cost preference, task capability and fallback order. Initial adapters may target an OpenAI-compatible API and a local Ollama-compatible API.

The API publishes typed task events over WebSocket: `task.created`, `task.state_changed`, `approval.requested`, `tool.started`, `tool.finished`, `task.reported`, and `notification.created`. HTTP remains suitable for commands, history, settings and health checks. The event contract should be versioned from the beginning.

## Memory architecture

Memory is separate from execution state:

| Store | Contents | Retention/use |
| --- | --- | --- |
| Task context | Current plan, step results, temporary notes | Bound to one task; expires/archives |
| Application state | Settings, tool registrations, approvals, schedules, account tokens references | Durable operational data |
| Long-term memory | User preferences, project records, useful summaries and explicitly saved instructions | Explicit provenance, scope, review/delete controls |

Use repository interfaces (`TaskRepository`, `MemoryRepository`, `AuditRepository`, `SettingsRepository`) over SQLite first. Semantic retrieval can begin with SQLite full-text search and add an optional local vector index later. Store memory records with source, confidence, scope, sensitivity and timestamps. Retrieved memories are reference material, never executable instructions.

## Voice architecture

The future voice path is `MicrophoneAdapter → STTProvider → TaskRequest → orchestrator → TTSProvider → UI/audio output`. Separate provider interfaces support local or cloud STT/TTS. A voice session state machine (`idle`, `listening`, `transcribing`, `thinking`, `speaking`, `interrupted`, `error`) enables interruption, visualisation and privacy controls. Wake-word processing must remain optional and local where possible.

## Future subsystem boundaries

- **Windows agent:** a controlled `ComputerController` interface for app launch, window queries, keyboard/mouse, screenshot, clipboard, terminal and process operations. It uses operation-specific typed requests, target allowlists, foreground-window checks where useful, and explicit high-risk policy gates.
- **Developer agent:** a `WorkspaceService` isolates project discovery, Git inspection, command execution, edits, test/build runs and diff review. Commands are structured templates or user-confirmed commands, constrained to a selected workspace.
- **Browser agent:** a `BrowserService` supports search, navigation, extraction and interaction. Prefer APIs/MCP for reliable data; browser automation is a fallback. Each browser action returns evidence (URL, extracted data, screenshot/reference) and respects account/confirmation controls.
- **Automation:** `Scheduler` adapters create one-off and recurring jobs which enter the same task/permission pipeline; background jobs never receive implicit escalation.
- **Mobile companion:** a future mobile client consumes the same versioned HTTP/WebSocket API. The local desktop agent remains the authority; remote access requires an explicit secure pairing/relay design, not an exposed unauthenticated local API.

## Proposed folder structure

```text
apps/
  desktop/                 # Tauri shell + React client
services/
  agent_api/
    app/
      api/ domain/ application/ infrastructure/
      orchestrator/ models/ tools/ policy/ memory/ mcp/
      scheduler/ audit/ integrations/
packages/
  contracts/               # Versioned JSON Schema/OpenAPI/TypeScript types
  test_fixtures/
docs/
tests/
  unit/ integration/ contract/ e2e/ security/
```

This is a long-term target structure; the current implementation intentionally remains smaller.

## Technology decision

The current implementation uses **Python + FastAPI**, **React + TypeScript + Vite**, **SQLite**, and Pydantic for the local API, dashboard, and persistence. Tauri packaging and WebSocket events remain deferred. Browser automation stays disabled pending verified Windows OS-level network isolation; computer control remains dependent on an optional Windows UI Automation backend and real-application testing.

This offers strong Windows support, efficient desktop packaging, a productive Python ecosystem for agents/automation, type-safe UI contracts and a clean path to a mobile client without coupling the backend to Tauri. Electron is a valid alternative with a larger Node ecosystem but a heavier runtime. A pure Python desktop UI is simpler initially but gives less reusable, rich UI/mobile-adjacent frontend capability. Cloud-only backend deployment is rejected for the first version because it complicates local OS control and privacy.

## Implemented Phase 5 foundations

Phase 5 adds strict capability and skill registries, deterministic capability resolution,
`WAITING_FOR_CAPABILITY` task state, and metadata-only MCP, browser/computer action,
workflow, and idempotent scheduler models. These models do not connect to network
transports, browsers, desktop input, or background execution. Future adapters must use the
existing Tool Registry → Permission → Approval → ToolExecutor → Audit chain. Autonomous
self-extension, live browser automation, computer control, voice, and account automation
remain deferred.

MCP server records accept a secret reference but deliberately omit it from public metadata;
discovery records identifier-only tool metadata and cannot register executable handlers.
Browser policy supplies only an allowlist and bounded action metadata. Client and application
workflows are drafts/state tracking: consequential outreach or submission remains unavailable
without an approved future tool and user approval. The scheduler provides idempotent schedule
and cancellation state only; it cannot execute or authorize any task.

Phase 6 adds an explicitly selected provider-neutral live brain adapter using an
OpenAI-compatible HTTP contract. It remains unavailable until an endpoint and credential
are configured. Credentials resolve only through named environment references or the
existing configured credential setting; they are omitted from status, logs and persisted
records. Provider failures and unmet task requirements use the deterministic planner.

Browser sessions are currently disabled. Chromium's HTTP proxy configuration and WebRTC
flags do not enforce process-wide egress: WebRTC can use direct TCP, and other browser
network paths may also open sockets outside the proxy. The application does not launch
Chromium until Windows OS-level network isolation can block direct TCP and UDP while
allowing traffic only through the destination-validating proxy, and that isolation has been
verified on Windows. The existing proxy still validates and pins destinations for its own
connections, including redirects and TLS CONNECT tunnels, but this alone is not sufficient
to mark browser traffic safe. Browser content remains untrusted, and the adapter fails closed
rather than opening an unrestricted browser session.

Windows computer control uses optional `pywinauto` with its UI Automation backend. It
attaches only to explicitly selected visible windows at the current user's privilege level.
The UIA backend works best with applications that expose accessible controls; custom-drawn
surfaces may offer little inspectable state. Elevated applications are intentionally
unavailable, and the adapter does not request UIAccess or attempt to cross privilege
boundaries. Actions remain bounded and approval-gated.

The Phase 6 MCP adapter uses the official Python SDK with explicitly configured HTTPS
Streamable HTTP servers. Servers are disabled until separately approved and enabled; the
SDK client is forced through a loopback forward proxy that validates and pins the destination
IP at each connection. Loopback, private, link-local, multicast, reserved, and other
non-global destinations are rejected. Discovery and invocation are bounded, and all
discovered calls require approval through the existing Tool Registry → Validation →
Permission/Approval → ToolExecutor → Audit boundary. Server credential references are not
returned from configuration routes. Enabled servers require reconnect after application
restart.

The unauthenticated API is local-only. `BACKEND_HOST` accepts only `localhost` or a loopback
IP, and middleware rejects non-loopback peer addresses, non-local Host values, and browser
Origins other than the two local development frontend origins. CORS remains a browser policy,
not authentication. Remote API access and reverse-proxy exposure are unsupported; adding
either requires an authenticated deployment design first. The desktop uses the loopback API
base URL by default.

### Phase 6 partial checkpoint status

Completed and tested locally: provider-neutral brain request/response contracts, explicit
provider selection, deterministic planning fallback, MCP server configuration and
ToolExecutor-gated tool registration, orchestration approval resume/cancellation/timeouts/
restart recovery, loopback API restrictions, and browser fail-closed behavior. These checks
use local tests and mocked provider/server responses where noted; they do not establish that
external services are available or compatible.

Partial or unverified: browser sessions remain unavailable pending Windows OS-level network
isolation implementation and testing. Live brain requests require a configured provider
endpoint and credential; no credential is included in this checkpoint. MCP external server
behavior has not been verified against a real server. Real desktop control requires a
compatible Windows UI Automation environment and the optional `pywinauto` dependency; its
operation against real desktop applications remains unverified. Cancellation cannot undo a
remote operation already accepted by an MCP server or stop a blocking UI Automation call
already running in its worker thread. These operations are not automatically replayed after
cancellation, timeout, or restart; inspect the audit trail and external state when completion
is uncertain.
