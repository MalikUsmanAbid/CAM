# USMAN JARVIS — Security and Permission Model

## Principles

Least privilege, explicit consent, typed boundaries, durable auditability, secret isolation, and fail-safe cancellation govern all automation. Model output is untrusted input. JARVIS must not bypass MFA, CAPTCHA, logins, platform controls, or user intent.

## Risk levels

| Level | Examples | Default handling |
| --- | --- | --- |
| Low | Read a selected file, web research, status check, open an app | Permit only when tool/scope is enabled; audit |
| Medium | Modify code, move files, run a command, install a dependency, config change | Preview and explicit per-action or session-scoped approval |
| High | Delete material data, publish/send, account/security action, financial action, irreversible change | Detailed preview plus explicit action-specific confirmation; never background by default |

Risk may be raised by context: an apparently low-risk file operation outside a selected workspace, a browser action in an authenticated session, or a command with destructive arguments requires more scrutiny.

## Approval flow

1. The registry supplies tool metadata and policy declares a baseline risk.
2. The policy engine checks user configuration, target scope, task origin, account state and a non-expired approval grant.
3. If approval is required, it creates a tamper-evident preview: tool, target, normalized inputs, expected effect, risk, timeout and rollback/recovery option.
4. The UI offers approve once, deny, cancel task, and only when safe, a narrowly scoped time-limited grant.
5. Executor receives a signed internal approval reference—not a UI boolean—and rechecks it immediately before execution.

The emergency stop revokes pending and active task tokens; executors must poll cancellation around long-running work. Timeouts have both API/orchestrator and executor enforcement.

## Secrets and accounts

- Never commit API keys, refresh tokens or passwords. `.env` is local-only and ignored; documentation uses `.env.example` without values.
- Store secrets via an OS-backed credential vault/keyring; SQLite stores only opaque references and non-sensitive metadata.
- OAuth integrations use PKCE where applicable, least scopes, refresh-token rotation and revocation/disconnect flows.
- Log redaction occurs before persistence and UI streaming. Avoid prompts containing secret values.
- A local-only API should bind to loopback by default and use a per-install authenticated session. Any future remote/mobile access requires encrypted pairing, device revocation and explicit exposure controls.

## Audit and retention

Audit events record task ID, actor/source, tool/version, normalized non-secret inputs, risk decision, approval ID, timestamps, result summary, error classification and artifact references. Logs must be append-oriented and protected from ordinary UI mutation. Users need history export/delete controls; retention defaults must be documented before personal data is collected.

## Threats and mitigations

| Risk | Mitigation |
| --- | --- |
| Prompt injection from web/MCP/files | Treat external text as data; plan validation, tool schemas, policy enforcement, quoted source provenance |
| Over-broad filesystem/terminal access | Selected workspace/root scopes, allowlists, path resolution, typed operations, medium/high confirmation |
| Malicious/unreliable MCP server | Explicit registration, server trust state, capability review, health isolation, per-server/tool permission grants |
| Accidental destructive automation | Preview, action-specific approval, recycle-bin/recoverable default, cancellation, verification |
| Credential leakage | OS vault, redaction, no secrets in source/prompt/audit, OAuth scopes |
| Infinite/repeating agent behavior | Step, time, cost and retry budgets; task state machine; human escalation |

## Security acceptance baseline

Before any real executor ships: policy unit tests cover escalation; secrets are absent from tracked files/log fixtures; all tool calls have correlation/audit IDs; cancellation works during an active operation; and a denied/revoked approval cannot be replayed.

## Phase 2 boundaries

SQLite memory is application data, not a credential store. The repository rejects obvious API-key, password, and token patterns before create or update. Database and safe-workspace paths are local and Git-ignored. Only low-risk tools run automatically; medium/high-risk registrations return approval-required before a handler runs. No shell, write/delete, account, browser, or Windows automation is available.
