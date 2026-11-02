"""Social source regressions. No test contacts a real platform or persistence."""

import json
import uuid

import httpx
import pytest

from services.agent_api.app.account_actions import AccountActions, AccountInput, LinkedInPostInput
from services.agent_api.app.knowledge_library import ArtifactId, ExportArtifact, KnowledgeLibrary
from services.agent_api.app.migrations import initialize_database
from services.agent_api.app.social_content import PLATFORMS, CampaignInput, register_social_content
from services.agent_api.app.tools import ToolRegistry


@pytest.mark.anyio
async def test_campaign_stores_exact_variants_as_a_scoped_exportable_draft(tmp_path):
    database = tmp_path / 'temporary.sqlite'
    initialize_database(database)
    root = tmp_path / 'project'
    root.mkdir()
    library = KnowledgeLibrary(root, database, 'A')
    registry = ToolRegistry(root)
    register_social_content(registry, database, 'A')
    value = CampaignInput(title='Launch drafts', variants=[{'platform': platform, 'text': f'Exact draft for {platform}'} for platform in PLATFORMS])
    result = await registry.get('social_prepare').handler(value)
    stored = library.artifact(result['artifact_id'])
    campaign = json.loads(stored['content'])
    assert len(campaign['variants']) == 10 and campaign['publication_authorized'] is False
    assert campaign['variants'][0]['text'] == 'Exact draft for facebook'
    assert stored['kind'] == 'content'
    exported = library.export_artifact(ExportArtifact(artifact_id=result['artifact_id'], source_sha256=stored['sha256'], format='json'))
    assert exported['filename'].endswith('.json')
    assert exported['media_type'].startswith('application/json')
    with pytest.raises(KeyError):
        KnowledgeLibrary(root, database, 'B').artifact(ArtifactId(artifact_id=result['artifact_id']).artifact_id)
    catalog = await registry.get('social_catalog').handler(None)
    assert all(item['publishing'] != 'available' for item in catalog['platforms'])
    assert all(item['draft_and_export'] for item in catalog['platforms'])
    assert list(root.iterdir()) == []


def test_campaign_rejects_duplicate_platforms_and_oversized_combined_content():
    with pytest.raises(ValueError, match='one exact draft'):
        CampaignInput(title='Duplicate', variants=[{'platform': 'x', 'text': 'One'}, {'platform': 'x', 'text': 'Two'}])
    with pytest.raises(ValueError, match='combined'):
        CampaignInput(title='Oversized', variants=[{'platform': platform, 'text': 'a' * 12000} for platform in PLATFORMS])


@pytest.mark.anyio
async def test_linkedin_transport_uses_exact_author_public_text_and_version_then_prevents_reuse(tmp_path):
    database = tmp_path / 'temporary.sqlite'
    initialize_database(database)
    class Vault:
        def resolve(self, reference):
            return 'fixture-member-access'
    requests = []
    def transport(request):
        requests.append(request)
        return httpx.Response(201)
    def factory(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return httpx.AsyncClient(transport=httpx.MockTransport(transport), **kwargs)
    actions = AccountActions(database, Vault(), client_factory=factory)
    account = actions.connect(AccountInput(provider='linkedin', label='Fixture member', secret_reference='vault:' + 'a' * 32,
        scopes=['w_member_social'], transmission_consent=True))
    value = LinkedInPostInput(operation_id=uuid.uuid4().hex, account_id=account, account_revision=1,
        author='urn:li:person:fixture', body='Exact approved public text', publish_publicly=True)
    result = await actions.dispatch(value)
    assert result['status'] == 'accepted' and len(requests) == 1
    request = requests[0]
    assert str(request.url) == 'https://api.linkedin.com/rest/posts'
    assert request.headers['LinkedIn-Version'] == '202605'
    assert request.headers['X-Restli-Protocol-Version'] == '2.0.0'
    assert json.loads(request.content) == {'author': value.author, 'commentary': value.body, 'visibility': 'PUBLIC',
        'distribution': {'feedDistribution': 'MAIN_FEED', 'targetEntities': [], 'thirdPartyDistributionChannels': []},
        'lifecycleState': 'PUBLISHED', 'isReshareDisabledByAuthor': True}
    with pytest.raises(ValueError, match='already dispatched'):
        await actions.dispatch(value)
    actions.revoke(account)
    with pytest.raises(ValueError, match='revoked'):
        await actions.dispatch(value.model_copy(update={'operation_id': uuid.uuid4().hex}))
    assert len(requests) == 1


def test_linkedin_requires_public_consent_and_rejects_organization_author():
    arguments = dict(operation_id='a' * 32, account_id='b' * 32, account_revision=1,
        author='urn:li:person:fixture', body='Public text')
    with pytest.raises(ValueError):
        LinkedInPostInput(**arguments, publish_publicly=False)
    with pytest.raises(ValueError):
        LinkedInPostInput(**{**arguments, 'author': 'urn:li:organization:123'}, publish_publicly=True)


def test_linkedin_api_waits_for_exact_approval_before_real_transport_invocation():
    from fastapi.testclient import TestClient

    from services.agent_api.app.main import app

    requests = []
    class Vault:
        def resolve(self, reference):
            return 'fixture-member-access'
    def transport(request):
        requests.append(request)
        return httpx.Response(201)
    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(transport), **kwargs)
    # The repository's autouse test fixture routes this app to temporary data.
    with TestClient(app) as client:
        app.state.account_actions.vault = Vault()
        app.state.account_actions.client_factory = factory
        connected = client.post('/api/v1/workspace/accounts', json={'provider': 'linkedin', 'label': 'Fixture member',
            'secret_reference': 'vault:' + 'a' * 32, 'scopes': ['w_member_social'], 'transmission_consent': True})
        assert connected.status_code == 201
        arguments = {'operation_id': uuid.uuid4().hex, 'account_id': connected.json()['account_id'], 'account_revision': 1,
            'author': 'urn:li:person:fixture', 'body': 'Exact publicly approved text', 'publish_publicly': True}
        proposal = client.post('/api/v1/workspace/accounts/execute', json={'tool': 'account_linkedin_post', 'arguments': arguments}).json()
        assert proposal['status'] == 'approval_required' and not requests
        assert proposal['approval']['preview']['exact_payload']['body'] == arguments['body']
        assert 'fixture-member-access' not in json.dumps(proposal)
        grant = proposal['approval']['request_id']
        assert client.post(f'/api/v1/approvals/{grant}/approve').status_code == 200
        result = client.post(f'/api/v1/tools/execute/{grant}/continue').json()
        assert result['status'] == 'success' and len(requests) == 1
        assert json.loads(requests[0].content)['commentary'] == arguments['body']
