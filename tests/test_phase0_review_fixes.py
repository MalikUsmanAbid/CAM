"""Regressions for the six Phase 0 review areas; persistence is always temporary."""

import asyncio
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import pytest
from pydantic import ValidationError

from services.agent_api.app.backend_boundary import ProcessCancellation, backend_database_lock
from services.agent_api.app.capabilities import (
    CapabilityRegistry,
    CapabilityResolver,
    ResolutionStatus,
)
from services.agent_api.app.mcp import MCPManager, MCPToolDefinition, _input_model_from_schema
from services.agent_api.app.memory import MemoryRepository
from services.agent_api.app.orchestrator import AgentOrchestrator
from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.planner import DeterministicPlanner, PlannedStep
from services.agent_api.app.tasks import TaskLimits
from services.agent_api.app.tools import EmptyInput, Tool, ToolExecutor, build_registry


def setup(tmp_path):
    memory = MemoryRepository(tmp_path / "review.sqlite")
    memory.initialize()
    registry = build_registry(tmp_path / "workspace-a")
    return memory, registry, ToolExecutor(registry, memory)


@pytest.mark.anyio
async def test_workspace_change_requires_fresh_task_approval(tmp_path):
    memory, _, executor = setup(tmp_path)
    first = AgentOrchestrator(executor, memory, DeterministicPlanner([
        PlannedStep("write", "write_file", {"path": "approved.txt", "content": "approved"})
    ]))
    task = await first.create("write")
    grant = memory.pending_approvals()[0]
    memory.decide_approval(grant.request_id, True)
    second = AgentOrchestrator(ToolExecutor(build_registry(tmp_path / "workspace-b"), memory), memory)
    result = await second.resume(task["task_id"])
    assert result["status"] == "failed"
    assert memory.get_task_steps(task["task_id"])[0]["result"]["status"] == "invalid_approval"
    assert not (tmp_path / "workspace-a" / "approved.txt").exists()
    assert not (tmp_path / "workspace-b" / "approved.txt").exists()


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["risk", "policy"])
async def test_current_permission_is_checked_at_dispatch(tmp_path, monkeypatch, change):
    memory, registry, executor = setup(tmp_path)
    effects = []

    async def effect(_):
        effects.append("executed")
        return {}

    registry.register(Tool("effect", "effect", EmptyInput, RiskLevel.LOW, 2, effect))
    original_wait = asyncio.wait_for

    async def dispatch_boundary(awaitable, timeout):
        if change == "risk":
            registry.get("effect").risk = RiskLevel.HIGH
        else:
            monkeypatch.setattr("services.agent_api.app.tools.is_allowed", lambda risk, approved=False: approved)
        return await original_wait(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", dispatch_boundary)
    result = await executor.execute("effect", {})
    assert result["status"] == "approval_required"
    assert effects == []
    assert len(memory.pending_approvals()) == 1


@pytest.mark.anyio
async def test_current_policy_can_deny_previously_approved_dispatch(tmp_path, monkeypatch):
    memory, _, executor = setup(tmp_path)
    pending = await executor.execute("create_directory", {"path": "denied"})
    identifier = pending["approval"]["request_id"]
    memory.decide_approval(identifier, True)
    original_wait = asyncio.wait_for

    async def dispatch_boundary(awaitable, timeout):
        monkeypatch.setattr("services.agent_api.app.tools.is_allowed", lambda risk, approved=False: False)
        return await original_wait(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", dispatch_boundary)
    assert (await executor.continue_approved(identifier))["status"] == "invalid_approval"
    assert not (tmp_path / "workspace-a" / "denied").exists()


@pytest.mark.parametrize("task_request", ['tool:read_file {"path":"x"}', "tool.read_file", "use tool.read_file", "tool:missing {}"])
def test_explicit_missing_tool_never_resolves_to_echo(tmp_path, task_request):
    _, registry, _ = setup(tmp_path)
    capabilities = CapabilityRegistry.from_tools(registry)
    capabilities.unregister("tool.read_file")
    resolution = CapabilityResolver(capabilities).resolve(task_request)
    assert resolution.status == ResolutionStatus.WAITING_FOR_CAPABILITY
    assert resolution.capability_id != "tool.echo"


class RecordingTransport:
    def __init__(self, model):
        self.model, self.arguments = model, []

    async def discover(self):
        return [MCPToolDefinition("bounded", "bounded input", RiskLevel.HIGH, self.model)]

    async def invoke(self, tool_id, arguments):
        self.arguments.append(arguments)
        return {"ok": True}

    async def close(self):
        pass


def optional_model():
    return _input_model_from_schema("optional", {
        "type": "object", "additionalProperties": False,
        "properties": {
            "count": {"type": "integer", "minimum": 1, "maximum": 3},
            "size": {"type": "integer", "minimum": 1, "maximum": 3, "default": 2},
        },
    })


@pytest.mark.anyio
@pytest.mark.parametrize("arguments, expected", [({}, {"size": 2}), ({"count": 3}, {"count": 3, "size": 2})])
async def test_mcp_transport_receives_omissions_and_valid_defaults(tmp_path, arguments, expected):
    memory, registry, executor = setup(tmp_path)
    transport = RecordingTransport(optional_model())
    manager = MCPManager(registry, CapabilityRegistry.from_tools(registry))
    name = (await manager.connect("test", transport))[0]
    pending = await executor.execute(name, arguments)
    grant_id = pending["approval"]["request_id"]
    memory.decide_approval(grant_id, True)
    assert (await executor.continue_approved(grant_id))["status"] == "success"
    assert transport.arguments == [expected]


@pytest.mark.anyio
async def test_explicit_registered_mcp_tool_resolves_and_executes(tmp_path):
    memory, registry, executor = setup(tmp_path)
    capabilities = CapabilityRegistry.from_tools(registry)
    transport = RecordingTransport(optional_model())
    manager = MCPManager(registry, capabilities)
    name = (await manager.connect("test", transport))[0]
    resolver = CapabilityResolver(capabilities)
    task_request = f'tool:{name} {{"count":2}}'
    assert resolver.resolve(task_request).capability_id == "mcp.test.bounded"
    capabilities.set_enabled("mcp.test.bounded", False)
    assert resolver.resolve(task_request).status == ResolutionStatus.WAITING_FOR_CAPABILITY
    capabilities.set_enabled("mcp.test.bounded", True)
    orchestrator = AgentOrchestrator(executor, memory, resolver=resolver)
    task = await orchestrator.create(task_request)
    assert task["status"] == "waiting_for_approval"
    memory.decide_approval(memory.pending_approvals()[0].request_id, True)
    assert (await orchestrator.resume(task["task_id"]))["status"] == "completed"
    assert transport.arguments == [{"count": 2, "size": 2}]


@pytest.mark.anyio
@pytest.mark.parametrize("value", [None, 0, 4, True, "2"])
async def test_mcp_invalid_optional_value_never_reaches_transport(tmp_path, value):
    _, registry, executor = setup(tmp_path)
    transport = RecordingTransport(optional_model())
    manager = MCPManager(registry, CapabilityRegistry.from_tools(registry))
    name = (await manager.connect("test", transport))[0]
    assert (await executor.execute(name, {"count": value}))["status"] == "invalid_arguments"
    assert transport.arguments == []


@pytest.mark.anyio
async def test_mcp_handler_revalidates_constructed_arguments(tmp_path):
    _, registry, _ = setup(tmp_path)
    model = optional_model()
    transport = RecordingTransport(model)
    manager = MCPManager(registry, CapabilityRegistry.from_tools(registry))
    name = (await manager.connect("test", transport))[0]
    # Simulate accidental model mutation after validation; transport must still reject it.
    value = model.model_validate({"count": 2})
    value.count = 99
    with pytest.raises(ValidationError):
        await registry.get(name).handler(value)
    assert transport.arguments == []


@pytest.mark.anyio
@pytest.mark.parametrize("resume", [False, True])
async def test_other_orchestrator_cancels_running_cooperative_handler(tmp_path, resume):
    memory, registry, executor = setup(tmp_path)
    started, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    effects = []

    async def wait(_):
        started.set()
        try:
            await release.wait()
            effects.append("late effect")
            return {}
        finally:
            stopped.set()

    registry.register(Tool("wait", "wait", EmptyInput, RiskLevel.HIGH if resume else RiskLevel.LOW, 5, wait))
    first = AgentOrchestrator(executor, memory, DeterministicPlanner([PlannedStep("wait", "wait", {})]))
    other_memory = MemoryRepository(memory.database_path)
    second = AgentOrchestrator(ToolExecutor(registry, other_memory), other_memory)
    if resume:
        task_id = (await first.create("wait"))["task_id"]
        memory.decide_approval(memory.pending_approvals()[0].request_id, True)
        running = asyncio.create_task(first.resume(task_id))
    else:
        task_id = memory.create_task("wait", TaskLimits())
        running = asyncio.create_task(first.run(task_id))
    await asyncio.wait_for(started.wait(), 2)
    second.cancel(task_id)
    release.set()  # Cancellation must win even when work is immediately made runnable.
    assert (await running)["status"] == "cancelled"
    assert stopped.is_set()
    assert effects == []
    assert memory.claim_task(task_id, "verify-release")
    memory.release_task(task_id, "verify-release")
    assert ProcessCancellation.key(memory.database_path, task_id) not in ProcessCancellation._actions


@pytest.mark.anyio
async def test_expiry_between_preflight_and_consumption_never_executes(tmp_path, monkeypatch):
    memory, _, executor = setup(tmp_path)
    pending = await executor.execute("create_directory", {"path": "expired"})
    identifier = pending["approval"]["request_id"]
    memory.decide_approval(identifier, True)
    original = memory.get_approval
    reads = 0

    def expire_after_approved_read(request_id):
        nonlocal reads
        grant = original(request_id)
        reads += 1
        if reads == 2:  # consume_approval has read approved; its transaction has not started.
            with memory._connect() as connection:
                connection.execute("UPDATE approvals SET expires_at=? WHERE request_id=?", ("2000-01-01T00:00:00+00:00", identifier))
        return grant

    monkeypatch.setattr(memory, "get_approval", expire_after_approved_read)
    assert (await executor.continue_approved(identifier))["status"] == "expired"
    assert not (tmp_path / "workspace-a" / "expired").exists()


def test_simultaneous_resume_attempts_claim_once(tmp_path, monkeypatch):
    memory, registry, executor = setup(tmp_path)
    effects = []
    entered, release = threading.Event(), threading.Event()

    async def effect(_):
        effects.append("executed")
        entered.set()
        await asyncio.to_thread(release.wait, 5)
        return {}

    registry.register(Tool("effect", "effect", EmptyInput, RiskLevel.HIGH, 10, effect))
    planner = DeterministicPlanner([PlannedStep("effect", "effect", {})])
    first, second = AgentOrchestrator(executor, memory, planner), AgentOrchestrator(executor, memory, planner)
    task = asyncio.run(first.create("effect"))
    memory.decide_approval(memory.pending_approvals()[0].request_id, True)
    barrier = threading.Barrier(2)
    original_claim = memory.claim_task
    claims = []

    def synchronized_claim(task_id, owner):
        barrier.wait(timeout=5)
        won = original_claim(task_id, owner)
        claims.append(won)
        return won

    monkeypatch.setattr(memory, "claim_task", synchronized_claim)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(asyncio.run, runner.resume(task["task_id"])) for runner in (first, second)]
        try:
            assert entered.wait(5)
            # The loser must return while the winner still owns the task.
            done, _ = wait(futures, timeout=5, return_when=FIRST_COMPLETED)
            assert len(done) == 1
        finally:
            release.set()
        results = [future.result(timeout=5) for future in futures]
    assert sorted(claims) == [False, True]
    assert effects == ["executed"]
    assert any(result["status"] == "completed" for result in results)
    assert memory.get_task(task["task_id"])["status"] == "completed"
    with memory._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM task_claims").fetchone()[0] == 0


@pytest.mark.anyio
async def test_tampered_approval_arguments_are_rejected(tmp_path):
    memory, _, executor = setup(tmp_path)
    pending = await executor.execute("write_file", {"path": "approved.txt", "content": "approved"})
    identifier = pending["approval"]["request_id"]
    memory.decide_approval(identifier, True)
    with memory._connect() as connection:
        connection.execute("UPDATE approvals SET arguments_json=? WHERE request_id=?", (json.dumps({"path": "other.txt", "content": "changed", "overwrite": False}), identifier))
    assert (await executor.continue_approved(identifier))["status"] == "invalid_approval"
    assert not (tmp_path / "workspace-a" / "other.txt").exists()


@pytest.mark.anyio
@pytest.mark.parametrize("boundary", ["preflight", "dispatch", "handler"])
async def test_resolved_destination_change_requires_fresh_approval(tmp_path, monkeypatch, boundary):
    memory, registry, executor = setup(tmp_path)
    root = tmp_path / "workspace-a"
    first, second, link = root / "first", root / "second", root / "link"
    first.mkdir()
    second.mkdir()

    def point_to(target):
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        else:
            link.symlink_to(target, target_is_directory=True)

    def redirect():
        if os.name == "nt":
            link.rmdir()  # Remove only the temporary junction, never its target.
        else:
            link.unlink()
        point_to(second)

    point_to(first)
    try:
        pending = await executor.execute("write_file", {"path": "link/approved.txt", "content": "approved"})
        identifier = pending["approval"]["request_id"]
        memory.decide_approval(identifier, True)
        if boundary == "dispatch":
            original_wait = asyncio.wait_for

            async def dispatch_boundary(awaitable, timeout):
                redirect()
                return await original_wait(awaitable, timeout)

            monkeypatch.setattr(asyncio, "wait_for", dispatch_boundary)
        elif boundary == "handler":
            original_handler = registry.get("write_file").handler

            async def redirect_at_handler(value):
                redirect()
                return await original_handler(value)

            registry.get("write_file").handler = redirect_at_handler
        else:
            redirect()
        assert (await executor.continue_approved(identifier))["status"] == "invalid_approval"
        assert not (first / "approved.txt").exists()
        assert not (second / "approved.txt").exists()
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


@pytest.mark.anyio
async def test_legacy_unbound_grant_requires_fresh_approval(tmp_path):
    memory, _, executor = setup(tmp_path)
    grant = memory.create_approval("create_directory", {"path": "legacy"}, "medium", {})
    memory.decide_approval(grant.request_id, True)
    assert (await executor.continue_approved(grant.request_id))["status"] == "invalid_approval"
    assert not (tmp_path / "workspace-a" / "legacy").exists()


def test_backend_lock_rejects_second_process_and_releases(tmp_path):
    database = tmp_path / "locked.sqlite"
    script = """import sys
from pathlib import Path
from services.agent_api.app.backend_boundary import backend_database_lock
try:
    with backend_database_lock(Path(sys.argv[1])):
        print('acquired')
except RuntimeError:
    print('blocked')
"""
    with backend_database_lock(database):
        with pytest.raises(RuntimeError, match="one backend process"):
            with backend_database_lock(database):
                pytest.fail("Second startup acquired the database")
        result = subprocess.run([sys.executable, "-B", "-c", script, str(database)], capture_output=True, text=True, timeout=10, check=True)
        assert result.stdout.strip() == "blocked"
    result = subprocess.run([sys.executable, "-B", "-c", script, str(database)], capture_output=True, text=True, timeout=10, check=True)
    assert result.stdout.strip() == "acquired"


def test_backend_lock_prevents_startup_recovery_of_active_task(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from services.agent_api.app.main import app

    database = tmp_path / "application.sqlite"
    monkeypatch.setenv("DATABASE_PATH", str(database))
    memory = MemoryRepository(database)
    memory.initialize()
    task_id = memory.create_task("active", TaskLimits())
    memory.update_task(task_id, "running")
    with backend_database_lock(database):
        with pytest.raises(RuntimeError, match="one backend process"):
            with TestClient(app):
                pytest.fail("Second startup ran")
        assert memory.get_task(task_id)["status"] == "running"


@pytest.mark.anyio
async def test_cancellation_is_scoped_to_database_and_cleans_up(tmp_path):
    memory, registry, executor = setup(tmp_path)
    started, release = asyncio.Event(), asyncio.Event()
    effects = []

    async def effect(_):
        started.set()
        await release.wait()
        effects.append("executed")
        return {}

    registry.register(Tool("effect", "effect", EmptyInput, RiskLevel.LOW, 5, effect))
    first = AgentOrchestrator(executor, memory, DeterministicPlanner([PlannedStep("effect", "effect", {})]))
    task_id = memory.create_task("effect", TaskLimits())
    other = MemoryRepository(tmp_path / "other.sqlite")
    other.initialize()
    other_task_id = other.create_task("other", TaskLimits())
    with other._connect() as connection:
        connection.execute("UPDATE tasks SET task_id=? WHERE task_id=?", (task_id, other_task_id))
    running = asyncio.create_task(first.run(task_id))
    await asyncio.wait_for(started.wait(), 2)
    AgentOrchestrator(ToolExecutor(registry, other), other).cancel(task_id)
    release.set()
    assert (await running)["status"] == "completed"
    assert effects == ["executed"]
    assert ProcessCancellation.key(memory.database_path, task_id) not in ProcessCancellation._actions
