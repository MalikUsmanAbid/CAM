# USMAN JARVIS — MCP Architecture

## Role

MCP is an integration boundary, not the application's control plane. Native tools and MCP-provided tools appear through the same registry contract, but MCP servers retain distinct trust, health, configuration and permission records.

## Server lifecycle

1. User registers a server by local command, managed endpoint, or approved configuration template.
2. The manager validates configuration shape and records transport, ownership, requested capabilities and secret references.
3. It establishes a constrained session, discovers tools/resources/prompts, and maps only approved tools into registry entries namespaced as `mcp.<server>.<tool>`.
4. It health-checks with bounded calls and marks failures degraded/offline without blocking native features.
5. User can enable, disable, refresh discovery, revoke grants, or remove the server. Removal deletes configuration references, not unrelated secrets unless explicitly requested.

## Isolation and permissions

Each server has an allowlist of exposed tools, maximum risk, permitted account scopes, timeout/concurrency limits and optional workspace restrictions. Discovery never auto-approves execution. MCP error payloads are treated as untrusted data and redacted/audited. Credential material belongs in the OS secret store, keyed by a server/configuration reference.

## Interfaces and observability

`McpServerRepository`, `McpClientAdapter`, `McpDiscoveryService`, and `McpHealthService` keep a transport/provider implementation replaceable. Record server version, protocol capability, health, last discovery, tool manifest hash and recent sanitized error state. Contract tests use a local fake server; no production account is required.
