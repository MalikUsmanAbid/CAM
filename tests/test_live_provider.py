import asyncio
import json

import httpx
import pytest

from services.agent_api.app.config import get_settings
from services.agent_api.app.context_builder import ContextBuilder
from services.agent_api.app.memory import MemoryRepository
from services.agent_api.app.model_contracts import ModelErrorCode, ModelProviderError, ModelRequest
from services.agent_api.app.model_planner import ModelPlanner
from services.agent_api.app.orchestrator import AgentOrchestrator
from services.agent_api.app.planner import DeterministicPlanner
from services.agent_api.app.providers import (
    BrainProviderStatus,
    DeterministicModelRouter,
    EnvironmentSecretResolver,
    OpenAICompatibleBrainProvider,
    OpenAICompatibleChatProvider,
    ProviderUnavailableError,
    ReservedCloudProvider,
    UnavailableBrainProvider,
    create_brain_provider,
    create_brain_router,
    create_provider,
    request_brain_plan,
)
from services.agent_api.app.tools import ToolExecutor, build_registry


@pytest.fixture(autouse=True)
def do_not_load_local_env_file(monkeypatch):
    from services.agent_api.app import config

    monkeypatch.setattr(config, "_load_env_file", lambda: None)


def _completion(content: str, status_code: int = 200) -> httpx.Response:
    body = {"choices": [{"message": {"content": content}}]} if status_code == 200 else {"error": "private provider detail"}
    return httpx.Response(status_code, json=body)


@pytest.mark.anyio
async def test_http_brain_contract_is_bounded_and_provider_neutral():
    response = {"kind": "final", "summary": "Plan proposal only."}
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return _completion(json.dumps(response))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleBrainProvider(
            "test-model", "https://provider.example/v1", "test-secret", client
        )
        result = await request_brain_plan(
            provider, ModelRequest(user_request="plan safely", max_output_tokens=32), 1
        )

    assert result.kind == "final"
    assert seen["url"] == "https://provider.example/v1/chat/completions"
    assert seen["authorization"] == "Bearer test-secret"
    assert seen["body"]["model"] == "test-model"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert "tools" not in seen["body"]
    assert seen["body"]["messages"][1]["content"].find("plan safely") >= 0
    assert "test-secret" not in str(provider.status())


def test_secret_reference_resolution_and_unavailable_without_credentials(monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.setenv("BRAIN_PROVIDER", "openai_compatible")
    monkeypatch.setenv("BRAIN_ENDPOINT", "https://provider.example/v1")
    monkeypatch.setenv("BRAIN_API_KEY_REF", "env:JARVIS_TEST_PROVIDER_TOKEN")
    monkeypatch.delenv("JARVIS_TEST_PROVIDER_TOKEN", raising=False)
    settings = get_settings()
    assert settings.brain_provider_configured is False
    unavailable = create_brain_router(settings)
    assert unavailable.status() == BrainProviderStatus(
        "openai_compatible", "jarvis-dev", False, (), "Provider credential is not configured."
    )

    monkeypatch.setenv("JARVIS_TEST_PROVIDER_TOKEN", "test-only-secret")
    settings = get_settings()
    assert settings.brain_provider_configured is True
    available = create_brain_router(settings)
    assert available.status().available
    assert "test-only-secret" not in str(available.status())
    assert EnvironmentSecretResolver().resolve("env:JARVIS_TEST_PROVIDER_TOKEN") == "test-only-secret"
    assert EnvironmentSecretResolver().resolve("vault://unresolved") is None


@pytest.mark.anyio
async def test_http_chat_contract_uses_configured_endpoint_and_bounded_response():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return _completion("Hello from the configured provider.")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            "test-chat", "https://provider.example/v1", "test-secret", client=client
        )
        answer = await provider.respond("Hello JARVIS")

    assert answer == "Hello from the configured provider."
    assert seen["url"] == "https://provider.example/v1/chat/completions"
    assert seen["authorization"] == "Bearer test-secret"
    assert seen["body"]["model"] == "test-chat"
    assert "tools" not in seen["body"]
    assert "test-secret" not in answer
    assert "test-secret" not in json.dumps(seen["body"])


