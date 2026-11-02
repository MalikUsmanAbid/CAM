"""Bounded account publishing workflows. Provider acceptance is not delivery.

No login, dependency installation, auto-retry or scheduled publishing. Exact
contracts are dispatched only by ToolExecutor. Media stays immutable in memory
after its approved hash is checked; remote URL fetches remain provider-owned.
"""

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Literal

import httpx
from pydantic import Field, field_validator

from services.agent_api.app.account_actions import PinterestPinInput
from services.agent_api.app.action_policy import ActionCategory
from services.agent_api.app.memory import is_sensitive_content
from services.agent_api.app.permissions import RiskLevel, canonical_arguments
from services.agent_api.app.project_files import ProjectFiles
from services.agent_api.app.session_security import execution_dispatch_authorized
from services.agent_api.app.tools import Tool, ToolExecutionOutcome, ToolInput
from services.agent_api.app.workspace_records import now

GRAPH = 'https://graph.facebook.com/v26.0'
EXTENDED_SCOPES = {
    'facebook': {'pages_manage_posts', 'pages_read_engagement'},
    'instagram': {'instagram_basic', 'instagram_content_publish', 'pages_read_engagement'},
    'x': {'tweet.read', 'tweet.write', 'users.read'},
    'youtube': {'https://www.googleapis.com/auth/youtube.upload'},
    'tiktok': {'video.publish'},
}


class AccountContract(ToolInput):
    operation_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    account_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    account_revision: int = Field(ge=1, strict=True)


class FacebookPost(AccountContract):
    page_id: str = Field(pattern=r'^[0-9]{1,30}$')
    body: str = Field(min_length=1, max_length=12000)
    publish_publicly: Literal[True]

    @field_validator('body')
    @classmethod
    def safe_text(cls, value):
        if not value.strip() or is_sensitive_content(value) or '\x00' in value:
            raise ValueError('Post is blank or contains excluded content.')
        return value


class InstagramPost(AccountContract):
    instagram_user_id: str = Field(pattern=r'^[0-9]{1,30}$')
    caption: str = Field(default='', max_length=2200)
    image_url: str = Field(min_length=1, max_length=2000)
    alt_text: str = Field(min_length=1, max_length=500)
    publish_publicly: Literal[True]

    @field_validator('image_url')
    @classmethod
    def safe_url(cls, value):
        return PinterestPinInput.exact_image_url(value)

    @field_validator('caption', 'alt_text')
    @classmethod
    def safe_text(cls, value):
        if is_sensitive_content(value) or '\x00' in value:
            raise ValueError('Post contains excluded content.')
        return value


class XPost(AccountContract):
    body: str = Field(min_length=1, max_length=280)
    budget_revision: int = Field(ge=1, strict=True)
    maximum_micro_usd: int = Field(ge=1, le=100_000_000, strict=True)
    publish_publicly: Literal[True]

    @field_validator('body')
    @classmethod
    def safe_text(cls, value):
        return FacebookPost.safe_text(value)


class XBudget(ToolInput):
    account_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    account_revision: int = Field(ge=1, strict=True)
    expected_revision: int = Field(ge=0, strict=True)
    enabled: bool
    unit_micro_usd: int = Field(ge=1, le=100_000_000, strict=True)
    daily_micro_usd: int = Field(ge=0, le=1_000_000_000, strict=True)
    accept_charges: Literal[True]


class PublishingStatus(ToolInput):
    account_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    account_revision: int = Field(ge=1, strict=True)


class MediaInspect(ToolInput):
    path: str = Field(min_length=1, max_length=500)


class YouTubeUpload(AccountContract, MediaInspect):
    media_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=5000)
    category_id: str = Field(pattern=r'^[0-9]{1,3}$')
    privacy: Literal['private', 'unlisted', 'public']
    made_for_kids: bool
    notify_subscribers: bool = False
    confirm_upload: Literal[True]

    @field_validator('title', 'description')
    @classmethod
    def safe_text(cls, value):
        if is_sensitive_content(value) or '\x00' in value:
            raise ValueError('Metadata contains excluded content.')
        return value


class TikTokCreator(AccountContract):
    pass


