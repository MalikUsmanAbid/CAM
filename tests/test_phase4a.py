from pathlib import Path

import pytest

from services.agent_api.app.memory import MemoryRepository
from services.agent_api.app.orchestrator import AgentOrchestrator
from services.agent_api.app.planner import DeterministicPlanner, PlannedStep
from services.agent_api.app.tasks import TaskLimits, TaskStatus
from services.agent_api.app.tools import ToolExecutor, build_registry


@pytest.mark.anyio
async def test_deterministic_task_completes_and_persists(tmp_path: Path) -> None:
    memory = MemoryRepository(tmp_path / "jarvis.db")
    memory.initialize()
    orchestrator = AgentOrchestrator(
        ToolExecutor(build_registry(tmp_path / "workspace"), memory), memory
    )
    task = await orchestrator.create("hello")
    assert task["status"] == TaskStatus.COMPLETED
    assert len(memory.get_task_steps(task["task_id"])) == 1


@pytest.mark.anyio
async def test_medium_task_waits_then_resumes_once(tmp_path: Path) -> None:
    memory = MemoryRepository(tmp_path / "jarvis.db")
    memory.initialize()
    planner = DeterministicPlanner(
        [PlannedStep("write", "write_file", {"path": "a.txt", "content": "a"})]
    )
    orchestrator = AgentOrchestrator(
        ToolExecutor(build_registry(tmp_path / "workspace"), memory), memory, planner
    )
    task = await orchestrator.create("write")
    assert task["status"] == TaskStatus.WAITING_FOR_APPROVAL
    approval = memory.pending_approvals()[0]
    memory.decide_approval(approval.request_id, True)
    completed = await orchestrator.resume(task["task_id"])
    assert completed["status"] == TaskStatus.COMPLETED
    replay = await orchestrator.resume(task["task_id"])
    assert replay["status"] == TaskStatus.COMPLETED


@pytest.mark.anyio
async def test_approval_resume_after_restart_continues_remaining_plan(tmp_path: Path) -> None:
    memory = MemoryRepository(tmp_path / "jarvis.db")
    memory.initialize()
    planner = DeterministicPlanner(
        [
            PlannedStep("write", "write_file", {"path": "a.txt", "content": "a"}),
            PlannedStep("echo", "echo", {"message": "done"}),
        ]
    )
    registry = build_registry(tmp_path / "workspace")
    first = AgentOrchestrator(ToolExecutor(registry, memory), memory, planner)
    paused = await first.create("write and report")
    assert paused["status"] == TaskStatus.WAITING_FOR_APPROVAL
    approval = memory.pending_approvals()[0]
    memory.decide_approval(approval.request_id, True)

    restarted = AgentOrchestrator(ToolExecutor(registry, memory), memory, planner)
    completed = await restarted.resume(paused["task_id"])
    assert completed["status"] == TaskStatus.COMPLETED
    assert [step["status"] for step in memory.get_task_steps(paused["task_id"])] == [
        "success",
        "success",
    ]


@pytest.mark.anyio
async def test_interrupted_running_task_is_not_replayed_after_restart(tmp_path: Path) -> None:
    from services.agent_api.app.permissions import RiskLevel
    from services.agent_api.app.tools import EmptyInput, Tool, ToolRegistry

    memory = MemoryRepository(tmp_path / "jarvis.db")
    memory.initialize()
    effects: list[str] = []

    async def completed_effect(_value):
        effects.append("completed")
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(Tool("effect", "effect", EmptyInput, RiskLevel.LOW, 10, completed_effect))
    executor = ToolExecutor(registry, memory)
    task_id = memory.create_task("effect", TaskLimits())
    memory.create_task_step(task_id, 1, "effect", "effect", {})
    assert (await executor.execute("effect", {}))["status"] == "success"
    assert effects == ["completed"]
    memory.update_task_step(task_id, 1, "success", {"status": "success"})
    memory.update_task(task_id, TaskStatus.RUNNING, 1)

    assert memory.recover_interrupted_tasks() == [task_id]
    restarted = AgentOrchestrator(executor, memory)
    recovered = await restarted.resume(task_id)
    assert recovered["status"] == TaskStatus.FAILED
    assert await restarted.run(task_id) == recovered
    assert effects == ["completed"]


@pytest.mark.anyio
async def test_orchestrator_cancellation_stops_running_tool(tmp_path: Path) -> None:
    import asyncio

    from services.agent_api.app.permissions import RiskLevel
    from services.agent_api.app.tools import EmptyInput, Tool

    memory = MemoryRepository(tmp_path / "jarvis.db")
    memory.initialize()
    registry = build_registry(tmp_path / "workspace")
    started = asyncio.Event()

    async def wait_for_cancel(_value):
        started.set()
        await asyncio.Event().wait()
        return {"ok": True}

    registry.register(Tool("wait", "wait", EmptyInput, RiskLevel.LOW, 10, wait_for_cancel))
    planner = DeterministicPlanner([PlannedStep("wait", "wait", {})])
    orchestrator = AgentOrchestrator(ToolExecutor(registry, memory), memory, planner)
    task_id = memory.create_task("wait", TaskLimits())
    run = asyncio.create_task(orchestrator.run(task_id))
    await started.wait()
    assert orchestrator.cancel(task_id)["status"] == TaskStatus.CANCELLED
    assert (await run)["status"] == TaskStatus.CANCELLED


@pytest.mark.anyio
async def test_orchestrator_deadline_fails_slow_tool(tmp_path: Path) -> None:
    import asyncio

    from services.agent_api.app.permissions import RiskLevel
    from services.agent_api.app.tools import EmptyInput, Tool

    memory = MemoryRepository(tmp_path / "jarvis.db")
    memory.initialize()
    registry = build_registry(tmp_path / "workspace")

    async def slow(_value):
        await asyncio.sleep(2)
        return {"ok": True}

    registry.register(Tool("slow", "slow", EmptyInput, RiskLevel.LOW, 5, slow))
    planner = DeterministicPlanner([PlannedStep("slow", "slow", {})])
    orchestrator = AgentOrchestrator(ToolExecutor(registry, memory), memory, planner)
    task = await orchestrator.create("slow", TaskLimits(timeout_seconds=1))
    assert task["status"] == TaskStatus.FAILED
    assert task["error"] == "Task exceeded its execution time limit."


@pytest.mark.anyio
async def test_limits_fail_before_unbounded_execution(tmp_path: Path) -> None:
    memory = MemoryRepository(tmp_path / "jarvis.db")
    memory.initialize()
    planner = DeterministicPlanner(
        [PlannedStep("one", "echo", {"message": "1"}), PlannedStep("two", "echo", {"message": "2"})]
    )
    orchestrator = AgentOrchestrator(
        ToolExecutor(build_registry(tmp_path / "workspace"), memory), memory, planner
    )
    task = await orchestrator.create("many", TaskLimits(max_steps=1))
    assert task["status"] == TaskStatus.FAILED
