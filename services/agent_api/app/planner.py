"""Provider-neutral deterministic planner and tool selector."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from services.agent_api.app.tools import ToolRegistry


@dataclass(frozen=True)
class PlannedStep:
    description: str
    tool_name: str
    arguments: dict[str, object]


class Planner(Protocol):
    async def plan(self, request: str, context: object) -> list[PlannedStep]: ...


class DeterministicPlanner:
    def __init__(self, steps: list[PlannedStep] | None = None) -> None:
        self.steps = steps

    async def plan(self, request: str, context: object) -> list[PlannedStep]:
        if self.steps is not None:
            return self.steps
        text = request.strip()
        if text.startswith("tool:"):
            name, _, raw = text[5:].partition(" ")
            return [PlannedStep(f"Run {name}", name, json.loads(raw or "{}"))]
        return [PlannedStep("Respond with a deterministic echo", "echo", {"message": text})]


class ToolSelector:
    def select(self, step: PlannedStep, registry: ToolRegistry):
        tool = registry.get(step.tool_name)
        validated = registry.validate_input(step.tool_name, step.arguments)
        return tool, validated
