"""Safe Phase 5 foundations: metadata/state only, never execution adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from services.agent_api.app.permissions import RiskLevel


class FoundationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MCPServer(FoundationModel):
    server_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,99}$")
    name: str = Field(min_length=1, max_length=100)
    endpoint: str = Field(min_length=1, max_length=300)
    secret_reference: str | None = Field(default=None, max_length=200)
    approved: bool = False
    enabled: bool = False
    status: str = Field(default="unavailable", max_length=30)
    discovered_tools: list[str] = Field(default_factory=list, max_length=20)


class MCPRegistry:
    """Configuration registry only; discovery/transport is deliberately deferred."""

    def __init__(self) -> None:
        self._items: dict[str, MCPServer] = {}

    def register(self, server: MCPServer) -> MCPServer:
        if server.server_id in self._items or server.enabled:
            raise ValueError("MCP server is already registered.")
        self._items[server.server_id] = server
        return server

    def get(self, server_id: str) -> MCPServer:
        return self._items[server_id]

    def list(self) -> list[MCPServer]:
        return sorted(self._items.values(), key=lambda item: item.server_id)

    def set_enabled(self, server_id: str, enabled: bool) -> MCPServer:
        current = self.get(server_id)
        if enabled and not current.approved:
            raise ValueError("MCP server must be explicitly approved before enablement.")
        item = current.model_copy(update={"enabled": enabled, "status": "enabled" if enabled else "disabled"})
        self._items[server_id] = item
        return item

    def approve(self, server_id: str) -> MCPServer:
        item = self.get(server_id).model_copy(update={"approved": True, "status": "approved"})
        self._items[server_id] = item
        return item

    def record_discovery(self, server_id: str, tool_ids: list[str]) -> MCPServer:
        """Accept only bounded, identifier-only discovery output; no transport is invoked."""
        if len(tool_ids) > 20 or any(not item.replace("_", "").isalnum() for item in tool_ids):
            raise ValueError("MCP discovery metadata is invalid.")
        item = self.get(server_id).model_copy(update={"discovered_tools": sorted(set(tool_ids)), "status": "discovered"})
        self._items[server_id] = item
        return item

    def public(self, server: MCPServer) -> dict[str, object]:
        """Never expose secret references or values in public API payloads."""
        return server.model_dump(exclude={"secret_reference"})


class ControlledAction(FoundationModel):
    action_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,99}$")
    domain: str = Field(pattern=r"^(browser|computer)$")
    description: str = Field(min_length=1, max_length=300)
    risk: RiskLevel
    requires_approval: bool = True
    max_input_characters: int = Field(default=500, ge=1, le=4_000)


class ActionRegistry:
    def __init__(self) -> None:
        self._items: dict[str, ControlledAction] = {}

    def register(self, action: ControlledAction) -> ControlledAction:
        if action.action_id in self._items:
            raise ValueError("Action is already registered.")
        self._items[action.action_id] = action
        return action

    def list(self) -> list[ControlledAction]:
        return sorted(self._items.values(), key=lambda item: item.action_id)

    def get(self, action_id: str) -> ControlledAction:
        return self._items[action_id]


class BrowserPolicy(FoundationModel):
    allowed_domains: list[str] = Field(default_factory=list, max_length=20)
    max_navigation_steps: int = Field(default=8, ge=1, le=20)
    max_content_characters: int = Field(default=20_000, ge=1, le=50_000)

    def permits(self, url: str) -> bool:
        from urllib.parse import urlparse

        host = urlparse(url).hostname or ""
        return bool(host and any(host == domain or host.endswith(f".{domain}") for domain in self.allowed_domains))


class WorkflowRegistry:
    """State tracking only; outreach and submission require external approved tools."""

    def __init__(self) -> None:
        self._items: dict[str, Workflow] = {}

    def create(self, workflow: Workflow) -> Workflow:
        if workflow.fabricated_data or workflow.workflow_id in self._items:
            raise ValueError("Workflow is invalid.")
        self._items[workflow.workflow_id] = workflow
        return workflow

    def get(self, workflow_id: str) -> Workflow:
        return self._items[workflow_id]

    def transition(self, workflow_id: str, status: WorkflowStatus) -> Workflow:
        item = self.get(workflow_id)
        if item.fabricated_data:
            raise ValueError("Fabricated data is not permitted.")
        updated = item.model_copy(update={"status": status})
        self._items[workflow_id] = updated
        return updated

    def list(self) -> list[Workflow]:
        return sorted(self._items.values(), key=lambda item: item.workflow_id)


class WorkflowStatus(StrEnum):
    DRAFT = "draft"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    READY = "ready"
    COMPLETED = "completed"


class Workflow(FoundationModel):
    workflow_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,99}$")
    kind: str = Field(min_length=1, max_length=50)
    status: WorkflowStatus = WorkflowStatus.DRAFT
    notes: list[str] = Field(default_factory=list, max_length=20)
    fabricated_data: bool = False


class ScheduledTask(FoundationModel):
    schedule_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,99}$")
    task_id: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=100)
    status: str = Field(default="scheduled", max_length=30)
    retry_limit: int = Field(default=0, ge=0, le=3)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Scheduler:
    """Bounded scheduler state; it does not run tasks or authorize actions."""

    def __init__(self) -> None:
        self._items: dict[str, ScheduledTask] = {}
        self._keys: set[str] = set()

    def schedule(self, item: ScheduledTask) -> ScheduledTask:
        if item.idempotency_key in self._keys or item.schedule_id in self._items:
            raise ValueError("Duplicate scheduled task.")
        self._items[item.schedule_id] = item
        self._keys.add(item.idempotency_key)
        return item

    def cancel(self, schedule_id: str) -> ScheduledTask:
        item = self._items[schedule_id].model_copy(update={"status": "cancelled"})
        self._items[schedule_id] = item
        return item

    def list(self) -> list[ScheduledTask]:
        return sorted(self._items.values(), key=lambda item: item.schedule_id)
