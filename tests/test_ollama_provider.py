"""Local adapter contract checks; mocked transport is not live inference proof."""

import json
from dataclasses import replace

import httpx
import pytest

from services.agent_api.app.config import get_settings
from services.agent_api.app.providers import (
    OllamaChatProvider,
    ReservedCloudProvider,
    create_provider,
)
from services.agent_api.app.session_security import (
    execution_authorized,
    execution_dispatch_authorized,
)


@pytest.fixture(autouse=True)
def local_configuration(monkeypatch):
    from services.agent_api.app import config
    monkeypatch.setattr(config, "_load_env_file", lambda: None)
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("AI_ENDPOINT", "http://127.0.0.1:11434")
    monkeypatch.setenv("AI_API_KEY_REF", "")


def test_local_provider_requires_no_credential():
    settings = get_settings()
    assert settings.provider_configured
    provider = create_provider(settings)
    assert isinstance(provider, OllamaChatProvider)
    assert provider.name == "ollama"


@pytest.mark.parametrize("endpoint", ["https://provider.example", "http://192.168.1.2:11434", "http://localhost:11434", "http://127.0.0.1:11434?token=x", "http://user:secret@127.0.0.1:11434", "http://127.0.0.1:99999", "http://[::1]:11434#fragment"])
def test_noncanonical_or_remote_local_adapter_is_unavailable(endpoint):
    settings = replace(get_settings(), ai_endpoint=endpoint)
    assert not settings.provider_configured
    assert isinstance(create_provider(settings), ReservedCloudProvider)


@pytest.mark.anyio
async def test_actual_local_transport_payload_and_final_authorization():
    received = []
    authorized = []
    def transport(request):
        received.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "local response"}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = create_provider(get_settings(), client=client)
        token = execution_dispatch_authorized.set(lambda: authorized.append(True))
        try:
            assert await provider.respond("fixture request") == "local response"
        finally:
            execution_dispatch_authorized.reset(token)
    assert authorized == [True]
    assert len(received) == 1
    assert str(received[0].url) == "http://127.0.0.1:11434/v1/chat/completions"
    assert "authorization" not in received[0].headers
    assert json.loads(received[0].content)["messages"][1]["content"] == "fixture request"
    assert "response_format" not in json.loads(received[0].content)


@pytest.mark.anyio
async def test_changed_remote_destination_and_revoked_session_never_transmit():
    received = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: received.append(request))) as client:
        provider = create_provider(get_settings(), client=client)
        provider._transport.endpoint = "https://provider.example/v1/chat/completions"
        with pytest.raises(ValueError, match="loopback"):
            await provider.respond("must not leave laptop")
        provider._transport.endpoint = "http://127.0.0.1:11434/v1/chat/completions"
        token = execution_authorized.set(lambda: False)
        try:
            with pytest.raises(Exception, match="session"):
                await provider.respond("revoked")
        finally:
            execution_authorized.reset(token)
    assert received == []


@pytest.mark.anyio
async def test_local_redirect_does_not_transmit_to_cloud():
    received = []
    def transport(request):
        received.append(request)
        return httpx.Response(302, headers={"Location": "https://provider.example/v1/chat/completions"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport), follow_redirects=True) as client:
        provider = create_provider(get_settings(), client=client)
        with pytest.raises(Exception, match="redirects"):
            await provider.respond("private fixture")
    assert len(received) == 1
    assert received[0].url.host == "127.0.0.1"


@pytest.mark.anyio
async def test_current_policy_denies_local_dispatch_before_transport():
    received = []
    def deny():
        raise ValueError("changed policy")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: received.append(request))) as client:
        provider = create_provider(get_settings(), client=client)
        token = execution_dispatch_authorized.set(deny)
        try:
            with pytest.raises(ValueError, match="changed policy"):
                await provider.respond("private fixture")
        finally:
            execution_dispatch_authorized.reset(token)
    assert received == []


@pytest.mark.anyio
async def test_owned_client_ignores_environment_proxy_without_network(monkeypatch):
    import httpcore

    import services.agent_api.app.providers as providers
    monkeypatch.setenv("HTTP_PROXY", "http://fixture-proxy.example:8080")
    monkeypatch.setenv("ALL_PROXY", "http://fixture-proxy.example:8080")
    monkeypatch.setenv("NO_PROXY", "")
    original = httpx.AsyncClient
    probes = []
    received = []
    def transport(request):
        received.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "owned local response"}}]})
    def owned_client(**arguments):
        # Inspect the actual default HTTPX routing, then supply a fixture-only
        # transport so this test never makes a network request.
        probe = original(**arguments)
        probes.append(probe)
        route = probe._transport_for_url(httpx.URL("http://127.0.0.1:11434/v1/chat/completions"))
        assert not isinstance(route._pool, httpcore.AsyncHTTPProxy)
        assert arguments["trust_env"] is False
        return original(**arguments, transport=httpx.MockTransport(transport))
    monkeypatch.setattr(providers.httpx, "AsyncClient", owned_client)
    try:
        provider = create_provider(get_settings())
        assert await provider.respond("private fixture") == "owned local response"
    finally:
        for probe in probes:
            await probe.aclose()
    assert len(probes) == 1 and len(received) == 1
    assert received[0].url.host == "127.0.0.1"
