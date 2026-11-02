"""Model metadata only: fake transport, fake credentials and isolated API data."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from services.agent_api.app.model_catalog import CATALOGS, CatalogRequest, discover_models
from services.agent_api.app.session_security import execution_authorized

REFERENCE = 'vault:' + 'a' * 32


@pytest.mark.anyio
@pytest.mark.parametrize('provider', CATALOGS)
async def test_real_catalog_request_contract_and_bounded_model_choices(provider):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'data': [{'id': 'fixture/model', 'display_name': 'Friendly model'},
                                                {'id': 'bad id'}, {'id': 'fixture/model'}], 'has_more': True})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await discover_models(CatalogRequest(provider=provider, credential_reference=REFERENCE),
                                       SimpleNamespace(resolve=lambda ref: 'fake-secret'), client=client)
    assert len(requests) == 1
    request = requests[0]
    assert request.method == 'GET' and str(request.url) == CATALOGS[provider]
    assert request.content == b'' and 'fake-secret' not in str(request.url)
    assert request.headers.get('x-api-key' if provider == 'anthropic' else 'authorization') == ('fake-secret' if provider == 'anthropic' else 'Bearer fake-secret')
    assert result['models'] == [{'id': 'fixture/model', 'name': 'fixture/model'}]
    assert result['partial'] is True


@pytest.mark.parametrize('payload', [
    {'provider': 'other', 'credential_reference': REFERENCE},
    {'provider': 'gemini', 'credential_reference': 'plaintext-key'},
    {'provider': 'gemini', 'credential_reference': REFERENCE, 'endpoint': 'https://attacker.invalid'},
])
def test_catalog_rejects_arbitrary_endpoints_and_unprotected_keys(payload):
    with pytest.raises(ValidationError):
        CatalogRequest(**payload)


@pytest.mark.anyio
@pytest.mark.parametrize('status', [302, 401, 403, 429, 500])
async def test_catalog_never_follows_redirect_or_exposes_provider_error(status):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(status, headers={'Location': 'https://attacker.invalid'}, text='fake-secret')
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        with pytest.raises(HTTPException) as error:
            await discover_models(CatalogRequest(provider='nvidia', credential_reference=REFERENCE),
                                  SimpleNamespace(resolve=lambda ref: 'fake-secret'), client=client)
    assert len(requests) == 1 and 'fake-secret' not in error.value.detail


@pytest.mark.anyio
@pytest.mark.parametrize('data', [b'x' * 1_000_001, b'not-json', b'{"data":{}}', b'[]'], ids=['oversized', 'not-json', 'wrong-data-type', 'wrong-root-type'])
async def test_catalog_rejects_oversized_or_invalid_responses(data):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=data))) as client:
        with pytest.raises(HTTPException):
            await discover_models(CatalogRequest(provider='gemini', credential_reference=REFERENCE),
                                  SimpleNamespace(resolve=lambda ref: 'fake-secret'), client=client)


@pytest.mark.anyio
async def test_expired_session_and_revoked_credential_do_not_contact_provider():
    sent = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: sent.append(request))) as client:
        payload = CatalogRequest(provider='anthropic', credential_reference=REFERENCE)
        context = execution_authorized.set(lambda: False)
        try:
            with pytest.raises(HTTPException) as error:
                await discover_models(payload, SimpleNamespace(resolve=lambda ref: 'fake-secret'), client=client)
            assert error.value.status_code == 401
        finally:
            execution_authorized.reset(context)
        with pytest.raises(HTTPException):
            await discover_models(payload, SimpleNamespace(resolve=lambda ref: None), client=client)
    assert sent == []


@pytest.mark.unpaired
def test_discovery_api_requires_paired_session():
    from services.agent_api.app.main import app
    with TestClient(app) as client:
        assert client.post('/api/v1/models/discover', json={'provider': 'gemini', 'credential_reference': REFERENCE}).status_code == 401


def test_policy_change_while_queued_blocks_metadata_transport():
    from services.agent_api.app.main import app
    with TestClient(app) as client:
        async def check():
            gate = app.state.inference_gate
            await gate.acquire()
            entered = asyncio.Event()
            original = gate.acquire
            async def observed():
                entered.set()
                return await original()
            gate.acquire = observed
            try:
                task = asyncio.create_task(__import__('services.agent_api.app.model_catalog', fromlist=['discover']).discover(
                    CatalogRequest(provider='gemini', credential_reference=REFERENCE), SimpleNamespace(app=app)))
                await entered.wait()
                app.state.executor.policy.emergency_stop(True)
                gate.release()
                with pytest.raises(HTTPException) as error:
                    await task
                assert error.value.status_code == 403
            finally:
                gate.acquire = original
        client.portal.call(check)
