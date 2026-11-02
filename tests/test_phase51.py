from pathlib import Path

import pytest

from services.agent_api.app.capabilities import (
    Capability,
    CapabilityRegistry,
    CapabilityResolver,
    CapabilitySource,
    CapabilityStatus,
    ResolutionStatus,
    Skill,
    SkillRegistry,
)
from services.agent_api.app.memory import MemoryRepository
from services.agent_api.app.orchestrator import AgentOrchestrator
from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.planner import DeterministicPlanner
from services.agent_api.app.tasks import TaskStatus
from services.agent_api.app.tools import EmptyInput, Tool, ToolExecutor, build_registry


def capability() -> Capability:
    return Capability(capability_id="tool.echo", name="echo", description="Echoes text.", category="tool", source_type=CapabilitySource.BUILTIN_TOOL, version="1.0.0", risk_level=RiskLevel.LOW, required_permission="low", available_tools=["echo"])


def test_capability_and_skill_registries_are_strict_and_bounded():
    registry = CapabilityRegistry()
    registry.register(capability())
    assert registry.get("tool.echo").status == CapabilityStatus.AVAILABLE
    skills = SkillRegistry(registry)
    skill = Skill(skill_id="echo.skill", name="Echo", description="safe", version="1.0.0", risk_level=RiskLevel.LOW, required_capabilities=["tool.echo"], allowed_tools=["echo"], instructions="Use echo.")
    skills.register(skill)
    assert skills.set_enabled("echo.skill", False).status.value == "disabled"


def test_resolver_waits_for_missing_capability_and_rejects_disabled():
    registry = CapabilityRegistry()
    assert CapabilityResolver(registry).resolve("use browser").status == ResolutionStatus.WAITING_FOR_CAPABILITY
    registry.register(capability())
    registry.set_enabled("tool.echo", False)
    assert CapabilityResolver(registry).resolve("echo").status == ResolutionStatus.WAITING_FOR_CAPABILITY


@pytest.mark.anyio
async def test_task_waits_then_resumes_when_capability_becomes_available(tmp_path: Path):
    memory = MemoryRepository(tmp_path / "db.sqlite")
    memory.initialize()
    tools = build_registry(tmp_path / "workspace")
    capabilities = CapabilityRegistry()
    resolver = CapabilityResolver(capabilities)
    orchestrator = AgentOrchestrator(ToolExecutor(tools, memory), memory, DeterministicPlanner(), resolver)
    task = await orchestrator.create("tool:browser_probe {}")
    assert task["status"] == TaskStatus.WAITING_FOR_CAPABILITY
    calls = []

    async def fake_browser_probe(_):
        calls.append("browser executor ran")
        return {"probe": "fake browser"}

    # A fake executor verifies dispatch; real browser isolation remains required.
    tools.register(Tool("browser_probe", "Fake browser probe", EmptyInput, RiskLevel.LOW, 2, fake_browser_probe))
    capabilities.register(Capability(capability_id="tool.browser_probe", name="browser_probe", description="fake browser", category="test", source_type=CapabilitySource.LOCAL, version="1.0.0", risk_level=RiskLevel.LOW, required_permission="low", available_tools=["browser_probe"]))
    resumed = await orchestrator.resume(task["task_id"])
    assert resumed["status"] == TaskStatus.COMPLETED
    assert calls == ["browser executor ran"]
    steps = memory.get_task_steps(task["task_id"])
    assert len(steps) == 1 and steps[0]["tool_name"] == "browser_probe"
    assert steps[0]["status"] == "success"


def test_skill_cannot_register_unavailable_capability():
    with pytest.raises((ValueError, KeyError)):
        SkillRegistry(CapabilityRegistry()).register(Skill(skill_id="bad", name="Bad", description="bad", version="1.0.0", risk_level=RiskLevel.LOW, required_capabilities=["missing"], instructions="x"))
