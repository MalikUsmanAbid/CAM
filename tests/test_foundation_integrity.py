"""Regression evidence for approval ownership, races and task dispatch."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from services.agent_api.app.capabilities import (
    CapabilityRegistry,
    CapabilityResolver,
    ResolutionStatus,
)
from services.agent_api.app.main import app
from services.agent_api.app.mcp import _input_model_from_schema
from services.agent_api.app.memory import MemoryRepository
from services.agent_api.app.orchestrator import AgentOrchestrator
from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.planner import DeterministicPlanner, PlannedStep
from services.agent_api.app.tasks import TaskLimits
from services.agent_api.app.tools import EmptyInput, Tool, ToolExecutor, build_registry


def setup(tmp_path):
    memory = MemoryRepository(tmp_path / "test.sqlite")
    memory.initialize()
    registry = build_registry(tmp_path / "workspace")
    return memory, registry, ToolExecutor(registry, memory)


def test_capability_fallback_requires_enabled_existing_echo(tmp_path):
    _, registry, _ = setup(tmp_path)
    capabilities = CapabilityRegistry.from_tools(registry)
    resolver = CapabilityResolver(capabilities)
    assert resolver.resolve("hello Jarvis").status == ResolutionStatus.FOUND
    capabilities.set_enabled("tool.echo", False)
    assert resolver.resolve("hello Jarvis").status == ResolutionStatus.WAITING_FOR_CAPABILITY
    assert resolver.resolve("echo hello").status == ResolutionStatus.WAITING_FOR_CAPABILITY
    assert (
        CapabilityResolver(CapabilityRegistry()).resolve("hello").status
        == ResolutionStatus.WAITING_FOR_CAPABILITY
    )
    assert resolver.resolve(" ").status == ResolutionStatus.WAITING_FOR_CAPABILITY


def test_ordinary_task_uses_production_resolver():
    with TestClient(app) as client:
        response = client.post("/api/v1/tasks", json={"request": "hello Jarvis"})
        assert response.status_code == 201
        assert response.json()["status"] == "completed"


@pytest.mark.parametrize("approved", [False, True])
def test_expired_approval_never_executes(tmp_path, approved):
    memory, _, executor = setup(tmp_path)
    approval = memory.create_approval("create_directory", {"path": "child"}, "medium", {})
    if approved:
        memory.decide_approval(approval.request_id, True)
    with memory._connect() as connection:
        connection.execute(
            "UPDATE approvals SET expires_at=? WHERE request_id=?",
            ("2000-01-01T00:00:00+00:00", approval.request_id),
        )
    assert memory.get_approval(approval.request_id).status == "expired"
    assert memory.pending_approvals() == []
    assert asyncio.run(executor.continue_approved(approval.request_id))["status"] == "expired"
    assert not (tmp_path / "workspace" / "child").exists()


def test_concurrent_continuations_execute_once(tmp_path, monkeypatch):
    memory, registry, executor = setup(tmp_path)
    calls = []

    async def effect(_):
        calls.append("executed")
        return {"ok": True}

    registry.register(Tool("effect", "test effect", EmptyInput, RiskLevel.HIGH, 2, effect))
    pending = asyncio.run(executor.execute("effect", {}))
    request_id = pending["approval"]["request_id"]
    memory.decide_approval(request_id, True)
    original = memory.get_approval
    barrier = threading.Barrier(2)
    thread_state = threading.local()

    def synchronized_read(identifier):
        result = original(identifier)
        thread_state.reads = getattr(thread_state, "reads", 0) + 1
        # Both preflight and consumption reads see approved before either claims it.
        if thread_state.reads <= 2:
            barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(memory, "get_approval", synchronized_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: asyncio.run(executor.continue_approved(request_id)), range(2))
        )
    assert calls == ["executed"]
    assert sorted(item["status"] for item in results) == ["consumed", "success"]


@pytest.mark.anyio
async def test_disabled_or_changed_risk_cannot_use_old_grant(tmp_path):
    memory, registry, executor = setup(tmp_path)
    for change, expected in [
        ("disabled", "disabled"),
        ("risk", "invalid_approval"),
        ("contract", "invalid_approval"),
    ]:
        registry.get("create_directory").enabled = True
        registry.get("create_directory").risk = RiskLevel.MEDIUM
        pending = await executor.execute("create_directory", {"path": change})
        request_id = pending["approval"]["request_id"]
        memory.decide_approval(request_id, True)
        if change == "disabled":
            registry.set_enabled("create_directory", False)
        elif change == "risk":
            registry.get("create_directory").risk = RiskLevel.HIGH
        else:
            registry.get("create_directory").description = "Changed operation"
        assert (await executor.continue_approved(request_id))["status"] == expected
        assert not (tmp_path / "workspace" / change).exists()


@pytest.mark.anyio
@pytest.mark.parametrize("approved", [False, True])
async def test_cancel_revokes_task_grant_and_direct_route_is_blocked(tmp_path, approved):
    memory, _, executor = setup(tmp_path)
    planner = DeterministicPlanner(
        [PlannedStep("write", "write_file", {"path": "a.txt", "content": "a"})]
    )
    orchestrator = AgentOrchestrator(executor, memory, planner)
    task = await orchestrator.create("write")
    approval = memory.pending_approvals()[0]
    if approved:
        memory.decide_approval(approval.request_id, True)
    assert (await executor.continue_approved(approval.request_id))["status"] == "task_owned"
    assert orchestrator.cancel(task["task_id"])["status"] == "cancelled"
    assert memory.get_approval(approval.request_id).status == "cancelled"
    assert (await orchestrator.resume(task["task_id"]))["status"] == "cancelled"
    assert not (tmp_path / "workspace" / "a.txt").exists()


def test_direct_api_cannot_continue_task_approval():
    with TestClient(app) as client:
        task = client.post(
            "/api/v1/tasks", json={"request": 'tool:create_directory {"path":"child"}'}
        ).json()
        approval = client.get("/api/v1/approvals").json()[0]
        identifier = approval["request_id"]
        client.post(f"/api/v1/approvals/{identifier}/approve")
        assert (
            client.post(f"/api/v1/tools/execute/{identifier}/continue").json()["status"]
            == "task_owned"
        )
        client.post(f"/api/v1/tasks/{task['task_id']}/cancel")
        assert (
            client.post(f"/api/v1/tasks/{task['task_id']}/resume").json()["status"] == "cancelled"
        )


@pytest.mark.anyio
async def test_concurrent_resume_across_orchestrators_claims_once(tmp_path):
    memory, registry, executor = setup(tmp_path)
    calls = []
    started, release = asyncio.Event(), asyncio.Event()

    async def effect(_):
        calls.append("executed")
        started.set()
        await release.wait()
        return {"ok": True}

    registry.register(Tool("effect", "effect", EmptyInput, RiskLevel.HIGH, 5, effect))
    planner = DeterministicPlanner(
        [PlannedStep("effect", "effect", {}), PlannedStep("echo", "echo", {"message": "done"})]
    )
    first = AgentOrchestrator(executor, memory, planner)
    second = AgentOrchestrator(executor, memory, planner)
    task = await first.create("effect")
    approval = memory.pending_approvals()[0]
    memory.decide_approval(approval.request_id, True)
    running = asyncio.create_task(first.resume(task["task_id"]))
    await asyncio.wait_for(started.wait(), 2)
    assert (await second.resume(task["task_id"]))["status"] == "running"
    assert (await second.run(task["task_id"]))["status"] == "running"
    release.set()
    assert (await running)["status"] == "completed"
    assert calls == ["executed"]
    assert [step["status"] for step in memory.get_task_steps(task["task_id"])] == [
        "success",
        "success",
    ]


def test_terminal_task_cannot_be_resurrected_and_claims_recover(tmp_path):
    memory, _, _ = setup(tmp_path)
    task_id = memory.create_task("hello", TaskLimits())
    memory.claim_task(task_id, "dead-worker")
    memory.update_task(task_id, "running")
    assert memory.recover_interrupted_tasks() == [task_id]
    assert memory.claim_task(task_id, "new-worker")
    memory.update_task(task_id, "completed")
    assert memory.get_task(task_id)["status"] == "failed"


@pytest.mark.anyio
async def test_interrupted_request_cleans_up_child_and_releases_claim(tmp_path):
    memory, registry, executor = setup(tmp_path)
    started, stopped = asyncio.Event(), asyncio.Event()

    async def wait(_):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    registry.register(Tool("wait", "wait", EmptyInput, RiskLevel.LOW, 5, wait))
    orchestrator = AgentOrchestrator(
        executor, memory, DeterministicPlanner([PlannedStep("wait", "wait", {})])
    )
    task_id = memory.create_task("wait", TaskLimits())
    running = asyncio.create_task(orchestrator.run(task_id))
    await asyncio.wait_for(started.wait(), 2)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert stopped.is_set()
    assert memory.get_task(task_id)["status"] == "failed"
    assert memory.claim_task(task_id, "next-worker")


@pytest.mark.anyio
async def test_cancel_while_resuming_never_runs_later_steps(tmp_path):
    memory, registry, executor = setup(tmp_path)
    started = asyncio.Event()
    calls = []

    async def wait(_):
        started.set()
        await asyncio.Event().wait()

    async def later(_):
        calls.append("late-effect")
        return {"ok": True}

    registry.register(Tool("wait", "wait", EmptyInput, RiskLevel.HIGH, 5, wait))
    registry.register(Tool("later", "later", EmptyInput, RiskLevel.LOW, 5, later))
    planner = DeterministicPlanner(
        [PlannedStep("wait", "wait", {}), PlannedStep("later", "later", {})]
    )
    orchestrator = AgentOrchestrator(executor, memory, planner)
    task = await orchestrator.create("wait")
    approval = memory.pending_approvals()[0]
    memory.decide_approval(approval.request_id, True)
    running = asyncio.create_task(orchestrator.resume(task["task_id"]))
    await asyncio.wait_for(started.wait(), 2)
    orchestrator.cancel(task["task_id"])
    assert (await running)["status"] == "cancelled"
    assert calls == []


def test_high_risk_preview_describes_effect(tmp_path):
    _, registry, executor = setup(tmp_path)
    registry.get("create_directory").risk = RiskLevel.HIGH
    pending = asyncio.run(executor.execute("create_directory", {"path": "child"}))
    preview = pending["approval"]["preview"]
    assert preview["risk"] == "high"
    assert preview["target"] == "child"
    assert preview["effect"] and preview["timeout_seconds"] == 2


def schema(field):
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"value": field},
        "required": ["value"],
    }


@pytest.mark.parametrize("kind", ["integer", "number"])
def test_mcp_numeric_bounds_are_enforced(kind):
    model = _input_model_from_schema("bounded", schema({"type": kind, "minimum": 1, "maximum": 3}))
    assert model.model_validate({"value": 2}).value == 2
    for value in [0, 100, True, "2", float("inf")]:
        with pytest.raises(ValidationError):
            model.model_validate({"value": value})


@pytest.mark.parametrize(
    "field",
    [
        {"type": "integer", "minimum": "1"},
        {"type": "number", "multipleOf": 0},
        {"type": "number", "maximum": float("inf")},
        {"type": "integer", "minimum": 4, "maximum": 2},
        {"type": "number", "exclusiveMinimum": 2, "maximum": 2},
        {"type": "string", "minLength": -1},
        {"type": "string", "minLength": 5, "maxLength": 2},
        {"type": "integer", "enum": [1, 2]},
        {"type": "string", "pattern": "^safe$"},
        {"type": "integer", "minimum": 1, "default": 0},
    ],
)
def test_mcp_unsupported_or_invalid_constraints_fail_closed(field):
    with pytest.raises(ValueError):
        _input_model_from_schema("invalid", schema(field))


def test_mcp_exclusive_and_multiple_bounds():
    model = _input_model_from_schema(
        "bounded",
        schema({"type": "integer", "exclusiveMinimum": 0, "exclusiveMaximum": 6, "multipleOf": 2}),
    )
    assert model.model_validate({"value": 4}).value == 4
    for value in [0, 3, 6]:
        with pytest.raises(ValidationError):
            model.model_validate({"value": value})


@pytest.mark.anyio
async def test_additive_initialization_preserves_and_protects_legacy_approval(tmp_path):
    memory, _, executor = setup(tmp_path)
    task_id = memory.create_task("legacy directory", TaskLimits())
    memory.create_task_step(task_id, 1, "directory", "create_directory", {"path": "legacy"})
    approval = memory.create_approval("create_directory", {"path": "legacy"}, "medium", {})
    memory.update_task_step(
        task_id, 1, "approval_required", {"approval": {"request_id": approval.request_id}}
    )
    memory.update_task(task_id, "waiting_for_approval", 1)
    memory.create("note", "preserve existing note", 3, "test")
    with memory._connect() as connection:
        connection.execute("DROP TABLE approval_tasks")
        connection.execute("DROP TABLE task_claims")
        # This fixture represents an unversioned legacy database, rather than
        # a damaged database falsely claiming the current schema version.
        connection.execute("DROP TABLE schema_versions")
        connection.execute("DROP TABLE migration_checksums")
    memory.initialize()
    assert memory.search()[0].content == "preserve existing note"
    assert memory.approval_owner(approval.request_id) == task_id
    memory.decide_approval(approval.request_id, True)
    assert (await executor.continue_approved(approval.request_id))["status"] == "task_owned"
    memory.update_task(task_id, "cancelled", 1)
    assert memory.get_approval(approval.request_id).status == "cancelled"
    assert not (tmp_path / "workspace" / "legacy").exists()