@pytest.mark.anyio
async def test_production_https_client_keeps_tls_certificate_verification(monkeypatch):
    from services.agent_api.app import providers as provider_module

    captured = {}
    request_schemes = []

    async def handler(request: httpx.Request) -> httpx.Response:
        request_schemes.append(request.url.scheme)
        return _completion("TLS-configured response")

    real_async_client = httpx.AsyncClient

    def mock_transport_client(*args, **kwargs):
        captured.update(kwargs)
        return real_async_client(
            *args, transport=httpx.MockTransport(handler), **kwargs
        )

    monkeypatch.setattr(provider_module.httpx, "AsyncClient", mock_transport_client)
    provider = OpenAICompatibleChatProvider(
        "test", "https://provider.example/v1", "test-only-key"
    )

    assert await provider.respond("hello") == "TLS-configured response"
    assert captured["verify"] is True
    assert captured["follow_redirects"] is False
    assert request_schemes == ["https"]


@pytest.mark.anyio
async def test_chat_http_failure_does_not_leak_provider_body_or_credential():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: _completion("", 401))
    ) as client:
        provider = OpenAICompatibleChatProvider(
            "test", "https://provider.example/v1", "hidden-token", client=client
        )
        with pytest.raises(ProviderUnavailableError) as error:
            await provider.respond("hello")
    assert "private provider detail" not in str(error.value)
    assert "hidden-token" not in str(error.value)


@pytest.mark.parametrize("status_code", [401, 403, 429, 500, 503])
@pytest.mark.anyio
async def test_chat_http_status_errors_are_normalized(status_code: int):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: _completion("", status_code))
    ) as client:
        provider = OpenAICompatibleChatProvider(
            "test", "https://provider.example/v1", "test-token", client=client
        )
        with pytest.raises(ProviderUnavailableError) as error:
            await provider.respond("hello")
    assert "private provider detail" not in str(error.value)
    assert "test-token" not in str(error.value)


@pytest.mark.anyio
async def test_chat_rejects_malformed_oversized_and_redirected_responses():
    malformed_responses = (
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": 12}}]}),
    )
    for response in malformed_responses:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request, value=response: value)
        ) as client:
            provider = OpenAICompatibleChatProvider(
                "test", "https://provider.example/v1", "test", client=client
            )
            with pytest.raises(ProviderUnavailableError):
                await provider.respond("hello")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 40_001))
    ) as client:
        provider = OpenAICompatibleChatProvider(
            "test", "https://provider.example/v1", "test", client=client
        )
        with pytest.raises(ProviderUnavailableError, match="allowed size"):
            await provider.respond("hello")

    requests = []

    async def redirect(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://redirect.example/steal"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect)) as client:
        provider = OpenAICompatibleChatProvider(
            "test", "https://provider.example/v1", "test", client=client
        )
        with pytest.raises(ProviderUnavailableError, match="redirects are not followed"):
            await provider.respond("hello")
    assert len(requests) == 1


@pytest.mark.anyio
async def test_chat_timeout_is_normalized_and_cancellation_propagates():
    async def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider included private data")

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler)) as client:
        provider = OpenAICompatibleChatProvider(
            "test", "https://provider.example/v1", "test-token", client=client
        )
        with pytest.raises(ProviderUnavailableError, match="timed out") as error:
            await provider.respond("hello")
        assert "private data" not in str(error.value)

    async def slow_response_handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return _completion("arrived too late")

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow_response_handler)) as client:
        provider = OpenAICompatibleChatProvider(
            "test", "https://provider.example/v1", "test-token", timeout_seconds=0.005,
            client=client,
        )
        with pytest.raises(ProviderUnavailableError, match="timed out"):
            await provider.respond("hello")

    entered = asyncio.Event()

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await asyncio.sleep(10)
        return _completion("too late")

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow_handler)) as client:
        provider = OpenAICompatibleChatProvider(
            "test", "https://provider.example/v1", "test-token", client=client
        )
        pending = asyncio.create_task(provider.respond("hello"))
        await entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending


