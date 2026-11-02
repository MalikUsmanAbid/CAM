"""Fixed Pinterest API transport fixtures, never fetches a real image."""

import json
import uuid

import httpx
import pytest

from services.agent_api.app.account_actions import (
    SCOPES,
    AccountActions,
    AccountInput,
    PinterestPinInput,
)
from services.agent_api.app.migrations import initialize_database


def pin_arguments():
    return dict(operation_id=uuid.uuid4().hex, account_id='a' * 32, account_revision=1, board_id='12345678',
                title='Fixture title', description='Fixture description', alt_text='Fixture alternative text',
                image_url='https://images.example/fixture.png', publish_to_board=True)


@pytest.mark.parametrize('url', ['http://images.example/a.png', 'https://127.0.0.1/a.png', 'https://10.0.0.1/a.png',
    'https://localhost/a.png', 'https://host.local/a.png', 'https://user:secret@images.example/a.png',
    'https://images.example/a.png?token=fixture', 'https://images.example/a.png#fragment', 'https://images.example:8443/a.png'])
def test_pin_rejects_credentialed_ambiguous_or_local_image_urls(url):
    with pytest.raises(ValueError):
        PinterestPinInput(**{**pin_arguments(), 'image_url': url})


@pytest.mark.anyio
@pytest.mark.parametrize('status', [200, 201])
async def test_pin_transport_exact_board_content_media_and_no_local_image_fetch(tmp_path, status):
    database = tmp_path / 'pinterest.sqlite'
    initialize_database(database)
    requests = []
    class Vault:
        def resolve(self, _): return 'synthetic-fixture-token'
    def transport(request):
        requests.append(request)
        return httpx.Response(status)
    def factory(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return httpx.AsyncClient(transport=httpx.MockTransport(transport), **kwargs)
    actions = AccountActions(database, Vault(), client_factory=factory)
    identifier = actions.connect(AccountInput(provider='pinterest', label='Fixture', secret_reference='vault:' + 'a' * 32,
        scopes=sorted(SCOPES['pinterest']), transmission_consent=True))
    value = PinterestPinInput(**{**pin_arguments(), 'account_id': identifier})
    assert (await actions.dispatch(value))['status'] == 'accepted'
    assert len(requests) == 1
    assert str(requests[0].url) == 'https://api.pinterest.com/v5/pins'
    assert json.loads(requests[0].content) == {'board_id': value.board_id, 'title': value.title,
        'description': value.description, 'alt_text': value.alt_text,
        'media_source': {'source_type': 'image_url', 'url': value.image_url}}
    with pytest.raises(ValueError, match='already dispatched'):
        await actions.dispatch(value)
    assert len(requests) == 1


def test_pin_scope_mismatch_cannot_build_outgoing_payload():
    account = {'provider': 'pinterest', 'scopes_json': '["pins:read"]'}
    with pytest.raises(ValueError, match='access'):
        AccountActions.payload(account, PinterestPinInput(**pin_arguments()))
