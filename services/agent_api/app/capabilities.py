"""Bounded capability and skill metadata registries; neither registry executes code."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.tools import ToolRegistry


class StrictMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CapabilitySource(StrEnum):
    BUILTIN_TOOL = "builtin_tool"
    SKILL = "skill"
    MCP = "mcp"
    INTEGRATION = "integration"
    PROVIDER = "provider"
    LOCAL = "local_capability"


class CapabilityStatus(StrEnum):
    AVAILABLE = "available"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


class Capability(StrictMetadata):
    capability_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,99}$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    category: str = Field(min_length=1, max_length=50)
    source_type: CapabilitySource
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", max_length=60)
    enabled: bool = True
    risk_level: RiskLevel
    required_permission: str = Field(min_length=1, max_length=100)
    available_tools: list[str] = Field(default_factory=list, max_length=20)
    status: CapabilityStatus = CapabilityStatus.AVAILABLE


class CapabilityRegistry:
    def __init__(self) -> None:
        self._items: dict[str, Capability] = {}

    def register(self, capability: Capability) -> Capability:
        if capability.capability_id in self._items:
            raise ValueError("Capability is already registered.")
        self._items[capability.capability_id] = capability
        return capability

    def get(self, capability_id: str) -> Capability:
        try:
            return self._items[capability_id]
        except KeyError as exc:
            raise KeyError("Unknown capability.") from exc

    def list(self) -> list[Capability]:
        return sorted(self._items.values(), key=lambda item: item.capability_id)

    def set_enabled(self, capability_id: str, enabled: bool) -> Capability:
        current = self.get(capability_id)
        updated = current.model_copy(update={"enabled": enabled, "status": CapabilityStatus.AVAILABLE if enabled else CapabilityStatus.DISABLED})
        self._items[capability_id] = updated
        return updated

    def unregister(self, capability_id: str) -> None:
        self._items.pop(capability_id, None)

    @classmethod
    def from_tools(cls, tools: ToolRegistry) -> "CapabilityRegistry":
        registry = cls()
        for tool in tools.list():
            registry.register(Capability(
                capability_id=f"tool.{tool.name}", name=tool.name,
                description=tool.description, category="tool",
                source_type=CapabilitySource.BUILTIN_TOOL, version="1.0.0",
                enabled=tool.enabled, risk_level=tool.risk,
                required_permission=str(tool.risk), available_tools=[tool.name],
                status=CapabilityStatus.AVAILABLE if tool.enabled else CapabilityStatus.DISABLED,
            ))
        return registry


class SkillStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class Skill(StrictMetadata):
    skill_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,99}$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", max_length=60)
    status: SkillStatus = SkillStatus.ENABLED
    required_capabilities: list[str] = Field(default_factory=list, max_length=20)
    allowed_tools: list[str] = Field(default_factory=list, max_length=20)
    risk_level: RiskLevel
    instructions: str = Field(min_length=1, max_length=2_000)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=20)


class SkillRegistry:
    def __init__(self, capabilities: CapabilityRegistry) -> None:
        self.capabilities = capabilities
        self._items: dict[str, Skill] = {}

    def register(self, skill: Skill) -> Skill:
        for capability_id in skill.required_capabilities:
            capability = self.capabilities.get(capability_id)
            if not capability.enabled or capability.status != CapabilityStatus.AVAILABLE:
                raise ValueError("Skill requires an unavailable capability.")
        if skill.skill_id in self._items:
            raise ValueError("Skill is already registered.")
        self._items[skill.skill_id] = skill
        return skill

    def get(self, skill_id: str) -> Skill:
        try:
            return self._items[skill_id]
        except KeyError as exc:
            raise KeyError("Unknown skill.") from exc

    def unregister(self, skill_id: str) -> None:
        self._items.pop(skill_id, None)

    def list(self) -> list[Skill]:
        return sorted(self._items.values(), key=lambda item: item.skill_id)

    def set_enabled(self, skill_id: str, enabled: bool) -> Skill:
        current = self.get(skill_id)
        updated = current.model_copy(update={"status": SkillStatus.ENABLED if enabled else SkillStatus.DISABLED})
        self._items[skill_id] = updated
        return updated


class ResolutionStatus(StrEnum):
    FOUND = "found"
    WAITING_FOR_CAPABILITY = "waiting_for_capability"
    REJECTED = "rejected"


class CapabilityResolution(StrictMetadata):
    status: ResolutionStatus
    capability_id: str | None = None
    reason: str = Field(min_length=1, max_length=300)


class CapabilityResolver:
    """Deterministic lookup only; it never executes tools or changes policy."""

    def __init__(self, capabilities: CapabilityRegistry, skills: SkillRegistry | None = None) -> None:
        self.capabilities = capabilities
        self.skills = skills or SkillRegistry(capabilities)

    def resolve(self, task: str) -> CapabilityResolution:
        normalized = task.strip().lower()
        # Explicit identifiers take precedence over incidental words in arguments.
        explicit = re.match(r"^tool:([a-z0-9_.-]+)(?:\s|$)", normalized)
        if explicit:
            name = explicit.group(1)
            matches = [item for item in self.capabilities.list()
                       if item.capability_id == f"tool.{name}" or name in item.available_tools]
            if not matches:
                return CapabilityResolution(status=ResolutionStatus.WAITING_FOR_CAPABILITY, reason="Requested tool capability is missing.")
            identifiers = [item.capability_id for item in matches]
        else:
            identifiers = re.findall(r"\b(?:tool|mcp|capability)\.[a-z0-9_.-]+", normalized)
        if identifiers:
            for identifier in identifiers:
                try:
                    capability = self.capabilities.get(identifier)
                except KeyError:
                    return CapabilityResolution(status=ResolutionStatus.WAITING_FOR_CAPABILITY, reason="Requested capability is missing.")
                if not capability.enabled or capability.status != CapabilityStatus.AVAILABLE:
                    return CapabilityResolution(status=ResolutionStatus.WAITING_FOR_CAPABILITY, reason="Requested capability is disabled or unavailable.")
            return CapabilityResolution(status=ResolutionStatus.FOUND, capability_id=identifiers[0], reason="Requested capability found.")
        for capability in self.capabilities.list():
            if (
                capability.name.lower() in normalized or capability.capability_id.lower() in normalized
            ):
                if not capability.enabled or capability.status != CapabilityStatus.AVAILABLE:
                    return CapabilityResolution(status=ResolutionStatus.WAITING_FOR_CAPABILITY, reason="Requested capability is disabled or unavailable.")
                return CapabilityResolution(status=ResolutionStatus.FOUND, capability_id=capability.capability_id, reason="Registered capability found.")
        if re.search(r"\b(?:browser|shell|mcp|computer|voice|download|credential)\b", normalized):
            return CapabilityResolution(status=ResolutionStatus.WAITING_FOR_CAPABILITY, reason="No registered capability matches this task.")
        if re.search(r"\b(?:tool|file|directory|echo|time)\b", normalized) or normalized:
            if not any(item.capability_id == "tool.echo" and item.enabled and item.status == CapabilityStatus.AVAILABLE for item in self.capabilities.list()):
                return CapabilityResolution(status=ResolutionStatus.WAITING_FOR_CAPABILITY, reason="The requested built-in capability is unavailable.")
            return CapabilityResolution(status=ResolutionStatus.FOUND, capability_id="tool.echo", reason="Built-in tool capability available.")
        return CapabilityResolution(status=ResolutionStatus.WAITING_FOR_CAPABILITY, reason="No registered capability matches this task.")
