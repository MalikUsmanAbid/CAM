"""Safe adapter from BrainProvider proposals to orchestrator-ready steps."""

from __future__ import annotations

from dataclasses import dataclass

from services.agent_api.app.context_builder import ContextBuilder, ContextBuildOptions
from services.agent_api.app.model_contracts import (
    ModelActionResponse,
    ModelErrorCode,
    ModelPlanResponse,
    ModelProviderError,
    ModelResponse,
)
from services.agent_api.app.planner import DeterministicPlanner, PlannedStep
from services.agent_api.app.providers import BrainProvider, request_brain_plan
from services.agent_api.app.tools import ToolRegistry

MAX_PLAN_ARGUMENT_BYTES = 4_000


class ModelPlannerError(ModelProviderError):
    """Safe planner failure; no partial plan is returned."""


@dataclass(frozen=True)
class ModelPlanner:
    context_builder: ContextBuilder
    brain: BrainProvider
    registry: ToolRegistry
    fallback: DeterministicPlanner
    timeout_seconds: float = 15

    async def plan(self, request: str, context: object) -> list[PlannedStep]:
        task_id = getattr(context, "task_id", None)
        try:
            model_request = self.context_builder.build(
                ContextBuildOptions(task_text=request, task_id=task_id, memory_ids=getattr(context, "memory_ids", ()))
            )
            response = await request_brain_plan(self.brain, model_request, self.timeout_seconds)
            return self._validate_response(response)
        except (ModelProviderError, ValueError, KeyError, TypeError):
            try:
                return await self.fallback.plan(request, context)
            except Exception as fallback_error:
                raise ModelPlannerError(
                    ModelErrorCode.INTERNAL_ERROR, "Planning failed safely."
                ) from fallback_error

    def _validate_response(self, response: ModelResponse) -> list[PlannedStep]:
        if isinstance(response, ModelPlanResponse):
            steps = []
            for item in response.plan.steps:
                tool = self.registry.get(item.tool_name)
                if not tool.enabled:
                    raise ModelPlannerError(ModelErrorCode.MALFORMED_RESPONSE, "Plan selected a disabled tool.")
                validated = self.registry.validate_input(item.tool_name, item.arguments)
                if len(str(validated.model_dump()).encode()) > MAX_PLAN_ARGUMENT_BYTES:
                    raise ModelPlannerError(ModelErrorCode.MALFORMED_RESPONSE, "Plan arguments exceeded the allowed size.")
                steps.append(PlannedStep(item.purpose, tool.name, validated.model_dump()))
            return steps
        if isinstance(response, ModelActionResponse):
            tool = self.registry.get(response.action.tool_name)
            if not tool.enabled:
                raise ModelPlannerError(ModelErrorCode.MALFORMED_RESPONSE, "Plan selected a disabled tool.")
            validated = self.registry.validate_input(tool.name, response.action.arguments)
            return [PlannedStep(response.summary, tool.name, validated.model_dump())]
        raise ModelPlannerError(ModelErrorCode.MALFORMED_RESPONSE, "Model did not return an executable plan.")