def test_live_chat_provider_is_unavailable_without_an_explicit_credential(monkeypatch):
    from services.agent_api.app.providers import ReservedCloudProvider, create_provider

    monkeypatch.setenv("AI_PROVIDER", "openai_compatible")
    monkeypatch.setenv("AI_ENDPOINT", "https://provider.example/v1")
    monkeypatch.setenv("AI_API_KEY_REF", "env:JARVIS_MISSING_CHAT_KEY")
    monkeypatch.delenv("JARVIS_MISSING_CHAT_KEY", raising=False)
    monkeypatch.setenv("AI_API_KEY", "legacy-key-must-not-be-used")
    settings = get_settings()
    assert settings.provider_configured is False
    assert isinstance(create_provider(settings), ReservedCloudProvider)


@pytest.mark.anyio
async def test_chat_factory_uses_validated_endpoint_model_timeout_and_env_reference(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai_compatible")
    monkeypatch.setenv("AI_MODEL", "configured-model")
    monkeypatch.setenv("AI_ENDPOINT", "https://provider.example/v1")
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("AI_API_KEY_REF", "env:JARVIS_TEST_CHAT_KEY")
    monkeypatch.setenv("JARVIS_TEST_CHAT_KEY", "mock-only-chat-key")
    monkeypatch.setenv("BRAIN_PROVIDER", "openai_compatible")
    monkeypatch.setenv("BRAIN_ENDPOINT", "https://planner.example/v1")
    monkeypatch.setenv("BRAIN_API_KEY_REF", "env:JARVIS_TEST_BRAIN_KEY")
    monkeypatch.setenv("JARVIS_TEST_BRAIN_KEY", "mock-only-brain-key")
    monkeypatch.setenv("AI_API_KEY", "legacy-key-must-not-be-used")
    settings = get_settings()
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.host] = {
            "authorization": request.headers.get("authorization"),
            "model": json.loads(request.content)["model"],
        }
        if request.url.host == "planner.example":
            return _completion(json.dumps({"kind": "final", "summary": "Plan response"}))
        return _completion("Configured response")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = create_provider(settings, client=client)
        assert isinstance(provider, OpenAICompatibleChatProvider)
        assert provider._transport._timeout_seconds == 7
        assert await provider.respond("hello") == "Configured response"
        brain = create_brain_provider(settings, client=client)
        assert isinstance(brain, OpenAICompatibleBrainProvider)
        assert brain._transport._timeout_seconds == 7
        result = await request_brain_plan(
            brain, ModelRequest(user_request="plan", max_output_tokens=8), 1
        )
        assert result.kind == "final"

    assert seen == {
        "provider.example": {
            "authorization": "Bearer mock-only-chat-key",
            "model": "configured-model",
        },
        "planner.example": {
            "authorization": "Bearer mock-only-brain-key",
            "model": "configured-model",
        },
    }