class TikTokPost(AccountContract):
    creator_contract_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    body: str = Field(default='', max_length=2200)
    video_url: str = Field(min_length=1, max_length=2000)
    duration_seconds: int = Field(ge=1, le=3600, strict=True)
    privacy: Literal['PUBLIC_TO_EVERYONE', 'MUTUAL_FOLLOW_FRIENDS', 'FOLLOWER_OF_CREATOR', 'SELF_ONLY']
    disable_comment: bool
    disable_duet: bool
    disable_stitch: bool
    ai_generated: bool
    confirm_url_ownership: Literal[True]
    confirm_publish: Literal[True]

    @field_validator('video_url')
    @classmethod
    def safe_url(cls, value):
        return PinterestPinInput.exact_image_url(value)

    @field_validator('body')
    @classmethod
    def safe_text(cls, value):
        if is_sensitive_content(value) or '\x00' in value:
            raise ValueError('Post contains excluded content.')
        return value


class ExtendedPublishing:
    def __init__(self, actions, root):
        self.actions = actions
        self.files = ProjectFiles(root)

    def media(self, value):
        target = self.files._safe(value.path)
        if target.suffix.lower() not in {'.mp4', '.webm', '.mov'}:
            raise ValueError('Only explicitly selected MP4/WebM/MOV files are supported.')
        with self.files._scan(target.parent):
            fd = os.open(target, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as source:
                self.files.verify_opened(source.fileno(), target)
                if os.fstat(source.fileno()).st_size > 32 * 1024**2:
                    raise ValueError('Video exceeds the 32 MiB bounded upload limit.')
                content = source.read(32 * 1024**2 + 1)
                if not content or len(content) > 32 * 1024**2:
                    raise ValueError('Video is empty, growing or oversized.')
        digest = hashlib.sha256(content).hexdigest()
        if isinstance(value, YouTubeUpload) and digest != value.media_sha256:
            raise ValueError('Video changed; inspect and request fresh approval.')
        return content, digest, {'path': value.path, 'bytes': len(content), 'sha256': digest,
                                'boundary': 'Hash/extension inspection only; no codec, duration or content verification.'}

    def budget(self, value):
        account, _ = self.actions.account(value.account_id, value.account_revision)
        if account['provider'] != 'x':
            raise ValueError('Paid post policy is scoped to an X account.')
        execution_dispatch_authorized.get()()
        with self.actions.records.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM publishing_budgets WHERE account_id=?', (value.account_id,)).fetchone()
            if (row[0] if row else 0) != value.expected_revision:
                raise ValueError('Budget policy changed; inspect and approve a fresh revision.')
            revision = value.expected_revision + 1
            db.execute('INSERT INTO publishing_budgets VALUES (?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET revision=excluded.revision,enabled=excluded.enabled,unit_micro_usd=excluded.unit_micro_usd,daily_micro_usd=excluded.daily_micro_usd',
                       (value.account_id, revision, value.enabled, value.unit_micro_usd, value.daily_micro_usd))
        return {'account_id': value.account_id, 'budget_revision': revision,
                'warning': 'Configured conservative reservation, not verified provider pricing or an invoice guarantee.'}

    def reserve(self, value):
        with self.actions.records.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM publishing_budgets WHERE account_id=?', (value.account_id,)).fetchone()
            if row is None or not row['enabled'] or row['revision'] != value.budget_revision or row['unit_micro_usd'] > value.maximum_micro_usd:
                raise ValueError('Paid publishing is disabled, pricing changed or approval allowance is insufficient.')
            day = datetime.now(UTC).date().isoformat()
            used = db.execute('SELECT COALESCE(SUM(micro_usd),0) FROM publishing_reservations WHERE account_id=? AND day=?', (value.account_id, day)).fetchone()[0]
            if used + row['unit_micro_usd'] > row['daily_micro_usd']:
                raise ValueError('Daily paid publishing allowance exceeded.')
            db.execute('INSERT INTO publishing_reservations VALUES (?,?,?,?)', (value.operation_id, value.account_id, day, row['unit_micro_usd']))

    def authorize(self, value, provider, secret):
        execution_dispatch_authorized.get()()
        current, credential = self.actions.account(value.account_id, value.account_revision)
        if credential != secret or current['provider'] != provider or not EXTENDED_SCOPES[provider].issubset(json.loads(current['scopes_json'])):
            raise ValueError('Credential, account or required scopes changed.')
        if isinstance(value, XPost):
            with self.actions.records.connect() as db:
                row = db.execute('SELECT * FROM publishing_budgets WHERE account_id=?', (value.account_id,)).fetchone()
            if row is None or not row['enabled']:
                raise ValueError('Paid publishing is disabled.')
            if row['revision'] != value.budget_revision:
                raise ValueError('Paid publishing authorization changed.')

    async def request(self, client, value, provider, secret, url, *, method='POST', **body):
        self.authorize(value, provider, secret)
        async with client.stream(method, url, headers={'Authorization': 'Bearer ' + secret, 'Accept': 'application/json', 'Accept-Encoding': 'identity', **body.pop('extra_headers', {})}, **body) as response:
            if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                raise ValueError('Compressed provider response is unsupported.')
            raw = bytearray()
            if response.is_stream_consumed:
                if len(response.content) > 64000:
                    raise ValueError('Provider response exceeds its bound; reconcile manually.')
                raw.extend(response.content)
            else:
                async for block in response.aiter_raw():
                    if len(raw) + len(block) > 64000:
                        raise ValueError('Provider response exceeds its bound; reconcile manually.')
                    raw.extend(block)
            if response.status_code not in {200, 201}:
                raise ToolExecutionOutcome({'status': 'rejected' if response.status_code in {400, 401, 403, 404, 422} else 'outcome_unknown', 'http_status': response.status_code})
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeError):
                raise ValueError('Provider response is malformed; reconcile manually.') from None
            if not isinstance(result, dict) or 'error' in result and provider != 'tiktok':
                raise ValueError('Provider refused or returned unexpected data; reconcile manually.')
            return result

    @staticmethod
    def identifier(result, key='id'):
        if not isinstance(result, dict):
            raise ValueError('Provider receipt shape is invalid; reconcile manually.')
        value = result.get(key)
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.~-]{1,128}', value):
            raise ValueError('Provider receipt identity is missing; reconcile manually.')
        return value

    @staticmethod
    def creator(result):
        if not isinstance(result.get('error'), dict) or result['error'].get('code') != 'ok' or not isinstance(result.get('data'), dict):
            raise ValueError('TikTok creator information was refused.')
        data = result['data']
        selected = {key: data.get(key) for key in ('creator_username', 'creator_nickname', 'privacy_level_options', 'comment_disabled', 'duet_disabled', 'stitch_disabled', 'max_video_post_duration_sec')}
        options = selected['privacy_level_options']
        if (not isinstance(options, list) or not 1 <= len(options) <= 4 or any(not isinstance(item, str) or item not in {'PUBLIC_TO_EVERYONE', 'MUTUAL_FOLLOW_FRIENDS', 'FOLLOWER_OF_CREATOR', 'SELF_ONLY'} for item in options)
                or any(type(selected[key]) is not bool for key in ('comment_disabled', 'duet_disabled', 'stitch_disabled'))
                or type(selected['max_video_post_duration_sec']) is not int):
            raise ValueError('TikTok creator information is unsupported.')
        if len(set(options)) != len(options) or not 1 <= selected['max_video_post_duration_sec'] <= 86400:
            raise ValueError('TikTok creator limits are unsupported.')
        for key in ('creator_username', 'creator_nickname'):
            if not isinstance(selected[key], str) or len(selected[key]) > 100 or is_sensitive_content(selected[key]):
                raise ValueError('Creator identity is invalid.')
        checksum = hashlib.sha256(canonical_arguments(selected).encode()).hexdigest()
        return selected, checksum

    async def dispatch(self, value):
        providers = {FacebookPost: 'facebook', InstagramPost: 'instagram', XPost: 'x', YouTubeUpload: 'youtube', TikTokCreator: 'tiktok', TikTokPost: 'tiktok'}
        provider = providers[type(value)]
        async with self.actions.gate:
            account, secret = self.actions.account(value.account_id, value.account_revision)
            if account['provider'] != provider:
                raise ValueError('Account provider does not match this action.')
            self.authorize(value, provider, secret)
            media = self.media(value)[0] if isinstance(value, YouTubeUpload) else None
            with self.actions.records.connect() as db:
                try:
                    db.execute("INSERT INTO account_actions VALUES (?,?,?,'dispatching',NULL,?)", (value.operation_id, value.account_id, hashlib.sha256(canonical_arguments(value.model_dump()).encode()).hexdigest(), now()))
                except sqlite3.IntegrityError:
                    raise ValueError('Operation already claimed; reconcile before requesting another action.') from None
            status, receipt = 'outcome_unknown', {'provider': provider, 'stage': 'claimed'}
            try:
                if isinstance(value, XPost):
                    self.reserve(value)
                async with self.actions.client_factory(trust_env=False, follow_redirects=False, timeout=60) as client:
                    async def send(url, **body):
                        return await self.request(client, value, provider, secret, url, **body)
                    if isinstance(value, FacebookPost):
                        result = await send(f'{GRAPH}/{value.page_id}/feed', data={'message': value.body, 'published': 'true'})
                        receipt['post_id'] = self.identifier(result)
                    elif isinstance(value, InstagramPost):
                        container = await send(f'{GRAPH}/{value.instagram_user_id}/media', data={'image_url': value.image_url, 'caption': value.caption, 'alt_text': value.alt_text})
                        container_id = self.identifier(container)
                        if not container_id.isdigit():
                            raise ValueError('Unexpected Instagram container identity.')
                        receipt.update(stage='container_created', container_id=container_id)
                        for attempt in range(3):
                            state = await send(f'{GRAPH}/{container_id}?fields=status_code', method='GET')
                            if state.get('status_code') == 'FINISHED':
                                break
                            if state.get('status_code') != 'IN_PROGRESS' or attempt == 2:
                                raise ValueError('Instagram container is not ready; inspect retained receipt before any new action.')
                            await asyncio.sleep(0.25)
                        result = await send(f'{GRAPH}/{value.instagram_user_id}/media_publish', data={'creation_id': container_id})
                        receipt['post_id'] = self.identifier(result)
                    elif isinstance(value, XPost):
                        result = await send('https://api.x.com/2/tweets', json={'text': value.body})
                        receipt['post_id'] = self.identifier(result.get('data') or {})
                    elif isinstance(value, YouTubeUpload):
                        metadata = {'snippet': {'title': value.title, 'description': value.description, 'categoryId': value.category_id},
                                    'status': {'privacyStatus': value.privacy, 'selfDeclaredMadeForKids': value.made_for_kids}}
                        boundary = 'jarvis' + uuid.uuid4().hex
                        mime = {'.mp4': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm'}[self.files._safe(value.path).suffix.lower()]
                        body = (f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'.encode() + json.dumps(metadata).encode()
                                + f'\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n'.encode() + media + f'\r\n--{boundary}--\r\n'.encode())
                        url = 'https://www.googleapis.com/upload/youtube/v3/videos?uploadType=multipart&part=snippet,status&notifySubscribers=' + ('true' if value.notify_subscribers else 'false')
                        result = await send(url, content=body, extra_headers={'Content-Type': 'multipart/related; boundary=' + boundary})
                        receipt.update(post_id=self.identifier(result), media_sha256=value.media_sha256)
                    else:
                        result = await send('https://open.tiktokapis.com/v2/post/publish/creator_info/query/', json={})
                        creator, checksum = self.creator(result)
                        if isinstance(value, TikTokCreator):
                            receipt.update(creator=creator, creator_contract_sha256=checksum)
                        else:
                            if (checksum != value.creator_contract_sha256 or value.privacy not in creator['privacy_level_options']
                                    or value.duration_seconds > creator['max_video_post_duration_sec']
                                    or any(creator[remote] and not getattr(value, local) for remote, local in [('comment_disabled', 'disable_comment'), ('duet_disabled', 'disable_duet'), ('stitch_disabled', 'disable_stitch')])):
                                raise ValueError('Creator settings changed or selected interactions/duration are unsupported; reload options and approve again.')
                            result = await send('https://open.tiktokapis.com/v2/post/publish/video/init/', json={
                                'post_info': {'title': value.body, 'privacy_level': value.privacy, 'disable_comment': value.disable_comment,
                                              'disable_duet': value.disable_duet, 'disable_stitch': value.disable_stitch, 'is_aigc': value.ai_generated},
                                'source_info': {'source': 'PULL_FROM_URL', 'video_url': value.video_url}})
                            if not isinstance(result.get('error'), dict) or result['error'].get('code') != 'ok':
                                raise ValueError('TikTok did not accept this export; reconcile before retry.')
                            receipt['publish_id'] = self.identifier(result.get('data') or {}, 'publish_id')
                status = 'accepted'
                receipt['stage'] = 'provider_accepted'
            except ToolExecutionOutcome as error:
                status = error.result.get('status', 'outcome_unknown')
                raise
            except httpx.HTTPError:
                raise ToolExecutionOutcome({'status': 'outcome_unknown', 'operation_id': value.operation_id}) from None
            finally:
                with self.actions.records.connect() as db:
                    db.execute('UPDATE account_actions SET status=?,receipt_json=? WHERE operation_id=?', (status, json.dumps(receipt), value.operation_id))
            return {'status': status, 'operation_id': value.operation_id, 'receipt': receipt,
                    'boundary': 'Provider acceptance, not completed processing, visibility or delivery verification. No automatic retries.'}


def register_extended_publishing(registry, actions):
    publishing = ExtendedPublishing(actions, registry.workspace_root)
    async def inspect(value):
        async with actions.gate:
            return publishing.media(value)[2]
    async def configure(value):
        return publishing.budget(value)
    async def status(value):
        with actions.records.connect() as db:
            # Reading receipts must remain possible after credential revocation;
            # it grants no authority to send and never resolves the old secret.
            if db.execute('SELECT 1 FROM connected_accounts WHERE account_id=? AND revision=?', (value.account_id, value.account_revision)).fetchone() is None:
                raise ValueError('Account revision changed; refresh the connection list.')
            budget = db.execute('SELECT * FROM publishing_budgets WHERE account_id=?', (value.account_id,)).fetchone()
            rows = db.execute('SELECT operation_id,status,receipt_json,created_at FROM account_actions WHERE account_id=? ORDER BY created_at DESC LIMIT 20', (value.account_id,)).fetchall()
        return {'budget': dict(budget) if budget else {'revision': 0, 'enabled': False},
                'operations': [{'operation_id': row['operation_id'], 'status': row['status'], 'receipt': json.loads(row['receipt_json']) if row['receipt_json'] else None, 'created_at': row['created_at']} for row in rows]}
    for name, schema in [('account_facebook_post', FacebookPost), ('account_instagram_post', InstagramPost),
                         ('account_x_post', XPost), ('account_youtube_upload', YouTubeUpload),
                         ('account_tiktok_creator', TikTokCreator), ('account_tiktok_post', TikTokPost)]:
        registry.register(Tool(name, 'Perform only the exact approved account workflow; uncertain requests cannot replay.', schema,
            RiskLevel.HIGH, 125, publishing.dispatch, category=ActionCategory.READ if schema is TikTokCreator else ActionCategory.PUBLISH,
            resolve_destination=(lambda args: publishing.files._safe(args['path'])) if schema is YouTubeUpload else None,
            preview_details=lambda args: {'exact_payload': args, 'warning': 'Consequential provider request. URLs are fetched by the provider; external outcomes cannot always be cancelled.'}))
    registry.register(Tool('account_media_inspect', 'Inspect selected bounded video hash/size; never uploads.', MediaInspect, RiskLevel.HIGH, 10, inspect,
                           category=ActionCategory.READ, resolve_destination=lambda args: publishing.files._safe(args['path'])))
    registry.register(Tool('account_x_budget', 'Explicitly configure conservative paid X post allowance; default is disabled.', XBudget, RiskLevel.HIGH, 10, configure,
                           category=ActionCategory.FINANCIAL))
    registry.register(Tool('account_publishing_status', 'Inspect this account\'s durable publishing receipts and configured budget.', PublishingStatus, RiskLevel.LOW, 10, status,
                           category=ActionCategory.READ))


def refresh_extended_publishing(registry, actions):
    rows = actions.accounts()
    for tool, provider in [('account_facebook_post', 'facebook'), ('account_instagram_post', 'instagram'), ('account_x_post', 'x'),
                           ('account_youtube_upload', 'youtube'), ('account_tiktok_creator', 'tiktok'), ('account_tiktok_post', 'tiktok'), ('account_x_budget', 'x')]:
        registry.set_enabled(tool, any(row['provider'] == provider and row['status'] == 'connected' and EXTENDED_SCOPES[provider].issubset(json.loads(row['scopes_json'])) for row in rows))
