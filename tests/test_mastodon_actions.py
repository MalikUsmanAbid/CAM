"""Exact-instance publishing regressions; all HTTP/proxy effects are injected."""

import uuid
from urllib.parse import parse_qs

import httpx
import pytest

from services.agent_api.app.account_actions import AccountActions, AccountInput, MastodonPostInput
from services.agent_api.app.migrations import initialize_database
from services.agent_api.app.workspace_records import WorkspaceRecords


@pytest.mark.parametrize('origin', ['http://social.example', 'https://name:secret@social.example', 'https://social.example/path', 'https://social.example?token=x', 'https://127.0.0.1', 'https://localhost', 'https://social.example:8443'])
def test_instance_configuration_rejects_ambiguous_or_nonpublic_origins(origin):
    with pytest.raises(ValueError):
        AccountInput(provider='mastodon', label='Fixture', secret_reference='vault:' + 'a' * 32,
            scopes=['write:statuses'], transmission_consent=True, service_base=origin)


@pytest.mark.anyio
@pytest.mark.parametrize('race', [None, 'revoke', 'destination'])
async def test_mastodon_sends_exact_payload_only_to_bound_public_instance_and_rechecks_after_proxy(tmp_path, race):
    database = tmp_path / 'temporary.sqlite'
    initialize_database(database)
    requests, lifecycle = [], []
    account_id = None
    class Vault:
        def resolve(self, reference):
            return 'fixture-instance-token'
    class Proxy:
        def __init__(self, policy):
            assert policy.exact_hosts and not policy.allow_private_addresses
            assert policy.allowed_domains == ('social.example',) and policy.allowed_ports == {443}
        async def start(self):
            lifecycle.append('started')
            if race == 'revoke':
                actions.revoke(account_id)
            if race == 'destination':
                with WorkspaceRecords(database).connect() as db:
                    db.execute('UPDATE account_services SET base_url=? WHERE account_id=?', ('https://other.example', account_id))
            return 'http://127.0.0.1:54321'
        async def close(self):
            lifecycle.append('closed')
    def transport(request):
        requests.append(request)
        return httpx.Response(200)
    def factory(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        assert kwargs.pop('proxy') == 'http://127.0.0.1:54321'
        return httpx.AsyncClient(transport=httpx.MockTransport(transport), **kwargs)
    actions = AccountActions(database, Vault(), client_factory=factory, proxy_factory=Proxy)
    account_id = actions.connect(AccountInput(provider='mastodon', label='Fixture instance',
        secret_reference='vault:' + 'a' * 32, scopes=['write:statuses'], transmission_consent=True, service_base='https://social.example/'))
    value = MastodonPostInput(operation_id=uuid.uuid4().hex, account_id=account_id, account_revision=1,
        service_base='https://social.example', body='Exact approved status', visibility='private', confirm_visibility=True,
        sensitive=True, spoiler_text='Exact warning')
    if race:
        with pytest.raises(ValueError):
            await actions.dispatch(value)
        assert requests == []
    else:
        result = await actions.dispatch(value)
        assert result['status'] == 'accepted' and len(requests) == 1
        assert str(requests[0].url) == 'https://social.example/api/v1/statuses'
        assert requests[0].headers['Idempotency-Key'] == value.operation_id
        assert parse_qs(requests[0].content.decode()) == {'status': ['Exact approved status'], 'visibility': ['private'],
            'sensitive': ['true'], 'spoiler_text': ['Exact warning']}
        mismatched = value.model_copy(update={'operation_id': uuid.uuid4().hex, 'service_base': 'https://other.example'})
        with pytest.raises(ValueError, match='destination changed'):
            await actions.dispatch(mismatched)
        assert len(requests) == 1
    assert lifecycle == ['started', 'closed']