def test_missing_planning_reference_never_uses_chat_credentials(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai_compatible")
    monkeypatch.setenv("AI_ENDPOINT", "https://chat.example/v1")
    monkeypatch.setenv("AI_API_KEY_REF", "env:JARVIS_TEST_CHAT_ONLY_KEY")
    monkeypatch.setenv("JARVIS_TEST_CHAT_ONLY_KEY", "chat-only-test-key")
    monkeypatch.setenv("BRAIN_PROVIDER", "openai_compatible")
    monkeypatch.setenv("BRAIN_ENDPOINT", "https://planner.example/v1")
    monkeypatch.delenv("BRAIN_API_KEY_REF", raising=False)
    monkeypatch.setenv("AI_API_KEY", "legacy-chat-test-key")

    settings = get_settings()
    brain = create_brain_provider(settings)

    assert settings.provider_configured is True
    assert settings.brain_provider_configured is False
    assert isinstance(brain, UnavailableBrainProvider)
    assert not brain.status().available
    assert "chat-only-test-key" not in str(brain.status())
    assert "legacy-chat-test-key" not in str(brain.status())


@pytest.mark.parametrize("selected_provider", ["chat", "planning"])
def test_invalid_provider_credential_reference_fails_safely(monkeypatch, selected_provider):
    monkeypatch.setenv("AI_PROVIDER", "openai_compatible")
    monkeypatch.setenv("AI_ENDPOINT", "https://chat.example/v1")
    monkeypatch.setenv("AI_API_KEY_REF", "env:JARVIS_TEST_OTHER_PROVIDER_KEY")
    monkeypatch.setenv("JARVIS_TEST_OTHER_PROVIDER_KEY", "other-provider-test-key")
    monkeypatch.setenv("BRAIN_PROVIDER", "openai_compatible")
    monkeypatch.setenv("BRAIN_ENDPOINT", "https://planner.example/v1")
    monkeypatch.setenv("BRAIN_API_KEY_REF", "env:JARVIS_TEST_BRAIN_KEY")
    monkeypatch.setenv("JARVIS_TEST_BRAIN_KEY", "planning-test-key")
    monkeypatch.setenv("AI_API_KEY", "legacy-test-key")
    if selected_provider == "chat":
        monkeypatch.setenv("AI_API_KEY_REF", "file://not-a-reference")
    else:
        monkeypatch.setenv("BRAIN_API_KEY_REF", "../not-a-reference")

    settings = get_settings()

    if selected_provider == "chat":
        provider = create_provider(settings)
        assert settings.provider_configured is False
        assert isinstance(provider, ReservedCloudProvider)
    else:
        provider = create_brain_provider(settings)
        assert settings.brain_provider_configured is False
        assert isinstance(provider, UnavailableBrainProvider)
        assert not provider.status().available


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AI_ENDPOINT", "http://provider.example/v1"),
        ("AI_ENDPOINT", "https://user:password@provider.example/v1"),
        ("AI_ENDPOINT", "https://provider.example:invalid/v1"),
        ("BRAIN_ENDPOINT", "https://provider.example/v1?token=secret"),
        ("AI_TIMEOUT_SECONDS", "61"),
    ],
)
def test_invalid_live_provider_configuration_is_rejected(monkeypatch, name: str, value: str):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError) as error:
        get_settings()
    assert value not in str(error.value)


@pytest.mark.anyio
async def test_http_brain_maps_auth_rate_limit_and_response_size_without_leaking_body():
    for code, expected in (
        (401, ModelErrorCode.AUTHENTICATION_FAILED),
        (429, ModelErrorCode.RATE_LIMITED),
    ):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request, status=code: _completion("", status))
        ) as client:
            provider = OpenAICompatibleBrainProvider(
                "test", "https://provider.example/v1", "hidden", client
            )
            with pytest.raises(ModelProviderError) as error:
                await provider.plan(ModelRequest(user_request="x", max_output_tokens=1))
        assert error.value.code == expected
        assert "private provider detail" not in error.value.detail
        assert "hidden" not in error.value.detail

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b" " * 40_001)
        )
    ) as client:
        provider = OpenAICompatibleBrainProvider("test", "https://provider.example/v1", "hidden", client)
        with pytest.raises(ModelProviderError) as error:
            await provider.plan(ModelRequest(user_request="x", max_output_tokens=1))
    assert error.value.code == ModelErrorCode.MALFORMED_RESPONSE


@pytest.mark.anyio
async def test_brain_rejects_malformed_structured_planning_responses():
    malformed_responses = (
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"choices": []}),
        _completion("not-json"),
        _completion(json.dumps({"kind": "plan", "plan": {}})),
    )
    for response in malformed_responses:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request, value=response: value)
        ) as client:
            provider = OpenAICompatibleBrainProvider(
                "test", "https://provider.example/v1", "test-only-key", client
            )
            with pytest.raises(ModelProviderError) as error:
                await request_brain_plan(
                    provider,
                    ModelRequest(user_request="plan safely", max_output_tokens=16),
                    1,
                )
        assert error.value.code == ModelErrorCode.MALFORMED_RESPONSE
        assert "test-only-key" not in error.value.detail


@pytest.mark.anyio
async def test_router_checks_task_requirements_and_uses_existing_deterministic_fallback():
    provider = OpenAICompatibleBrainProvider("model-a", "https://provider.example/v1", "test")
    router = DeterministicModelRouter({"openai_compatible": provider}, "openai_compatible")
    request = ModelRequest(
        user_request="plan",
        max_output_tokens=12,
        context={"metadata": {"required_model": "model-b"}},
    )
    with pytest.raises(ModelProviderError) as error:
        await router.plan(request)
    assert error.value.code == ModelErrorCode.UNAVAILABLE


