"""Cloud adapter contracts; fake keys/transports and temporary persistence only."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from services.agent_api.app.cloud_models import ANTHROPIC_ENDPOINT, GEMINI_ENDPOINT, cloud_endpoint
from services.agent_api.app.config import get_settings
from services.agent_api.app.desktop_bootstrap import DesktopConfiguration
from services.agent_api.app.providers import (
    ProviderUnavailableError,
    ReservedCloudProvider,
    create_provider,
)
from services.agent_api.app.secure_store import SecureStore
from services.agent_api.app.usage_policy import CloudPolicy, UsagePolicy

ENDPOINTS = {'gemini': GEMINI_ENDPOINT, 'anthropic': ANTHROPIC_ENDPOINT,
             'openai_compatible': 'https://compatible.example/v1/chat/completions'}


@pytest.mark.parametrize('provider', ENDPOINTS)
def test_desktop_missing_cloud_key_returns_error_without_fallback(provider, monkeypatch, tmp_path):
    from services.agent_api.app.main import app

    selected = replace(settings(provider, monkeypatch), ai_api_key_ref=None)
    monkeypatch.setattr(app.state, 'desktop_settings', selected, raising=False)
    with TestClient(app) as client:
        response = client.post('/api/v1/chat', json={'message': 'keyless fixture'})
        assert response.status_code >= 400
        assert 'not configured' in response.text
        assert 'deterministic_fallback' not in response.text


def settings(provider, monkeypatch):
    monkeypatch.setenv('JARVIS_FIXTURE_KEY', 'fixture-not-a-real-key')
    return replace(get_settings(load_env_file=False), ai_provider=provider, ai_model='fixture-model',
                   ai_endpoint=ENDPOINTS[provider], ai_api_key_ref='env:JARVIS_FIXTURE_KEY')


def reply(provider, content='cloud fixture'):
    if provider == 'anthropic':
        return {'content': [{'type': 'text', 'text': content}], 'stop_reason': 'end_turn'}
    return {'choices': [{'message': {'content': content}}]}


@pytest.mark.parametrize('provider', ENDPOINTS)
def test_desktop_cloud_selection_without_keys_is_valid_but_unavailable(provider, tmp_path, monkeypatch):
    configuration = DesktopConfiguration(version=1, action='configure', data_directory=str(tmp_path / 'data'),
                                         workspace_directory=str(tmp_path / 'workspace'), provider=provider,
                                         model='fixture-model', endpoint=ENDPOINTS[provider])
    assert configuration.credential_reference is None
    selected = replace(settings(provider, monkeypatch), ai_api_key_ref=None)
    assert not selected.provider_configured
    assert isinstance(create_provider(selected), ReservedCloudProvider)


@pytest.mark.parametrize('provider', ['gemini', 'anthropic'])
@pytest.mark.parametrize('bad', ['https://attacker.example/v1/messages', 'http://127.0.0.1:9000',
                                 'https://api.anthropic.com/v1/messages?key=secret',
                                 'https://user:secret@api.anthropic.com/v1/messages'])
def test_named_provider_endpoint_cannot_redirect_credentials(provider, bad, tmp_path):
    with pytest.raises(ValueError):
        cloud_endpoint(provider, bad)
    with pytest.raises(ValidationError):
        DesktopConfiguration(version=1, action='configure', data_directory=str(tmp_path),
                             workspace_directory=str(tmp_path / 'workspace'), provider=provider,
                             model='fixture', endpoint=bad)


@pytest.mark.anyio
@pytest.mark.parametrize('provider', ENDPOINTS)
async def test_actual_outgoing_cloud_contract_and_structured_proposal(provider, monkeypatch):
    sent = []
    def respond(request):
        sent.append(request)
        return httpx.Response(200, json=reply(provider, '{"content":"approved proposal only"}'))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = create_provider(settings(provider, monkeypatch), client=client)
        assert adapter.name == provider
        result = await adapter.respond('Return JSON content', json_output=True)
    assert json.loads(result) == {'content': 'approved proposal only'}
    assert len(sent) == 1 and str(sent[0].url) == ENDPOINTS[provider]
    payload = json.loads(sent[0].content)
    assert payload['model'] == 'fixture-model' and payload['max_tokens'] == 1024
    assert 'fixture-not-a-real-key' not in sent[0].content.decode()
    if provider == 'anthropic':
        assert sent[0].headers['x-api-key'] == 'fixture-not-a-real-key'
        assert sent[0].headers['anthropic-version'] == '2023-06-01'
        assert 'authorization' not in sent[0].headers
        assert payload['messages'] == [{'role': 'user', 'content': 'Return JSON content'}]
        assert 'JSON object' in payload['system'] and 'response_format' not in payload
    else:
        assert sent[0].headers['authorization'] == 'Bearer fixture-not-a-real-key'
        assert payload['response_format'] == {'type': 'json_object'}


@pytest.mark.anyio
@pytest.mark.parametrize('provider', ['gemini', 'anthropic'])
async def test_budget_policy_change_while_queued_blocks_native_and_compatible_dispatch(provider, monkeypatch, tmp_path):
    path = tmp_path / 'cloud.sqlite'
    SecureStore(path).migrate()
    usage = UsagePolicy(path)
    policy = CloudPolicy(endpoint=ENDPOINTS[provider], model='fixture-model', enabled=True,
                         transmission_consent=True, daily_micro_usd=1_000_000, task_micro_usd=500_000,
                         input_micro_usd_per_million_tokens=1_000_000,
                         output_micro_usd_per_million_tokens=1_000_000)
    usage.configure(policy)
    assert policy.endpoint == ENDPOINTS[provider]
    sent = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: sent.append(r))) as client:
        adapter = create_provider(settings(provider, monkeypatch), client=client)
        gate = asyncio.Semaphore(0)
        queued = asyncio.Event()
        class Queued:
            async def __aenter__(self):
                queued.set()
                await gate.acquire()
            async def __aexit__(self, *args):
                gate.release()
        adapter._transport.dispatch_gate = Queued()
        adapter._transport.authorize_dispatch = usage.authorize
        pending = asyncio.create_task(adapter.respond('must not transmit'))
        await queued.wait()
        usage.configure(policy.model_copy(update={'enabled': False}))
        gate.release()
        with pytest.raises(ProviderUnavailableError, match='disabled'):
            await pending
    assert sent == []
    assert usage.status()['reserved_today_micro_usd'] == 0


@pytest.mark.anyio
@pytest.mark.parametrize('provider', ['gemini', 'anthropic'])
async def test_cloud_default_denial_and_exact_budget_authorization(provider, monkeypatch, tmp_path):
    path = tmp_path / 'cloud.sqlite'
    SecureStore(path).migrate()
    usage = UsagePolicy(path)
    sent = []
    def respond(request):
        sent.append(request)
        return httpx.Response(200, json=reply(provider))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = create_provider(settings(provider, monkeypatch), client=client)
        adapter._transport.authorize_dispatch = usage.authorize
        with pytest.raises(ProviderUnavailableError, match='disabled'):
            await adapter.respond('blocked until consent')
        assert sent == []
        usage.configure(CloudPolicy(endpoint=ENDPOINTS[provider], model='fixture-model', enabled=True,
                                    transmission_consent=True, daily_micro_usd=1_000_000,
                                    task_micro_usd=500_000, input_micro_usd_per_million_tokens=1_000_000,
                                    output_micro_usd_per_million_tokens=1_000_000))
        assert await adapter.respond('explicit fixture policy') == 'cloud fixture'
        monkeypatch.delenv('JARVIS_FIXTURE_KEY')
        with pytest.raises(ProviderUnavailableError, match='revoked'):
            await adapter.respond('revoked key')
    assert len(sent) == 1
    assert usage.status()['reserved_today_micro_usd'] > 0


@pytest.mark.anyio
@pytest.mark.parametrize('response', [
    {'content': [{'type': 'text', 'text': '{}'}], 'stop_reason': 'max_tokens'},
    {'content': [{'type': 'tool_use', 'name': 'delete_file', 'input': {}}], 'stop_reason': 'tool_use'},
    {'content': [{'type': 'text', 'text': '```json\n{}\n```'}], 'stop_reason': 'end_turn'},
    {'content': [{'type': 'text', 'text': '[]'}], 'stop_reason': 'end_turn'},
])
async def test_claude_malformed_truncated_or_tool_output_never_becomes_a_proposal(response, monkeypatch):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=response))) as client:
        adapter = create_provider(settings('anthropic', monkeypatch), client=client)
        with pytest.raises(ProviderUnavailableError, match='contract'):
            await adapter.respond('fixture', json_output=True)


@pytest.mark.anyio
@pytest.mark.parametrize('status', [302, 401, 429, 500])
async def test_claude_failures_are_redacted_and_redirects_not_followed(status, monkeypatch):
    sent = []
    def respond(request):
        sent.append(request)
        return httpx.Response(status, headers={'Location': 'https://attacker.example'},
                              json={'error': 'fixture-not-a-real-key'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        adapter = create_provider(settings('anthropic', monkeypatch), client=client)
        with pytest.raises(ProviderUnavailableError) as error:
            await adapter.respond('fixture')
    assert len(sent) == 1 and 'fixture-not-a-real-key' not in str(error.value)
