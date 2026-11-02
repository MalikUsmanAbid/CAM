"""Schema-validated tools with safe-root enforcement and approval gating."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from services.agent_api.app.action_policy import ActionCategory, ActionPolicy, category_for
from services.agent_api.app.memory import Approval, MemoryRepository
from services.agent_api.app.permissions import RiskLevel, canonical_arguments, is_allowed
from services.agent_api.app.session_security import (
    execution_authorized,
    execution_dispatch_authorized,
)

MAX_FILE_BYTES = 64_000
_approved_scope: ContextVar[dict[str, Any] | None] = ContextVar("approved_scope", default=None)
_approved_deadline: ContextVar[datetime | None] = ContextVar("approved_deadline", default=None)
_approved_revision: ContextVar[int | None] = ContextVar("approved_revision", default=None)


class ToolInput(BaseModel):
    """Tool contracts reject unknown arguments rather than silently ignoring them."""

    model_config = ConfigDict(extra="forbid")


class EmptyInput(ToolInput):
    pass


class EchoInput(ToolInput):
    message: str = Field(min_length=1, max_length=1000)


class DirectoryInput(ToolInput):
    path: str = Field(default="", max_length=500)


class ReadFileInput(ToolInput):
    path: str = Field(min_length=1, max_length=500)
    max_bytes: int = Field(default=MAX_FILE_BYTES, ge=1, le=MAX_FILE_BYTES)


class WriteFileInput(ToolInput):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=MAX_FILE_BYTES)
    overwrite: bool = False


class CreateDirectoryInput(ToolInput):
    path: str = Field(min_length=1, max_length=500)


class WorkspaceGuard:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative: str, require_exists: bool = False) -> Path:
        if not execution_authorized.get()():
            raise _PolicyDenied
        deadline = _approved_deadline.get()
        if deadline is not None and datetime.now(UTC) >= deadline:
            raise _InvalidApproval("Approval expired before its effect; request fresh approval.")
        if not relative or "\x00" in relative or re.match(r"^(?:[A-Za-z]:|\\\\|/|\\)", relative):
            raise ValueError("Path must be a safe relative workspace path.")
        candidate = self.root / relative
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise ValueError("Path is outside the safe workspace.")
        parent = resolved if resolved.exists() else resolved.parent
        if not parent.resolve(strict=False).is_relative_to(self.root):
            raise ValueError("Path is outside the safe workspace.")
        if require_exists and not resolved.exists():
            raise ValueError("Workspace target does not exist.")
        scope = _approved_scope.get()
        if scope is not None and (
            scope.get("workspace_root") != str(self.root.resolve())
            or (scope.get("destination") != str(resolved) and str(resolved) not in scope.get("destinations", []))
        ):
            raise _InvalidApproval("Workspace destination changed; request fresh approval.")
        return resolved


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    risk: RiskLevel
    timeout_seconds: float
    handler: Callable[[BaseModel], Awaitable[dict[str, Any]]]
    enabled: bool = True
    resolve_destination: Callable[[dict[str, Any]], Path] | None = None
    category: ActionCategory | None = None
    preview_details: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    resolve_destinations: Callable[[dict[str, Any]], list[Path]] | None = None


class ToolRegistry:
    def __init__(self, workspace_root: Path | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self.workspace_root = workspace_root

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(name)
        return self._tools[name]

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def set_enabled(self, name: str, enabled: bool) -> None:
        self.get(name).enabled = enabled

    def unregister(self, name: str) -> None:
        self._tools.pop(name)

    def validate_input(self, name: str, arguments: dict[str, Any]) -> BaseModel:
        return self.get(name).input_model.model_validate(arguments)


class _InvalidApproval(ValueError):
    pass


class _PermissionRequired(Exception):
    pass


class _PolicyDenied(Exception):
    pass


class ToolExecutionOutcome(Exception):
    """An executor completed with evidence of an unsuccessful action."""

    def __init__(self, result):
        self.result = result
        super().__init__("Execution did not succeed.")


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, memory: MemoryRepository) -> None:
        self.registry, self.memory = registry, memory
        self.policy = ActionPolicy(memory.database_path)

    async def execute(self, name: str, arguments: dict[str, Any], *, task_id: str | None = None, step_number: int | None = None) -> dict[str, Any]:
        if not execution_authorized.get()():
            return self._result(name, "denied", "Execution session expired or was revoked.")
        try:
            tool, validated = self.registry.get(name), self.registry.validate_input(name, arguments)
            if not tool.enabled:
                return self._result(name, "disabled", "This tool is disabled.")
            normalized = validated.model_dump()
            category = category_for(tool)
            if self.policy.denied(category):
                return self._result(name, "denied", "The current action policy blocks this category.")
            if not is_allowed(tool.risk) or not self.policy.allowed(category):
                approval = self.memory.create_approval(
                    name, normalized, str(tool.risk), self._preview(name, normalized),
                    task_id=task_id, step_number=step_number,
                )
                return self._result(
                    name, "approval_required", "Approval is required.", approval=approval
                )
            return await self._invoke(name, validated, task_id=task_id, step_number=step_number)
        except KeyError:
            return self._result(name, "not_found", "Tool was not found.")
        except (ValidationError, ValueError):
            return self._result(name, "invalid_arguments", "Tool arguments are invalid.")

    async def continue_approved(self, request_id: str, *, task_id: str | None = None) -> dict[str, Any]:
        current = self.memory.get_approval(request_id)
        if self.memory.approval_owner(request_id) != task_id:
            return self._result(current.tool_name, "task_owned", "Task actions must resume through their owning task.")
        if current.status != "approved":
            return self._result(
                current.tool_name,
                current.status,
                "Approval cannot be used.",
                approval=current,
            )
        approval = self.memory.consume_approval(request_id, task_id)
        if approval is None:
            current = self.memory.get_approval(request_id)
            return self._result(
                current.tool_name, current.status, "Approval cannot be used.", approval=current
            )
        try:
            if not self._approval_matches(approval):
                return self._result(approval.tool_name, "invalid_approval", "Execution scope or contract changed; request a fresh approval.")
            return await self._invoke(
                approval.tool_name,
                self.registry.validate_input(approval.tool_name, approval.arguments),
                task_id=task_id, approval=approval,
            )
        except (KeyError, ValidationError, ValueError):
            return self._result(
                approval.tool_name, "invalid_arguments", "Approved action is invalid."
            )

    async def _invoke(self, name: str, validated: BaseModel, *, task_id: str | None = None, step_number: int | None = None, approval: Approval | None = None) -> dict[str, Any]:
        tool = self.registry.get(name)
        if not tool.enabled:
            return self._result(name, "disabled", "This tool is disabled.")
        if task_id is not None and self.memory.get_task(task_id)["status"] != "running":
            return self._result(name, "cancelled", "Task is not available for execution.")
        try:
            inherited_authorize = execution_dispatch_authorized.get()
            def authorize():
                # wait_for schedules a new coroutine; check again at actual dispatch.
                inherited_authorize()
                if not execution_authorized.get()():
                    raise _PolicyDenied
                if not tool.enabled:
                    raise ValueError("Tool was disabled before dispatch.")
                if self.registry.get(name) is not tool:
                    raise ValueError("Tool changed before dispatch.")
                if approval is not None and (
                    not self._approval_matches(approval)
                    or self._arguments_hash(validated.model_dump()) != approval.preview.get("arguments_sha256")
                ):
                    raise _InvalidApproval("Execution scope or contract changed; request a fresh approval.")
                if task_id is not None and self.memory.get_task(task_id)["status"] != "running":
                    raise ValueError("Task stopped before dispatch.")
                # Read the current policy and risk here, after wait_for's scheduling boundary.
                category = category_for(tool)
                if self.policy.denied(category):
                    raise _PolicyDenied
                if not is_allowed(tool.risk, approved=approval is not None) or not self.policy.allowed(category, approved=approval is not None):
                    raise _PermissionRequired

            async def invoke():
                authorize()
                token = _approved_scope.set(approval.preview["execution_scope"] if approval else None)
                deadline_token = _approved_deadline.set(datetime.fromisoformat(approval.expires_at) if approval else None)
                revision_token = _approved_revision.set(approval.preview["policy_revision"] if approval else None)
                dispatch_token = execution_dispatch_authorized.set(authorize)
                try:
                    return await tool.handler(validated)
                finally:
                    _approved_scope.reset(token)
                    _approved_deadline.reset(deadline_token)
                    _approved_revision.reset(revision_token)
                    execution_dispatch_authorized.reset(dispatch_token)

            result = await asyncio.wait_for(invoke(), timeout=tool.timeout_seconds)
            return self._result(name, "success", "Tool completed.", result)
        except ToolExecutionOutcome as exc:
            status = exc.result.get("status")
            if status not in {"failed", "timed_out", "output_limit", "cancelled"}:
                status = "failed"
            return self._result(name, status, "Execution did not succeed; inspect its evidence.", exc.result)
        except _InvalidApproval as exc:
            return self._result(name, "invalid_approval", str(exc))
        except _PolicyDenied:
            return self._result(name, "denied", "The current action policy blocks this category.")
        except _PermissionRequired:
            if approval is not None:
                return self._result(name, "invalid_approval", "Current policy denies this grant; request fresh approval.")
            arguments = validated.model_dump()
            grant = self.memory.create_approval(
                name, arguments, str(tool.risk), self._preview(name, arguments),
                task_id=task_id, step_number=step_number,
            )
            return self._result(name, "approval_required", "Current policy requires fresh approval.", approval=grant)
        except asyncio.CancelledError:
            self._result(name, "cancelled", "Tool execution was cancelled.")
            raise
        except asyncio.TimeoutError:
            return self._result(name, "timed_out", "Tool timed out.")
        except (ValidationError, ValueError):
            return self._result(name, "invalid_arguments", "Tool arguments are invalid.")
        except Exception:
            return self._result(name, "failed", "Tool execution failed safely.")

    def _preview(self, name: str, arguments: dict[str, Any]) -> dict[str, object]:
        from services.agent_api.app.project_runtime import execution_project
        from services.agent_api.app.session_security import (
            POLICY_VERSION,
            execution_agent_job,
            execution_session,
        )

        preview: dict[str, object] = {
            "session": execution_session.get(),
            "actor": "local_user",
            "project_id": execution_project.get(),
            "policy_revision": self.policy.snapshot()["revision"],
            "action_category": str(category_for(self.registry.get(name))),
            "policy_version": POLICY_VERSION,
            "tool_name": name,
            "target": arguments.get("path") or arguments.get("target") or arguments.get("url") or arguments.get("session_id") or name,
            "risk": str(self.registry.get(name).risk),
            "effect": self.registry.get(name).description,
            "timeout_seconds": self.registry.get(name).timeout_seconds,
            "tool_contract_sha256": self._contract_hash(name),
            "arguments_sha256": self._arguments_hash(arguments),
            "execution_scope": self._execution_scope(name, arguments),
        }
        if name == "write_file":
            content = str(arguments["content"])
            preview.update(
                {
                    "bytes": len(content.encode()),
                    "overwrite": arguments["overwrite"],
                    "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                }
            )
        extra_preview = self.registry.get(name).preview_details
        if execution_agent_job.get() is not None:
            preview["agent_job_id"] = execution_agent_job.get()
        if extra_preview is not None:
            details = extra_preview(arguments)
            if set(details) & set(preview):
                raise ValueError("Tool preview cannot replace authorization fields.")
            preview.update(details)
        return preview

    @staticmethod
    def _arguments_hash(arguments: dict[str, Any]) -> str:
        return hashlib.sha256(canonical_arguments(arguments).encode()).hexdigest()

    def _execution_scope(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self.registry.get(name)
        root = self.registry.workspace_root
        destination = tool.resolve_destination(arguments) if tool.resolve_destination else None
        scope = {"workspace_root": str(root.resolve()) if root is not None else None,
                 "destination": str(destination.resolve()) if destination is not None else None}
        if tool.resolve_destinations is not None:
            paths = tool.resolve_destinations(arguments)
            if category_for(tool) != ActionCategory.READ or root is None or len(paths) > 14 or any(not path.resolve().is_relative_to(root.resolve()) for path in paths):
                raise ValueError("Only bounded exact read destinations within the workspace are supported.")
            scope["destinations"] = sorted({str(path.resolve()) for path in paths})
        return scope

    def _approval_matches(self, approval: Approval) -> bool:
        from services.agent_api.app.project_runtime import execution_project
        from services.agent_api.app.session_security import (
            POLICY_VERSION,
            execution_agent_job,
            execution_session,
        )

        # Legacy grants lack a verifiable scope and must be approved again.
        if not execution_authorized.get()():
            return False
        try:
            return (
                datetime.now(UTC) < datetime.fromisoformat(approval.expires_at)
                and approval.preview.get("session") == execution_session.get()
                and approval.preview.get("agent_job_id") == execution_agent_job.get()
                and approval.preview.get("project_id") == execution_project.get()
                and approval.preview.get("policy_revision") == self.policy.snapshot()["revision"]
                and approval.preview.get("action_category") == str(category_for(self.registry.get(approval.tool_name)))
                and approval.preview.get("policy_version") == POLICY_VERSION
                and approval.preview.get("tool_contract_sha256") == self._contract_hash(approval.tool_name)
                and approval.preview.get("arguments_sha256") == self._arguments_hash(approval.arguments)
                and approval.preview.get("execution_scope") == self._execution_scope(approval.tool_name, approval.arguments)
                and str(self.registry.get(approval.tool_name).risk) == approval.risk
            )
        except (KeyError, ValueError, OSError):
            return False

    def _contract_hash(self, name: str) -> str:
        tool = self.registry.get(name)
        contract = {"name": name, "description": tool.description, "risk": str(tool.risk),
                    "category": str(category_for(tool)),
                    "timeout": tool.timeout_seconds, "input": tool.input_model.model_json_schema()}
        return hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()

    def _result(
        self,
        name: str,
        status: str,
        detail: str,
        result: dict[str, Any] | None = None,
        approval: Approval | None = None,
    ) -> dict[str, Any]:
        event = "tool_execution_succeeded" if status == "success" else "tool_execution_failed"
        if status not in {"approval_required", "approved", "rejected", "expired", "consumed"}:
            self.memory.record_event(name, event, detail)
        return {
            "tool_name": name,
            "status": status,
            "detail": detail,
            "result": result,
            "approval": _approval_public(approval),
            "timestamp": datetime.now().isoformat(),
        }


def _approval_public(approval: Approval | None) -> dict[str, object] | None:
    if approval is None:
        return None
    return {
        "request_id": approval.request_id,
        "tool_name": approval.tool_name,
        "risk": approval.risk,
        "status": approval.status,
        "expires_at": approval.expires_at,
        "preview": approval.preview,
    }


def build_registry(workspace: Path) -> ToolRegistry:
    guard = WorkspaceGuard(workspace)
    registry = ToolRegistry(guard.root)

    async def system_time(_: BaseModel) -> dict[str, Any]:
        return {"local_time": datetime.now().astimezone().isoformat()}

    async def echo(value: BaseModel) -> dict[str, Any]:
        return {"message": EchoInput.model_validate(value).message}

    async def list_directory(value: BaseModel) -> dict[str, Any]:
        path = DirectoryInput.model_validate(value).path or "."
        return {"entries": sorted(item.name for item in guard.resolve(path).iterdir())}

    async def read_file(value: BaseModel) -> dict[str, Any]:
        data = ReadFileInput.model_validate(value)
        target = guard.resolve(data.path, True)
        if not target.is_file() or target.stat().st_size > data.max_bytes:
            raise ValueError("File is unavailable or exceeds the allowed size.")
        return {
            "path": data.path,
            "content": target.read_text(encoding="utf-8"),
            "bytes": target.stat().st_size,
        }

    async def write_file(value: BaseModel) -> dict[str, Any]:
        from services.agent_api.app.native_replace import ensure_directory, replace
        from services.agent_api.app.project_files import ProjectFiles

        data = WriteFileInput.model_validate(value)
        raw = data.content.encode("utf-8")
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("File content exceeds the byte limit.")
        target = guard.resolve(data.path)
        if target.exists() and not data.overwrite:
            raise ValueError("Target exists and overwrite is disabled.")
        existed = target.exists()
        ensure_directory(ProjectFiles(guard.root), target.parent, target)
        target = guard.resolve(data.path)
        files = ProjectFiles(guard.root)
        replace(files, target, raw, create_new=not existed)
        return {
            "path": data.path,
            "effect": "replaced" if existed else "created",
            "bytes": len(data.content.encode()),
        }

    async def create_directory(value: BaseModel) -> dict[str, Any]:
        from services.agent_api.app.native_replace import ensure_directory
        from services.agent_api.app.project_files import ProjectFiles

        data = CreateDirectoryInput.model_validate(value)
        target = guard.resolve(data.path)
        if not target.parent.is_dir():
            raise ValueError("Parent directory is unavailable.")
        ensure_directory(ProjectFiles(guard.root), target, target)
        target = guard.resolve(data.path, True)
        if not target.is_dir():
            raise RuntimeError("Directory verification failed.")
        return {"path": data.path, "effect": "directory_ready"}

    registry.register(
        Tool("system_time", "Returns local server time.", EmptyInput, RiskLevel.LOW, 2, system_time)
    )
    registry.register(Tool("echo", "Returns validated text.", EchoInput, RiskLevel.LOW, 2, echo))
    registry.register(
        Tool(
            "list_directory",
            "Lists an allowed workspace directory.",
            DirectoryInput,
            RiskLevel.LOW,
            2,
            list_directory,
            resolve_destination=lambda arguments: guard.resolve(arguments.get("path") or "."),
        )
    )
    registry.register(
        Tool("read_file", "Reads an allowed text file.", ReadFileInput, RiskLevel.LOW, 2, read_file,
             resolve_destination=lambda arguments: guard.resolve(arguments["path"], True))
    )
    registry.register(
        Tool(
            "write_file",
            "Writes an allowed text file.",
            WriteFileInput,
            RiskLevel.MEDIUM,
            2,
            write_file,
            resolve_destination=lambda arguments: guard.resolve(arguments["path"]),
        )
    )
    registry.register(
        Tool(
            "create_directory",
            "Creates an allowed directory.",
            CreateDirectoryInput,
            RiskLevel.MEDIUM,
            2,
            create_directory,
            resolve_destination=lambda arguments: guard.resolve(arguments["path"]),
        )
    )
    return registry