@pytest.mark.anyio
async def test_http_brain_timeout_is_normalized():
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return _completion('{"kind":"final","summary":"late"}')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleBrainProvider("model", "https://provider.example/v1", "test", client)
        with pytest.raises(ModelProviderError) as error:
            await request_brain_plan(
                provider, ModelRequest(user_request="slow", max_output_tokens=8), 0.001
            )
    assert error.value.code == ModelErrorCode.TIMEOUT


@pytest.mark.anyio
async def test_live_brain_plan_flows_through_validation_approval_executor_and_audit(tmp_path):
    proposed_plan = {
        "kind": "plan",
        "plan": {
            "goal": "write a note",
            "success_criteria": ["note file exists"],
            "steps": [
                {
                    "step_id": "write-note",
                    "tool_name": "write_file",
                    "arguments": {
                        "path": "live-note.txt",
                        "content": "created through the tool executor",
                    },
                    "purpose": "save the note",
                    "expected_result": "file created",
                }
            ],
            "reasoning_summary": "The registered file tool is suitable.",
            "completion_strategy": "Verify the tool result.",
        },
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        request_body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer test-only-provider-key"
        assert "test-only-provider-key" not in request.content.decode()
        assert request_body["model"] == "test-planner"
        return _completion(json.dumps(proposed_plan))

    memory = MemoryRepository(tmp_path / "jarvis.db")
    memory.initialize()
    registry = build_registry(tmp_path / "workspace")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        brain = OpenAICompatibleBrainProvider(
            "test-planner", "https://provider.example/v1", "test-only-provider-key", client
        )
        planner = ModelPlanner(
            ContextBuilder(memory, registry),
            brain,
            registry,
            DeterministicPlanner(),
            timeout_seconds=1,
        )
        orchestrator = AgentOrchestrator(ToolExecutor(registry, memory), memory, planner)

        waiting = await orchestrator.create("Please write a note")
        assert waiting["status"] == "waiting_for_approval"
        assert not (tmp_path / "workspace" / "live-note.txt").exists()
        approval = memory.pending_approvals()[0]
        memory.decide_approval(approval.request_id, True)
        completed = await orchestrator.resume(waiting["task_id"])

    assert completed["status"] == "completed"
    assert (tmp_path / "workspace" / "live-note.txt").read_text(encoding="utf-8") == (
        "created through the tool executor"
    )
    assert memory.get_task_steps(waiting["task_id"])[0]["status"] == "success"
    assert any(event["status"] == "tool_execution_succeeded" for event in memory.recent_events())


@pytest.mark.anyio
async def test_invalid_model_tool_arguments_fall_back_before_execution(tmp_path):
    invalid_plan = {
        "kind": "plan",
        "plan": {
            "goal": "write a note",
            "success_criteria": ["note file exists"],
            "steps": [
                {
                    "step_id": "write-note",
                    "tool_name": "write_file",
                    "arguments": {"path": "unvalidated-note.txt"},
                    "purpose": "save the note",
                    "expected_result": "file created",
                }
            ],
            "reasoning_summary": "The proposed file arguments are incomplete.",
            "completion_strategy": "Do not run an invalid proposal.",
        },
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return _completion(json.dumps(invalid_plan))

    memory = MemoryRepository(tmp_path / "jarvis.db")
    memory.initialize()
    workspace = tmp_path / "workspace"
    registry = build_registry(workspace)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        brain = OpenAICompatibleBrainProvider(
            "test-planner", "https://provider.example/v1", "test-only-key", client
        )
        planner = ModelPlanner(
            ContextBuilder(memory, registry),
            brain,
            registry,
            DeterministicPlanner(),
            timeout_seconds=1,
        )
        orchestrator = AgentOrchestrator(ToolExecutor(registry, memory), memory, planner)
        result = await orchestrator.create("Please write a note")

    assert result["status"] == "completed"
    assert not (workspace / "unvalidated-note.txt").exists()
    steps = memory.get_task_steps(result["task_id"])
    assert [step["tool_name"] for step in steps] == ["echo"]
    events = memory.recent_events()
    assert any(
        event["tool_name"] == "echo" and event["status"] == "tool_execution_succeeded"
        for event in events
    )
    assert not any(event["tool_name"] == "write_file" for event in events)
