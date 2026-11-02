"""Strict, provider-neutral contracts for untrusted model planning proposals."""

from __future__ import annotations

import asyncio
import json
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

MAX_MODEL_RESPONSE_BYTES = 32_000


class StrictModel(BaseModel):
    """Reject fields that are not part of a reviewed contract."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ContextSource(StrEnum):
    """Origin labels make it impossible to mistake data for trusted instructions."""

    SYSTEM = "system"
    TASK = "task"
    USER = "user"
    MEMORY = "memory"
    TOOL_RESULT = "tool_result"
    EXTERNAL = "external"
    CAPABILITY_METADATA = "capability_metadata"


class ContextTrust(StrEnum):
    TRUSTED_INSTRUCTION = "trusted_instruction"
    UNTRUSTED_DATA = "untrusted_data"


class ModelContextItem(StrictModel):
    """A bounded, labelled item supplied as data to a future model provider."""

    source: ContextSource
    trust: ContextTrust
    label: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=4_000)
    reference_id: str | None = Field(default=None, min_length=1, max_length=100)
    truncated: bool = False


class ModelContext(StrictModel):
    """Structured, source-labelled context. Non-system values are always data."""

    system_context: list[ModelContextItem] = Field(default_factory=list, max_length=2)
    task_context: list[ModelContextItem] = Field(default_factory=list, max_length=2)
    user_context: list[ModelContextItem] = Field(default_factory=list, max_length=2)
    memories: list[ModelContextItem] = Field(default_factory=list, max_length=8)
    tool_metadata: list[ModelContextItem] = Field(default_factory=list, max_length=12)
    tool_results: list[ModelContextItem] = Field(default_factory=list, max_length=4)
    external_context: list[ModelContextItem] = Field(default_factory=list, max_length=4)
    metadata: dict[str, str | int | bool] = Field(default_factory=dict, max_length=10)


class ModelRequest(StrictModel):
    """Bounded context for a single planning proposal; it carries no credentials."""

    user_request: str = Field(min_length=1, max_length=4_000)
    context: ModelContext = Field(default_factory=ModelContext)
    max_output_tokens: int = Field(ge=1, le=4_096)
    task_id: str | None = Field(default=None, min_length=1, max_length=100)


class ModelAction(StrictModel):
    """A proposed registry action. It is not an instruction to execute a tool."""

    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")
    arguments: dict[str, object] = Field(default_factory=dict, max_length=50)
    rationale_summary: str | None = Field(default=None, max_length=500)


class ModelPlanStep(StrictModel):
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,49}$")
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")
    arguments: dict[str, object] = Field(default_factory=dict, max_length=20)
    purpose: str = Field(min_length=1, max_length=500)
    expected_result: str = Field(min_length=1, max_length=500)


class ModelPlan(StrictModel):
    goal: str = Field(min_length=1, max_length=500)
    success_criteria: list[str] = Field(min_length=1, max_length=8)
    steps: list[ModelPlanStep] = Field(min_length=1, max_length=8)
    reasoning_summary: str = Field(min_length=1, max_length=500)
    completion_strategy: str = Field(min_length=1, max_length=300)


class ModelPlanResponse(StrictModel):
    kind: Literal["plan"]
    plan: ModelPlan


class ModelFinalResponse(StrictModel):
    kind: Literal["final"]
    summary: str = Field(min_length=1, max_length=2_000)


class ModelActionResponse(StrictModel):
    kind: Literal["action"]
    summary: str = Field(min_length=1, max_length=2_000)
    action: ModelAction


class ModelClarificationResponse(StrictModel):
    kind: Literal["clarification"]
    summary: str = Field(min_length=1, max_length=2_000)
    question: str = Field(min_length=1, max_length=1_000)


class ModelCompleteResponse(StrictModel):
    kind: Literal["complete"]
    summary: str = Field(min_length=1, max_length=2_000)


ModelResponse = Annotated[
    ModelFinalResponse
    | ModelActionResponse
    | ModelClarificationResponse
    | ModelCompleteResponse
    | ModelPlanResponse,
    Field(discriminator="kind"),
]
_MODEL_RESPONSE_ADAPTER = TypeAdapter(ModelResponse)


class ModelErrorCode(StrEnum):
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTHENTICATION_FAILED = "authentication_failed"
    CONTEXT_TOO_LARGE = "context_too_large"
    MALFORMED_RESPONSE = "malformed_response"
    INTERNAL_ERROR = "internal_error"


class ModelProviderError(RuntimeError):
    """A safe, normalized provider error that deliberately excludes raw provider text."""

    def __init__(self, code: ModelErrorCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def validate_model_response(value: object) -> ModelResponse:
    """Validate a bounded provider result without retaining its raw representation."""
    try:
        encoded = json.dumps(
            value.model_dump(mode="json") if isinstance(value, BaseModel) else value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ModelProviderError(
            ModelErrorCode.MALFORMED_RESPONSE, "Model response did not match the required schema."
        ) from exc
    if len(encoded) > MAX_MODEL_RESPONSE_BYTES:
        raise ModelProviderError(
            ModelErrorCode.MALFORMED_RESPONSE, "Model response exceeded the allowed size."
        )
    try:
        return _MODEL_RESPONSE_ADAPTER.validate_json(encoded)
    except ValidationError as exc:
        raise ModelProviderError(
            ModelErrorCode.MALFORMED_RESPONSE, "Model response did not match the required schema."
        ) from exc


def normalize_model_error(error: Exception) -> ModelProviderError:
    """Map provider failures to fixed safe messages suitable for APIs and task records."""
    if isinstance(error, ModelProviderError):
        return error
    if isinstance(error, asyncio.TimeoutError):
        return ModelProviderError(ModelErrorCode.TIMEOUT, "Model request timed out.")
    return ModelProviderError(ModelErrorCode.INTERNAL_ERROR, "Model provider failed safely.")
