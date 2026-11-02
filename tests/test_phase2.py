import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel

from services.agent_api.app.memory import MemoryRepository
from services.agent_api.app.permissions import RiskLevel, is_allowed
from services.agent_api.app.tools import Tool, ToolExecutor, ToolRegistry, build_registry


def test_permissions_are_default_deny_for_higher_risk() -> None:
    assert is_allowed(RiskLevel.LOW)
    assert not is_allowed(RiskLevel.MEDIUM)
    assert not is_allowed(RiskLevel.HIGH)


def test_memory_crud_persists(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "jarvis.db")
    repository.initialize()
    memory = repository.create("preference", "Use dark mode", 4, "user")
    assert repository.get(memory.id).content == "Use dark mode"
    assert repository.search("dark")[0].id == memory.id
    assert repository.update(memory.id, "Use compact dark mode").content == "Use compact dark mode"
    repository.delete(memory.id)
    assert repository.search() == []
    with pytest.raises(ValueError, match="Credential"):
        repository.create("note", "api_key=not-for-memory", None, None)


def test_registry_lifecycle_and_duplicate_rejection(tmp_path: Path) -> None:
    registry = build_registry(tmp_path / "workspace")
    assert {tool.name for tool in registry.list()} == {
        "echo",
        "list_directory",
        "system_time",
        "read_file",
        "write_file",
        "create_directory",
    }
    with pytest.raises(ValueError, match="already registered"):
        registry.register(registry.get("echo"))
    assert registry.validate_input("echo", {"message": "validated"}).message == "validated"
    with pytest.raises(Exception):
        registry.validate_input("echo", {"message": ""})
    registry.unregister("echo")
    with pytest.raises(KeyError):
        registry.get("echo")


def test_safe_tools_and_workspace_boundary(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("safe")
    memory = MemoryRepository(tmp_path / "jarvis.db")
    memory.initialize()
    executor = ToolExecutor(build_registry(workspace), memory)
    assert asyncio.run(executor.execute("echo", {"message": "hello"}))["result"] == {
        "message": "hello"
    }
    assert asyncio.run(executor.execute("list_directory", {}))["result"] == {
        "entries": ["note.txt"]
    }
    assert (
        asyncio.run(executor.execute("list_directory", {"path": ".."}))["status"]
        == "invalid_arguments"
    )
    executor.registry.set_enabled("echo", False)
    assert asyncio.run(executor.execute("echo", {"message": "hello"}))["status"] == "disabled"
    assert asyncio.run(executor.execute("missing", {}))["status"] == "not_found"


def test_execution_failure_timeout_and_permission_are_structured(tmp_path: Path) -> None:
    class NoInput(BaseModel):
        pass

    async def fail(_: BaseModel) -> dict[str, str]:
        raise RuntimeError("internal failure")

    async def wait(_: BaseModel) -> dict[str, str]:
        await asyncio.sleep(0.02)
        return {"finished": "late"}

    async def medium(_: BaseModel) -> dict[str, str]:
        return {"should": "not run"}

    memory = MemoryRepository(tmp_path / "jarvis.db")
    memory.initialize()
    registry = ToolRegistry()
    registry.register(Tool("fail", "fails", NoInput, RiskLevel.LOW, 1, fail))
    registry.register(Tool("wait", "waits", NoInput, RiskLevel.LOW, 0.001, wait))
    registry.register(Tool("medium", "approval gate", NoInput, RiskLevel.MEDIUM, 1, medium))
    executor = ToolExecutor(registry, memory)
    assert asyncio.run(executor.execute("fail", {}))["status"] == "failed"
    assert asyncio.run(executor.execute("wait", {}))["status"] == "timed_out"
    assert asyncio.run(executor.execute("medium", {}))["status"] == "approval_required"
    assert (
        asyncio.run(executor.execute("medium", {"extra": "ignored"}))["status"]
        == "approval_required"
    )
