"""Injected transports only; temporary persistence and synthetic credentials."""

import json
import uuid
from urllib.parse import parse_qs

import httpx
import pytest

from services.agent_api.app.account_actions import (
    AccountActions,
    AccountInput,
    BlueskyPostInput,
    ThreadsPostInput,
)
from services.agent_api.app.migrations import initialize_database
from services.agent_api.app.tools import ToolExecutionOutcome


class Vault:
    def resolve(self, _):
        return 'synthetic-fixture-access-token'


@pytest.mark.anyio
@pytest.mark.parametrize('provider', ['bluesky', 'threads'])
@pytest.mark.parametrize('race', [False, True])
async def test_exact_text_transport_and_dispatch_revocation(tmp_path, provider, race):
    database = tmp_path / 'accounts.sqlite'
    initialize_database(database)
    requests = []
    proxies = []
    account_id = None
    class Proxy:
        def __init__(self, policy):
            assert policy.allowed_domains == ('pds.example',) and policy.exact_hosts
            assert policy.allowed_ports == {443} and not policy.allow_private_addresses
        async def start(self):
            proxies.append('started')
            return 'http://127.0.0.1:54321'
        async def close(self):
            proxies.append('closed')
    def transport(request):
        requests.append(request)
        return httpx.Response(200, json={'id': 'synthetic-receipt'})
    def factory(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        if provider == 'bluesky':
            assert kwargs.pop('proxy') == 'http://127.0.0.1:54321'
        if race:
            actions.revoke(account_id)
        return httpx.AsyncClient(transport=httpx.MockTransport(transport), **kwargs)
    actions = AccountActions(database, Vault(), client_factory=factory, proxy_factory=Proxy)
    config = {'provider': provider, 'label': 'Fixture', 'secret_reference': 'vault:' + 'a' * 32,
              'scopes': ['com.atproto.access'] if provider == 'bluesky' else ['threads_basic', 'threads_content_publish'],
              'transmission_consent': True}
    if provider == 'bluesky':
        config['service_base'] = 'https://pds.example'
    account_id = actions.connect(AccountInput(**config))
    common = {'operation_id': uuid.uuid4().hex, 'account_id': account_id, 'account_revision': 1,
              'body': 'Exact approved text', 'publish_publicly': True}
    if provider == 'bluesky':
        value = BlueskyPostInput(**common, service_base='https://pds.example', repository_did='did:plc:' + 'a' * 24,
                                 record_key='3lzzzzzzzzzzz', created_at='2026-10-18T10:00:00Z')
    else:
        value = ThreadsPostInput(**common)
    if race:
        with pytest.raises(ValueError, match='revoked'):
            await actions.dispatch(value)
        assert requests == []
    else:
        assert (await actions.dispatch(value))['status'] == 'accepted'
        assert len(requests) == 1
        request = requests[0]
        assert request.headers['authorization'] == 'Bearer synthetic-fixture-access-token'
        assert not request.url.query
        if provider == 'bluesky':
            assert str(request.url) == 'https://pds.example/xrpc/com.atproto.repo.createRecord'
            assert json.loads(request.content) == {'repo': 'did:plc:' + 'a' * 24, 'collection': 'app.bsky.feed.post',
                'rkey': value.record_key, 'validate': True, 'record': {'$type': 'app.bsky.feed.post', 'text': value.body, 'createdAt': value.created_at}}
        else:
            assert str(request.url) == 'https://graph.threads.net/v1.0/me/threads'
            assert parse_qs(request.content.decode()) == {'media_type': ['TEXT'], 'text': ['Exact approved text'],
                                                         'auto_publish_text': ['true'], 'reply_control': ['everyone']}
        with pytest.raises(ValueError, match='already dispatched'):
            await actions.dispatch(value)
        assert len(requests) == 1
    assert proxies == (['started', 'closed'] if provider == 'bluesky' else [])


def test_bluesky_origin_identity_timestamp_and_scope_are_bound(tmp_path):
    database = tmp_path / 'accounts.sqlite'
    initialize_database(database)
    actions = AccountActions(database, Vault())
    account_id = actions.connect(AccountInput(provider='bluesky', label='Fixture', secret_reference='vault:' + 'a' * 32,
        scopes=['com.atproto.access'], transmission_consent=True, service_base='https://pds.example'))
    account, _ = actions.account(account_id, 1)
    arguments = dict(operation_id='b' * 32, account_id=account_id, account_revision=1, service_base='https://other.example',
        repository_did='did:plc:' + 'a' * 24, record_key='3lzzzzzzzzzzz', created_at='2026-10-18T10:00:00Z',
        body='Exact text', publish_publicly=True)
    with pytest.raises(ValueError, match='destination changed'):
        actions.payload(account, BlueskyPostInput(**arguments))
    for field, invalid in [('repository_did', 'https://attacker.example'), ('created_at', '2026-10-18T10:00:00'),
                           ('record_key', '../overwrite'), ('publish_publicly', False)]:
        with pytest.raises(ValueError):
            BlueskyPostInput(**{**arguments, field: invalid})


@pytest.mark.anyio
async def test_threads_server_error_is_uncertain_and_cannot_automatically_replay(tmp_path):
    database = tmp_path / 'accounts.sqlite'
    initialize_database(database)
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(503)
    actions = AccountActions(database, Vault(), client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(respond), **kwargs))
    identifier = actions.connect(AccountInput(provider='threads', label='Fixture', secret_reference='vault:' + 'a' * 32,
        scopes=['threads_basic', 'threads_content_publish'], transmission_consent=True))
    value = ThreadsPostInput(operation_id=uuid.uuid4().hex, account_id=identifier, account_revision=1, body='Exact text', publish_publicly=True)
    with pytest.raises(ToolExecutionOutcome):
        await actions.dispatch(value)
    with pytest.raises(ValueError, match='already dispatched'):
        await actions.dispatch(value)
    assert len(requests) == 1
