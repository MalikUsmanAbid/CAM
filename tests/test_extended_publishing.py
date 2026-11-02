"""Isolated regression source; injected HTTP only, no live publication."""

import hashlib
import json
import uuid

import httpx
import pytest

from services.agent_api.app.account_actions import AccountActions, AccountInput
from services.agent_api.app.extended_publishing import (
    EXTENDED_SCOPES,
    ExtendedPublishing,
    FacebookPost,
    InstagramPost,
    TikTokPost,
    XBudget,
    XPost,
    YouTubeUpload,
)
from services.agent_api.app.migrations import initialize_database


def setup(tmp_path, provider, respond):
    root = tmp_path / 'workspace'
    root.mkdir()
    database = tmp_path / 'temporary.sqlite'
    initialize_database(database)
    class Vault:
        def resolve(self, _): return 'synthetic-fixture-token'
    def factory(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return httpx.AsyncClient(transport=httpx.MockTransport(respond), **kwargs)
    actions = AccountActions(database, Vault(), client_factory=factory)
    account = actions.connect(AccountInput(provider=provider, label='Fixture', secret_reference='vault:' + 'a' * 32,
        scopes=sorted(EXTENDED_SCOPES[provider]), transmission_consent=True))
    return root, actions, ExtendedPublishing(actions, root), {'account_id': account, 'account_revision': 1, 'operation_id': uuid.uuid4().hex}


@pytest.mark.anyio
async def test_facebook_sends_exact_fixed_versioned_page_payload_once(tmp_path):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'id': '123_456'})
    _, _, publishing, common = setup(tmp_path, 'facebook', respond)
    value = FacebookPost(**common, page_id='123', body='Exact message', publish_publicly=True)
    assert (await publishing.dispatch(value))['receipt']['post_id'] == '123_456'
    assert str(calls[0].url) == 'https://graph.facebook.com/v26.0/123/feed'
    assert calls[0].content == b'message=Exact+message&published=true'
    with pytest.raises(ValueError, match='already claimed'):
        await publishing.dispatch(value)
    assert len(calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize('revoke', [False, True])
async def test_instagram_exact_container_followed_by_publish_and_revocation_boundary(tmp_path, revoke):
    calls = []
    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            if revoke:
                actions.revoke(common['account_id'])
            return httpx.Response(200, json={'id': '42'})
        if request.method == 'GET':
            return httpx.Response(200, json={'status_code': 'FINISHED'})
        return httpx.Response(200, json={'id': '43'})
    _, actions, publishing, common = setup(tmp_path, 'instagram', respond)
    value = InstagramPost(**common, instagram_user_id='123', caption='Exact caption', alt_text='Exact alt',
                          image_url='https://images.example/owned.jpg', publish_publicly=True)
    if revoke:
        with pytest.raises(ValueError, match='revoked'):
            await publishing.dispatch(value)
        assert len(calls) == 1
        with actions.records.connect() as db:
            row = db.execute('SELECT status,receipt_json FROM account_actions WHERE operation_id=?', (value.operation_id,)).fetchone()
        assert row['status'] == 'outcome_unknown'
        assert json.loads(row['receipt_json'])['container_id'] == '42'
    else:
        assert (await publishing.dispatch(value))['receipt']['post_id'] == '43'
        assert [str(call.url) for call in calls] == ['https://graph.facebook.com/v26.0/123/media', 'https://graph.facebook.com/v26.0/42?fields=status_code', 'https://graph.facebook.com/v26.0/123/media_publish']
        assert calls[2].content == b'creation_id=42'


@pytest.mark.anyio
async def test_x_default_zero_and_conservative_daily_reservation(tmp_path):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(201, json={'data': {'id': '123'}})
    _, actions, publishing, common = setup(tmp_path, 'x', respond)
    value = XPost(**common, body='Exact paid post', budget_revision=1, maximum_micro_usd=2, publish_publicly=True)
    with pytest.raises(ValueError, match='disabled'):
        await publishing.dispatch(value)
    assert calls == []
    publishing.budget(XBudget(account_id=value.account_id, account_revision=1, expected_revision=0,
                             enabled=True, unit_micro_usd=2, daily_micro_usd=2, accept_charges=True))
    accepted = value.model_copy(update={'operation_id': uuid.uuid4().hex})
    assert (await publishing.dispatch(accepted))['status'] == 'accepted'
    with pytest.raises(ValueError, match='Daily'):
        await publishing.dispatch(value.model_copy(update={'operation_id': uuid.uuid4().hex}))
    assert len(calls) == 1
    assert str(calls[0].url) == 'https://api.x.com/2/tweets'
    assert json.loads(calls[0].content) == {'text': 'Exact paid post'}
    with actions.records.connect() as db:
        assert db.execute('SELECT SUM(micro_usd) FROM publishing_reservations').fetchone()[0] == 2


@pytest.mark.anyio
async def test_youtube_upload_binds_video_bytes_metadata_and_visibility(tmp_path):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'id': 'fixture_video'})
    root, _, publishing, common = setup(tmp_path, 'youtube', respond)
    content = b'INERT VIDEO FIXTURE; not codec verification'
    (root / 'video.mp4').write_bytes(content)
    value = YouTubeUpload(**common, path='video.mp4', media_sha256=hashlib.sha256(content).hexdigest(),
        title='Exact video title', description='Exact description', category_id='22', privacy='private', made_for_kids=False, confirm_upload=True)
    assert (await publishing.dispatch(value))['receipt']['media_sha256'] == value.media_sha256
    assert len(calls) == 1 and calls[0].url.host == 'www.googleapis.com'
    assert calls[0].url.params['notifySubscribers'] == 'false'
    assert calls[0].headers['content-type'].startswith('multipart/related; boundary=')
    assert content in calls[0].content and b'"privacyStatus": "private"' in calls[0].content
    (root / 'video.mp4').write_bytes(b'Later user data')
    with pytest.raises(ValueError, match='Video changed'):
        await publishing.dispatch(value.model_copy(update={'operation_id': uuid.uuid4().hex}))
    assert len(calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize('changed', [False, True])
async def test_tiktok_rechecks_creator_contract_and_semantic_error_not_http_count(tmp_path, changed):
    calls = []
    creator = {'creator_username': 'fixture', 'creator_nickname': 'Fixture', 'privacy_level_options': ['SELF_ONLY'],
               'comment_disabled': True, 'duet_disabled': True, 'stitch_disabled': True, 'max_video_post_duration_sec': 60}
    _, digest = ExtendedPublishing.creator({'data': creator, 'error': {'code': 'ok'}})
    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            data = {**creator, 'max_video_post_duration_sec': 30} if changed else creator
            return httpx.Response(200, json={'data': data, 'error': {'code': 'ok'}})
        return httpx.Response(200, json={'data': {'publish_id': 'v_pub_file~v2-1.123'}, 'error': {'code': 'ok'}})
    _, _, publishing, common = setup(tmp_path, 'tiktok', respond)
    value = TikTokPost(**common, creator_contract_sha256=digest, body='Exact caption', video_url='https://owned.example/video.mp4',
        duration_seconds=10, privacy='SELF_ONLY', disable_comment=True, disable_duet=True, disable_stitch=True,
        ai_generated=False, confirm_url_ownership=True, confirm_publish=True)
    if changed:
        with pytest.raises(ValueError, match='Creator settings changed'):
            await publishing.dispatch(value)
        assert len(calls) == 1
    else:
        assert (await publishing.dispatch(value))['receipt']['publish_id'] == 'v_pub_file~v2-1.123'
        assert len(calls) == 2
        assert json.loads(calls[1].content)['source_info'] == {'source': 'PULL_FROM_URL', 'video_url': value.video_url}
    with pytest.raises(ValueError, match='refused'):
        ExtendedPublishing.creator({'data': creator, 'error': {'code': 'scope_not_authorized'}})
