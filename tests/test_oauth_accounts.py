"""OAuth regression source; no live provider requests or user persistence."""

import base64
import hashlib
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from services.agent_api.app.account_actions import AccountActions
from services.agent_api.app.action_policy import ActionPolicy
from services.agent_api.app.migrations import initialize_database
from services.agent_api.app.oauth_accounts import OAuthAccounts, OAuthClientInput, OAuthStartInput
from services.agent_api.app.session_security import execution_session


def broker_fixture(tmp_path):
    database = tmp_path / 'oauth.sqlite'
    initialize_database(database)
    calls = []
    active = {'owner': True}
    class Vault:
        def __init__(self):
            self.values = {}
        def store(self, value):
            reference = 'vault:' + uuid.uuid4().hex
            self.values[reference] = value
            return reference
        def resolve(self, reference):
            return self.values.get(reference)
    vault = Vault()
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={'token_type': 'Bearer', 'access_token': 'fixture-access',
                                       'refresh_token': 'fixture-refresh', 'expires_in': 3600,
                                       'scope': 'https://www.googleapis.com/auth/gmail.send'})
    app = SimpleNamespace(state=SimpleNamespace(
        settings=SimpleNamespace(database_path=database, backend_port=8123), secret_resolver=vault,
        sessions=SimpleNamespace(identity_active=lambda identity: active.get(identity, False)),
        executor=SimpleNamespace(policy=ActionPolicy(database)), account_actions=AccountActions(database, vault)))
    broker = OAuthAccounts(app, client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(transport), **kwargs))
    return broker, app, calls, active


@pytest.mark.anyio
async def test_oauth_pkce_single_use_callback_protected_tokens_and_refresh_revision(tmp_path):
    broker, app, calls, active = broker_fixture(tmp_path)
    context = execution_session.set('owner')
    try:
        broker.configure(OAuthClientInput(provider='google', client_id='fixture-client.apps.googleusercontent.com'))
        prepared = broker.start(OAuthStartInput(provider='google', label='Fixture account', scopes=['https://www.googleapis.com/auth/gmail.send'], transmission_consent=True))
        query = parse_qs(urlsplit(prepared['authorization_url']).query)
        assert query['redirect_uri'] == ['http://127.0.0.1:8123/api/v1/oauth/callback']
        assert query['code_challenge_method'] == ['S256']
        assert 'code_verifier' not in query
        state = query['state'][0]
        account = await broker.finish(state, 'fixture-code')
        parameters = parse_qs(calls[0].content.decode())
        challenge = base64.urlsafe_b64encode(hashlib.sha256(parameters['code_verifier'][0].encode()).digest()).decode().rstrip('=')
        assert challenge == query['code_challenge'][0]
        assert calls[0].url == 'https://oauth2.googleapis.com/token'
        with pytest.raises(ValueError, match='already consumed'):
            await broker.finish(state, 'fixture-code')
        assert len(calls) == 1
        assert app.state.account_actions.accounts()[0]['revision'] == 1
        await broker.refresh(account)
        assert len(calls) == 2
        assert app.state.account_actions.accounts()[0]['revision'] == 2
        with broker.records.connect() as db:
            serialized = str(db.execute('SELECT * FROM connected_accounts').fetchall()) + str(db.execute('SELECT * FROM oauth_account_tokens').fetchall())
            assert 'fixture-access' not in serialized and 'fixture-refresh' not in serialized
        active['owner'] = False
        with pytest.raises(ValueError, match='expired'):
            await broker.refresh(account)
        assert len(calls) == 2
    finally:
        execution_session.reset(context)


@pytest.mark.anyio
async def test_cancelled_state_and_changed_client_never_reach_transport(tmp_path):
    broker, _, calls, _ = broker_fixture(tmp_path)
    context = execution_session.set('owner')
    try:
        broker.configure(OAuthClientInput(provider='google', client_id='fixture-client'))
        value = OAuthStartInput(provider='google', label='Fixture', scopes=['https://www.googleapis.com/auth/gmail.send'], transmission_consent=True)
        first = broker.start(value)
        state = parse_qs(urlsplit(first['authorization_url']).query)['state'][0]
        broker.cancel()
        with pytest.raises(ValueError):
            await broker.finish(state, 'code')
        second = broker.start(value)
        state = parse_qs(urlsplit(second['authorization_url']).query)['state'][0]
        broker.configure(OAuthClientInput(provider='google', client_id='different-client'))
        with pytest.raises(ValueError, match='changed'):
            await broker.finish(state, 'code')
        assert calls == []
    finally:
        execution_session.reset(context)
