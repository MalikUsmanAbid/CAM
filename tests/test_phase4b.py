import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from services.agent_api.app.config import Settings, get_settings
from services.agent_api.app.main import app
from services.agent_api.app.model_contracts import (
    MAX_MODEL_RESPONSE_BYTES,
    ModelErrorCode,
    ModelProviderError,
    ModelRequest,
    validate_model_response,
)
from services.agent_api.app.providers import (
    BrainProviderStatus,
    MockBrainProvider,
    create_brain_provider,
    request_brain_plan,
)


def test_model_contracts_reject_unknown_fields_and_oversized_context() -> None:
    with pytest.raises(ValidationError):
        ModelRequest.model_validate(
            {"user_request": "hello", "max_output_tokens": 10, "unexpected": "nope"}
        )
    with pytest.raises(ValidationError):
        ModelRequest.model_validate(
            {
                "user_request": "hello",
                "max_output_tokens": 10,
                "context": {"unexpected": "nope"},
            }
        )
    with pytest.raises(ValidationError):
        ModelRequest(user_request="x" * 4_001, max_output_tokens=10)


def test_model_response_rejects_invalid_actions_unknown_fields_and_oversize() -> None:
    with pytest.raises(ModelProviderError) as missing_action:
        validate_model_response({"kind": "action", "summary": "Run a tool"})
    assert missing_action.value.code == ModelErrorCode.MALFORMED_RESPONSE
    with pytest.raises(ModelProviderError) as unknown_field:
        validate_model_response(
            {
                "kind": "action",
                "summary": "Run a tool",
                "action": {"tool_name": "echo", "arguments": {}, "extra": True},
            }
        )
    assert unknown_field.value.code == ModelErrorCode.MALFORMED_RESPONSE
    with pytest.raises(ModelProviderError) as invalid_name:
        validate_model_response(
            {
                "kind": "action",
                "summary": "Run a tool",
                "action": {"tool_name": "not-a-tool", "arguments": {}},
            }
        )
    assert invalid_name.value.code == ModelErrorCode.MALFORMED_RESPONSE
    with pytest.raises(ModelProviderError) as oversized:
        validate_model_response({"kind": "final", "summary": "x" * MAX_MODEL_RESPONSE_BYTES})
    assert oversized.value.code == ModelErrorCode.MALFORMED_RESPONSE


@pytest.mark.anyio
async def test_mock_brain_returns_a_valid_non_executing_plan() -> None:
    provider = MockBrainProvider("jarvis-dev")
    response = await request_brain_plan(
        provider, ModelRequest(user_request="list files", max_output_tokens=20), 1
    )
    assert response.kind == "final"
    assert "did not execute tools" in response.summary
    assert provider.status().capabilities == ("structured_planning",)


@pytest.mark.anyio
async def test_brain_provider_normalizes_unavailable_timeout_and_malformed_responses() -> None:
    class SlowProvider:
        name = "slow"
        model = "test"

        def status(self) -> BrainProviderStatus:
            return BrainProviderStatus(self.name, self.model, True, ())

        async def plan(self, request: ModelRequest) -> object:
            del request
            await asyncio.sleep(0.02)
            return {"kind": "final", "summary": "late"}

    class MalformedProvider(SlowProvider):
        async def plan(self, request: ModelRequest) -> object:
            del request
            return {"kind": "unknown", "provider_detail": "secret-value"}

    request = ModelRequest(user_request="hello", max_output_tokens=20)
    defaults = get_settings()
    unavailable = create_brain_provider(
        Settings(
            ai_provider="reserved",
            ai_model="test",
            ai_api_key_ref=None,
            ai_endpoint=None,
            ai_timeout_seconds=1,
            ai_max_output_tokens=20,
            backend_host="127.0.0.1",
            backend_port=8000,
            database_path=defaults.database_path,
            safe_workspace=defaults.safe_workspace,
            brain_provider="openai_compatible",
            brain_model="test",
        )
    )
    with pytest.raises(ModelProviderError) as unavailable_error:
        await request_brain_plan(unavailable, request, 1)
    assert unavailable_error.value.code == ModelErrorCode.UNAVAILABLE
    with pytest.raises(ModelProviderError) as timeout_error:
        await request_brain_plan(SlowProvider(), request, 0.001)  # type: ignore[arg-type]
    assert timeout_error.value.code == ModelErrorCode.TIMEOUT
    with pytest.raises(ModelProviderError) as malformed_error:
        await request_brain_plan(MalformedProvider(), request, 1)  # type: ignore[arg-type]
    assert malformed_error.value.code == ModelErrorCode.MALFORMED_RESPONSE
    assert "secret-value" not in malformed_error.value.detail


def test_brain_provider_factory_and_secret_free_status(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "mock")
    monkeypatch.setenv("AI_MODEL", "jarvis-test")
    settings = get_settings()
    provider = create_brain_provider(settings)
    assert provider.status() == BrainProviderStatus(
        provider="mock", model="jarvis-test", available=True, capabilities=("structured_planning",)
    )

    monkeypatch.setenv("AI_API_KEY", "sensitive-test-value")
    monkeypatch.setenv("AI_API_KEY_REF", "env:UNRESOLVED_PRIVATE_REFERENCE")
    with TestClient(app) as client:
        response = client.get("/api/v1/ai/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "provider": "mock",
        "model": "jarvis-test",
        "available": True,
        "supported_capabilities": ["structured_planning"],
        "availability_reason": None,
    }
    assert "sensitive-test-value" not in response.text
    assert "vault://private-reference" not in response.text
