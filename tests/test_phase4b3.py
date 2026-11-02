from pathlib import Path

import pytest

from services.agent_api.app.context_builder import ContextBuilder
from services.agent_api.app.memory import MemoryRepository
from services.agent_api.app.model_contracts import validate_model_response
from services.agent_api.app.model_planner import ModelPlanner
from services.agent_api.app.planner import DeterministicPlanner
from services.agent_api.app.providers import BrainProviderStatus
from services.agent_api.app.tools import build_registry


def _setup(tmp_path: Path):
    memory = MemoryRepository(tmp_path / "db.sqlite")
    memory.initialize()
    registry = build_registry(tmp_path / "workspace")
    return ContextBuilder(memory, registry), registry


class PlanProvider:
    name = "test"
    model = "test"

    def __init__(self, value):
        self.value = value

    def status(self):
        return BrainProviderStatus("test", "test", True, ("structured_planning",))

    async def plan(self, request):
        return self.value


def valid_plan(tool="echo", arguments=None):
    return {"kind": "plan", "plan": {"goal": "echo", "success_criteria": ["done"],
        "steps": [{"step_id": "step1", "tool_name": tool, "arguments": arguments or {"message": "hello"},
                   "purpose": "echo safely", "expected_result": "message returned"}],
        "reasoning_summary": "bounded", "completion_strategy": "finish"}}


@pytest.mark.anyio
async def test_valid_plan_is_structured_and_validated(tmp_path):
    builder, registry = _setup(tmp_path)
    planner = ModelPlanner(builder, PlanProvider(valid_plan()), registry, DeterministicPlanner())
    steps = await planner.plan("echo", object())
    assert steps[0].tool_name == "echo"
    assert steps[0].arguments == {"message": "hello"}


@pytest.mark.anyio
async def test_unknown_tool_and_invalid_arguments_fall_back_safely(tmp_path):
    builder, registry = _setup(tmp_path)
    for response in (valid_plan("missing"), valid_plan("echo", {"unknown": True})):
        planner = ModelPlanner(builder, PlanProvider(response), registry, DeterministicPlanner())
        steps = await planner.plan("echo fallback", object())
        assert steps[0].tool_name == "echo"


def test_plan_contract_rejects_unknown_fields_and_oversized_steps():
    with pytest.raises(Exception):
        validate_model_response({"kind": "plan", "plan": {"goal": "x", "success_criteria": ["x"],
            "steps": [{"step_id": "x", "tool_name": "echo", "arguments": {}, "purpose": "x", "expected_result": "x", "extra": 1}],
            "reasoning_summary": "x", "completion_strategy": "x"}})
    with pytest.raises(Exception):
        validate_model_response({"kind": "plan", "plan": {"goal": "x", "success_criteria": ["x"],
            "steps": [dict(valid_plan()["plan"]["steps"][0], step_id=f"s{i}") for i in range(9)],
            "reasoning_summary": "x", "completion_strategy": "x"}})


@pytest.mark.anyio
async def test_provider_failure_falls_back_and_never_executes(tmp_path):
    builder, registry = _setup(tmp_path)
    class Failing(PlanProvider):
        async def plan(self, request):
            raise TimeoutError()
    planner = ModelPlanner(builder, Failing(None), registry, DeterministicPlanner())
    steps = await planner.plan("echo no execution", object())
    assert steps[0].tool_name == "echo"
    assert not (tmp_path / "workspace" / "created.txt").exists()
