"""API request and response schemas."""

import re
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    message: str = Field(max_length=4_000)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Message cannot be empty.")
        return normalized


class ChatResponse(BaseModel):
    message: str
    provider: str
    model: str


class HealthResponse(BaseModel):
    status: str
    provider: str
    model: str
    provider_configured: bool
    authentication_required: bool = True


class AIStatusResponse(BaseModel):
    provider: str
    model: str
    available: bool
    supported_capabilities: list[str]
    availability_reason: str | None = None


class ToolExecutionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, object] = Field(default_factory=dict)
    project_id: str | None = Field(default=None, min_length=1, max_length=100)


class TaskLimitsRequest(BaseModel):
    max_steps: int = Field(default=8, ge=1, le=8)
    max_executions: int = Field(default=8, ge=1, le=8)
    max_failures: int = Field(default=2, ge=0, le=2)
    timeout_seconds: int = Field(default=60, ge=1, le=60)


class TaskCreateRequest(BaseModel):
    request: str = Field(min_length=1, max_length=4_000)
    project_id: str | None = Field(default=None, min_length=1, max_length=100)
    memory_ids: list[int] = Field(default_factory=list, max_length=20)
    limits: TaskLimitsRequest = Field(default_factory=TaskLimitsRequest)


class WorkflowCreateRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=50)
    idempotency_key: str = Field(min_length=1, max_length=100)
    task_id: str | None = Field(default=None, max_length=100)


class WorkflowTransitionRequest(BaseModel):
    status: str = Field(min_length=1, max_length=40)
    failure: str | None = Field(default=None, max_length=500)


class ScheduleCreateRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=100)
    schedule_type: str = Field(min_length=1, max_length=20)
    next_run: str = Field(min_length=1, max_length=50)
    retry_limit: int = Field(default=0, ge=0, le=3)


class BrowserSessionCreateRequest(BaseModel):
    allowed_domains: list[str] = Field(min_length=1, max_length=20)


class ComputerSessionCreateRequest(BaseModel):
    allowed_windows: list[str] = Field(min_length=1, max_length=20)


class MCPServerCreateRequest(BaseModel):
    server_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")
    name: str = Field(min_length=1, max_length=100)
    endpoint: str = Field(min_length=1, max_length=500)
    secret_reference: str | None = Field(default=None, max_length=200)

    @field_validator("endpoint")
    @classmethod
    def endpoint_is_explicit_and_secure(cls, value: str) -> str:
        parsed = urlparse(value)
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("MCP endpoint must not contain credentials, query, or fragment values.")
        if parsed.scheme != "https" or parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("MCP endpoint must use HTTPS.")
        return value.rstrip("/")

    @field_validator("secret_reference")
    @classmethod
    def secret_reference_is_env_name(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"(?:env:[A-Za-z_][A-Za-z0-9_]{0,127}|vault:[a-f0-9]{32})", value):
            raise ValueError("MCP secret reference must use an environment or protected vault reference.")
        return value


class MemoryRequest(BaseModel):
    memory_type: str = Field(min_length=1, max_length=50)
    content: str = Field(min_length=1, max_length=10_000)
    importance: int | None = Field(default=None, ge=1, le=5)
    source: str | None = Field(default=None, max_length=100)
