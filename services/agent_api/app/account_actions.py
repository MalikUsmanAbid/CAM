"""Exact-payload account adapters. Login/refresh are separate; no automatic retries.

Users explicitly connect an already obtained backend-vault token. OAuth login
is managed separately; these methods never invent provider consent or identity.
"""

import asyncio
import base64
import hashlib
import ipaddress
import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.policy import SMTP
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.agent_api.app.action_policy import ActionCategory
from services.agent_api.app.memory import is_sensitive_content
from services.agent_api.app.network_security import NetworkDestinationPolicy, PinnedHTTPProxy
from services.agent_api.app.permissions import RiskLevel, canonical_arguments
from services.agent_api.app.session_security import execution_dispatch_authorized
from services.agent_api.app.tools import Tool, ToolExecutionOutcome, ToolInput, WorkspaceGuard
from services.agent_api.app.workspace_records import WorkspaceRecords, now


class AccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider: Literal["github", "google", "microsoft", "linkedin", "mastodon", "bluesky", "threads", "pinterest", "facebook", "instagram", "x", "youtube", "tiktok"]
    label: str = Field(min_length=1, max_length=100)
    secret_reference: str = Field(pattern=r"^vault:[a-f0-9]{32}$")
    scopes: list[str] = Field(min_length=1, max_length=5)
    transmission_consent: bool
    service_base: str | None = Field(default=None, max_length=300)

    @model_validator(mode='after')
    def bound_instance(self):
        if self.provider not in {'mastodon', 'bluesky'}:
            if self.service_base is not None:
                raise ValueError('Custom service destinations are unsupported for this provider.')
            return self
        parsed = urlsplit(self.service_base or '')
        host = (parsed.hostname or '').encode('idna').decode().lower()
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None, 443) or parsed.path not in ('', '/') or not re.fullmatch(r'[a-z0-9.-]+\.[a-z]{2,63}', host):
            raise ValueError('This provider needs an exact HTTPS service origin on port 443, without credentials, path, query or fragment.')
        self.service_base = 'https://' + host
        return self

    @field_validator("label")
    @classmethod
    def safe_label(cls, value):
        if is_sensitive_content(value):
            raise ValueError("Account labels cannot contain potential credentials.")
        return value


class SendInput(ToolInput):
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    account_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    account_revision: int = Field(ge=1)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=12000)
    recipients: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}$", max_length=254)]] = Field(min_length=1, max_length=20)

    @field_validator("subject", "body")
    @classmethod
    def safe_text(cls, value):
        if is_sensitive_content(value) or "\x00" in value:
            raise ValueError("Outbound content contains potential credentials or invalid characters.")
        return value

    @field_validator("subject")
    @classmethod
    def single_line(cls, value):
        if "\r" in value or "\n" in value:
            raise ValueError("Subjects must occupy one header line.")
        return value


class IssueInput(ToolInput):
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    account_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    account_revision: int = Field(ge=1)
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=12000)

    @field_validator("title", "body")
    @classmethod
    def safe_text(cls, value):
        if is_sensitive_content(value):
            raise ValueError("Outbound content contains potential credentials.")
        return value


class CalendarInput(ToolInput):
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    account_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    account_revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=12000)
    start: str = Field(min_length=1, max_length=50)
    end: str = Field(min_length=1, max_length=50)
    attendees: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}$", max_length=254)]] = Field(default_factory=list, max_length=20)
    send_invitations: bool = False

    @field_validator("title", "body")
    @classmethod
    def safe_text(cls, value):
        if is_sensitive_content(value) or "\x00" in value:
            raise ValueError("Event content contains potential credentials or invalid characters.")
        return value

    @field_validator("start", "end")
    @classmethod
    def timestamp(cls, value):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Calendar timestamps require explicit timezone offsets.")
        return parsed.astimezone(UTC).isoformat()

    @model_validator(mode="after")
    def interval_and_guests(self):
        duration = datetime.fromisoformat(self.end) - datetime.fromisoformat(self.start)
        if not timedelta(0) < duration <= timedelta(days=7):
            raise ValueError("Event duration must be positive and at most seven days.")
        if self.attendees and not self.send_invitations:
            raise ValueError("Adding guests requires explicit invitation consent.")
        if len(set(address.casefold() for address in self.attendees)) != len(self.attendees):
            raise ValueError("Duplicate attendees are unsupported.")
        return self


class LinkedInPostInput(ToolInput):
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    account_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    account_revision: int = Field(ge=1)
    author: str = Field(pattern=r'^urn:li:person:[A-Za-z0-9_-]{1,100}$')
    body: str = Field(min_length=1, max_length=3000)
    publish_publicly: Literal[True]

    @field_validator('body')
    @classmethod
    def safe_body(cls, value):
        if not value.strip() or is_sensitive_content(value) or any(ord(character) < 32 and character not in '\t\r\n' for character in value):
            raise ValueError('Public post is blank or contains potential credentials or invalid characters.')
        return value


class MastodonPostInput(ToolInput):
    operation_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    account_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    account_revision: int = Field(ge=1)
    service_base: str = Field(min_length=1, max_length=300, pattern=r'^https://[^/?#@]+$')
    body: str = Field(min_length=1, max_length=500)
    visibility: Literal['public', 'unlisted', 'private']
    confirm_visibility: Literal[True]
    sensitive: bool = False
    spoiler_text: str = Field(default='', max_length=500)

    @field_validator('body', 'spoiler_text')
    @classmethod
    def safe_text(cls, value):
        if is_sensitive_content(value) or any(ord(character) < 32 and character not in '\t\r\n' for character in value):
            raise ValueError('Post contains potential credentials or invalid characters.')
        return value

    @model_validator(mode='after')
    def nonblank(self):
        if not self.body.strip():
            raise ValueError('Mastodon post cannot be blank.')
        return self


class BlueskyPostInput(ToolInput):
    operation_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    account_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    account_revision: int = Field(ge=1)
    service_base: str = Field(min_length=1, max_length=300)
    repository_did: str = Field(pattern=r'^did:plc:[a-z2-7]{24}$')
    record_key: str = Field(pattern=r'^[234567abcdefghij][234567abcdefghijklmnopqrstuvwxyz]{12}$')
    created_at: str = Field(min_length=1, max_length=40)
    body: str = Field(min_length=1, max_length=300)
    publish_publicly: Literal[True]

    @field_validator('body')
    @classmethod
    def text_only(cls, value):
        if not value.strip() or is_sensitive_content(value) or any(ord(char) < 32 and char not in '\n\r\t' for char in value):
            raise ValueError('Post is blank or contains excluded content.')
        # Codepoint <=300 is deliberately conservative versus grapheme counting.
        if len(value.encode()) > 3000:
            raise ValueError('Post exceeds the UTF-8 limit.')
        return value

    @field_validator('created_at')
    @classmethod
    def exact_time(cls, value):
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Post timestamp requires an explicit timezone.')
        return parsed.astimezone(UTC).isoformat().replace('+00:00', 'Z')


class ThreadsPostInput(ToolInput):
    operation_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    account_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    account_revision: int = Field(ge=1)
    body: str = Field(min_length=1, max_length=500)
    publish_publicly: Literal[True]

    @field_validator('body')
    @classmethod
    def text_only(cls, value):
        if not value.strip() or is_sensitive_content(value) or any(ord(char) < 32 and char not in '\n\r\t' for char in value):
            raise ValueError('Post is blank or contains excluded content.')
        return value


class PinterestPinInput(ToolInput):
    operation_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    account_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    account_revision: int = Field(ge=1)
    board_id: str = Field(pattern=r'^[0-9]{1,30}$')
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=800)
    alt_text: str = Field(min_length=1, max_length=500)
    image_url: str = Field(min_length=1, max_length=2000)
    publish_to_board: Literal[True]

    @field_validator('title', 'description', 'alt_text')
    @classmethod
    def text_only(cls, value, info):
        if (info.field_name != 'description' and not value.strip()) or is_sensitive_content(value) or any(ord(char) < 32 and char not in '\n\r\t' for char in value):
            raise ValueError('Pin contains excluded content.')
        return value

    @field_validator('image_url')
    @classmethod
    def exact_image_url(cls, value):
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.port not in (None, 443) or not parsed.path or '\\' in value
                or any(ord(char) < 33 for char in value) or is_sensitive_content(value)):
            raise ValueError('Use an exact HTTPS image URL without credentials, query or fragment.')
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            normalized = host.encode('idna').decode().lower()
            if not re.fullmatch(r'[a-z0-9.-]+\.[a-z]{2,63}', normalized) or normalized.endswith(('.local', '.localhost', '.internal', '.invalid', '.test')):
                raise ValueError('Image host must use public DNS syntax.')
        else:
            if not address.is_global:
                raise ValueError('Private/local image addresses are excluded.')
        # No local fetch occurs. Pinterest's own URL fetching/DNS is external to
        # JARVIS and is not described as protected by our local pinned proxy.
        return value


SCOPES = {"github": {"issues:write"}, "google": {"https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/calendar.events.owned"}, "microsoft": {"Mail.Send", "Calendars.ReadWrite"}, "linkedin": {"w_member_social"}, 'mastodon': {'write:statuses'}, 'bluesky': {'com.atproto.access'}, 'threads': {'threads_basic', 'threads_content_publish'}, 'pinterest': {'boards:read', 'boards:write', 'pins:read', 'pins:write'},
          'facebook': {'pages_manage_posts', 'pages_read_engagement'}, 'instagram': {'instagram_basic', 'instagram_content_publish', 'pages_read_engagement'},
          'x': {'tweet.read', 'tweet.write', 'users.read'}, 'youtube': {'https://www.googleapis.com/auth/youtube.upload'}, 'tiktok': {'video.publish'}}


class AccountActions:
    def __init__(self, database, vault, *, client_factory=httpx.AsyncClient, proxy_factory=PinnedHTTPProxy):
        self.records = WorkspaceRecords(database)
        self.vault = vault
        self.client_factory = client_factory
        self.proxy_factory = proxy_factory
        self.gate = asyncio.Semaphore(1)

    def connect(self, value):
        if not value.transmission_consent or not set(value.scopes).issubset(SCOPES[value.provider]):
            raise ValueError("Explicit transmission consent and supported least-privilege scopes are required.")
        if not self.vault.resolve(value.secret_reference):
            raise ValueError("The protected account credential is unavailable.")
        identifier = uuid.uuid4().hex
        with self.records.connect() as db:
            db.execute("INSERT INTO connected_accounts VALUES (?,?,?,?,?,1,'connected',?)", (identifier, value.provider, value.label, value.secret_reference, json.dumps(sorted(set(value.scopes))), now()))
            if value.service_base is not None:
                db.execute('INSERT INTO account_services VALUES (?,?)', (identifier, value.service_base))
        return identifier

    def accounts(self):
        with self.records.connect() as db:
            return [dict(row) for row in db.execute("SELECT a.account_id,a.provider,a.label,a.scopes_json,a.revision,a.status,a.created_at,t.state AS oauth_state,t.expires_at AS oauth_expires_at,s.base_url AS service_base FROM connected_accounts a LEFT JOIN oauth_account_tokens t ON t.account_id=a.account_id LEFT JOIN account_services s ON s.account_id=a.account_id ORDER BY a.created_at LIMIT 100")]

    def revoke(self, account_id):
        with self.records.connect() as db:
            if db.execute("UPDATE connected_accounts SET status='revoked',revision=revision+1 WHERE account_id=?", (account_id,)).rowcount != 1:
                raise KeyError(account_id)

    def account(self, account_id, revision):
        with self.records.connect() as db:
            row = db.execute("SELECT a.*,s.base_url AS service_base FROM connected_accounts a LEFT JOIN account_services s ON s.account_id=a.account_id WHERE a.account_id=? AND a.revision=? AND a.status='connected'", (account_id, revision)).fetchone()
        if row is None:
            raise ValueError("Account was revoked or changed; reconnect and request a fresh approval.")
        item = dict(row)
        secret = self.vault.resolve(item["secret_reference"])
        if not secret:
            raise ValueError("Account credential was revoked.")
        return item, secret

    @staticmethod
    def payload(account, value):
        scopes = set(json.loads(account["scopes_json"]))
        if isinstance(value, PinterestPinInput):
            if account['provider'] != 'pinterest' or not SCOPES['pinterest'].issubset(scopes):
                raise ValueError('This action requires the documented Pinterest board/pin access.')
            return 'https://api.pinterest.com/v5/pins', {'board_id': value.board_id, 'title': value.title,
                'description': value.description, 'alt_text': value.alt_text,
                'media_source': {'source_type': 'image_url', 'url': value.image_url}}, (200, 201)
        if isinstance(value, ThreadsPostInput):
            if account['provider'] != 'threads' or not {'threads_basic', 'threads_content_publish'}.issubset(scopes):
                raise ValueError('This action requires a Threads content-publish connection.')
            return 'https://graph.threads.net/v1.0/me/threads', {
                'media_type': 'TEXT', 'text': value.body, 'auto_publish_text': True, 'reply_control': 'everyone'}, 200
        if isinstance(value, BlueskyPostInput):
            if account['provider'] != 'bluesky' or 'com.atproto.access' not in scopes:
                raise ValueError('This action requires a configured Bluesky access-token connection.')
            config = AccountInput(provider='bluesky', label=account['label'], secret_reference=account['secret_reference'],
                scopes=['com.atproto.access'], transmission_consent=True, service_base=account.get('service_base'))
            if value.service_base != config.service_base:
                raise ValueError('PDS destination changed; reconnect and request fresh approval.')
            return config.service_base + '/xrpc/com.atproto.repo.createRecord', {
                'repo': value.repository_did, 'collection': 'app.bsky.feed.post', 'rkey': value.record_key, 'validate': True,
                'record': {'$type': 'app.bsky.feed.post', 'text': value.body, 'createdAt': value.created_at}}, 200
        if isinstance(value, MastodonPostInput):
            if account['provider'] != 'mastodon' or 'write:statuses' not in scopes:
                raise ValueError('This action requires a Mastodon status-write connection.')
            # Revalidate persisted configuration; never accept a credential
            # destination supplied by the post/model arguments.
            config = AccountInput(provider='mastodon', label=account['label'], secret_reference=account['secret_reference'],
                scopes=['write:statuses'], transmission_consent=True, service_base=account.get('service_base'))
            if value.service_base != config.service_base:
                raise ValueError('Instance destination changed; reconnect and request fresh approval.')
            return config.service_base + '/api/v1/statuses', {'status': value.body, 'visibility': value.visibility,
                'sensitive': value.sensitive, 'spoiler_text': value.spoiler_text}, 200
        if isinstance(value, LinkedInPostInput):
            if account['provider'] != 'linkedin' or 'w_member_social' not in scopes:
                raise ValueError('Member publishing requires a LinkedIn connection with w_member_social permission.')
            return 'https://api.linkedin.com/rest/posts', {
                'author': value.author, 'commentary': value.body, 'visibility': 'PUBLIC',
                'distribution': {'feedDistribution': 'MAIN_FEED', 'targetEntities': [], 'thirdPartyDistributionChannels': []},
                'lifecycleState': 'PUBLISHED', 'isReshareDisabledByAuthor': True,
            }, 201
        if isinstance(value, CalendarInput):
            if account["provider"] == "google":
                if "https://www.googleapis.com/auth/calendar.events.owned" not in scopes:
                    raise ValueError("This connection lacks the required calendar scope.")
                payload = {"id": value.operation_id, "summary": value.title, "description": value.body,
                           "start": {"dateTime": value.start}, "end": {"dateTime": value.end}, "guestsCanInviteOthers": False}
                if value.attendees:
                    payload["attendees"] = [{"email": address} for address in value.attendees]
                updates = "all" if value.attendees else "none"
                return f"https://www.googleapis.com/calendar/v3/calendars/primary/events?sendUpdates={updates}", payload, 201
            if account["provider"] == "microsoft":
                if "Calendars.ReadWrite" not in scopes:
                    raise ValueError("This connection lacks the required calendar scope.")
                payload = {"subject": value.title, "body": {"contentType": "Text", "content": value.body},
                           "start": {"dateTime": datetime.fromisoformat(value.start).replace(tzinfo=None).isoformat(), "timeZone": "UTC"},
                           "end": {"dateTime": datetime.fromisoformat(value.end).replace(tzinfo=None).isoformat(), "timeZone": "UTC"},
                           "transactionId": str(uuid.UUID(hex=value.operation_id))}
                if value.attendees:
                    payload["attendees"] = [{"emailAddress": {"address": address}, "type": "required"} for address in value.attendees]
                return "https://graph.microsoft.com/v1.0/me/events", payload, 201
            raise ValueError("Calendar actions require a Google or Microsoft account.")
        if isinstance(value, IssueInput):
            if account["provider"] != "github" or "issues:write" not in scopes:
                raise ValueError("This action requires a GitHub account.")
            return f"https://api.github.com/repos/{value.repository}/issues", {"title": value.title, "body": value.body}, 201
        if account["provider"] == "google":
            if "https://www.googleapis.com/auth/gmail.send" not in scopes:
                raise ValueError("This connection lacks Gmail send scope.")
            message = EmailMessage(policy=SMTP)
            message["To"] = ", ".join(value.recipients)
            message["Subject"] = value.subject
            message.set_content(value.body)
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            return "https://gmail.googleapis.com/gmail/v1/users/me/messages/send", {"raw": raw}, 200
        if account["provider"] == "microsoft":
            if "Mail.Send" not in scopes:
                raise ValueError("This connection lacks mail send scope.")
            payload = {"message": {"subject": value.subject, "body": {"contentType": "Text", "content": value.body},
                                   "toRecipients": [{"emailAddress": {"address": address}} for address in value.recipients]}, "saveToSentItems": True}
            return "https://graph.microsoft.com/v1.0/me/sendMail", payload, 202
        raise ValueError("This action requires a configured email account.")

    async def dispatch(self, value):
        authorize = execution_dispatch_authorized.get()
        async with self.gate:
            authorize()
            account, credential = self.account(value.account_id, value.account_revision)
            url, payload, accepted = self.payload(account, value)
            arguments_sha = hashlib.sha256(canonical_arguments(value.model_dump()).encode()).hexdigest()
            with self.records.connect() as db:
                try:
                    db.execute("INSERT INTO account_actions VALUES (?,?,?,'dispatching',NULL,?)", (value.operation_id, value.account_id, arguments_sha, now()))
                except sqlite3.IntegrityError:
                    raise ValueError("This operation ID was already dispatched or has an uncertain outcome; do not retry blindly.") from None
            status, receipt = "outcome_unknown", None
            proxy = None
            try:
                headers = {"Authorization": f"Bearer {credential}", "Accept": "application/json"}
                if account["provider"] == "github":
                    headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"})
                if account['provider'] == 'linkedin':
                    headers.update({'LinkedIn-Version': '202605', 'X-Restli-Protocol-Version': '2.0.0'})
                options = {'trust_env': False, 'follow_redirects': False, 'timeout': 20}
                if account['provider'] in {'mastodon', 'bluesky'}:
                    if account['provider'] == 'mastodon':
                        headers['Idempotency-Key'] = value.operation_id
                    proxy = self.proxy_factory(NetworkDestinationPolicy((urlsplit(url).hostname,), exact_hosts=True, allowed_ports={443}))
                    options['proxy'] = await proxy.start()
                async with self.client_factory(**options) as client:
                    authorize()
                    current_account, current_secret = self.account(value.account_id, value.account_revision)
                    if credential != current_secret or current_account.get('service_base') != account.get('service_base'):
                        raise ValueError("Account credentials or instance destination changed before dispatch.")
                    authorize()
                    body_options = {'data': {key: ('true' if item else 'false') if isinstance(item, bool) else item for key, item in payload.items()}} if account['provider'] in {'mastodon', 'threads'} else {'json': payload}
                    async with client.stream("POST", url, **body_options, headers=headers) as response:
                        # Provider acceptance is not delivery or an undo guarantee.
                        status = "accepted" if response.status_code in (accepted if isinstance(accepted, tuple) else (accepted,)) else (
                            "rejected" if response.status_code in {400, 401, 403, 404, 422} else "outcome_unknown")
                        receipt = {"http_status": response.status_code, "provider": account["provider"]}
                        # Do not persist returned message bodies, arbitrary redirects
                        # or credentials echoed by a server.
            except httpx.HTTPError:
                raise ToolExecutionOutcome({"status": "outcome_unknown", "operation_id": value.operation_id, "retry": "manual reconciliation required"}) from None
            finally:
                try:
                    if proxy is not None:
                        await proxy.close()
                finally:
                    with self.records.connect() as db:
                        db.execute("UPDATE account_actions SET status=?,receipt_json=? WHERE operation_id=?", (status, json.dumps(receipt) if receipt else None, value.operation_id))
            result = {"status": status, "operation_id": value.operation_id, "receipt": receipt}
            if status != "accepted":
                raise ToolExecutionOutcome(result)
            return result


def register_account_tools(registry, actions):
    guard = WorkspaceGuard(registry.workspace_root)

    def preview(arguments):
        account, _ = actions.account(arguments["account_id"], arguments["account_revision"])
        return {"account": {"provider": account["provider"], "label": account["label"], "revision": account["revision"], 'service_base': account.get('service_base')},
                "exact_payload": {key: value for key, value in arguments.items() if key not in {"account_id"}},
                "external_effect": "Consequential external request. Provider acceptance cannot always be cancelled or undone."}

    for name, model in [("account_send_email", SendInput), ("account_github_issue", IssueInput), ("account_calendar_create", CalendarInput), ('account_linkedin_post', LinkedInPostInput), ('account_mastodon_post', MastodonPostInput), ('account_bluesky_post', BlueskyPostInput), ('account_threads_post', ThreadsPostInput), ('account_pinterest_pin', PinterestPinInput)]:
        registry.register(Tool(name, "Send the exact approved account payload once; uncertain outcomes require manual reconciliation.", model, RiskLevel.HIGH, 30, actions.dispatch,
                               resolve_destination=lambda _: guard.resolve("."), category=ActionCategory.PUBLISH, preview_details=preview))
    from services.agent_api.app.extended_publishing import register_extended_publishing
    register_extended_publishing(registry, actions)
    refresh_account_tools(registry, actions)


def refresh_account_tools(registry, actions):
    from services.agent_api.app.extended_publishing import refresh_extended_publishing
    refresh_extended_publishing(registry, actions)
    rows = [row for row in actions.accounts() if row["status"] == "connected"]
    providers = {row["provider"] for row in rows}
    permissions = set().union(*(set(json.loads(row["scopes_json"])) for row in rows))
    registry.set_enabled("account_send_email", bool(permissions & {"https://www.googleapis.com/auth/gmail.send", "Mail.Send"}))
    registry.set_enabled("account_github_issue", "github" in providers)
    registry.set_enabled("account_calendar_create", bool(permissions & {"https://www.googleapis.com/auth/calendar.events.owned", "Calendars.ReadWrite"}))
    registry.set_enabled('account_linkedin_post', any(row['provider'] == 'linkedin' and 'w_member_social' in json.loads(row['scopes_json']) for row in rows))
    registry.set_enabled('account_mastodon_post', any(row['provider'] == 'mastodon' and row.get('service_base') and 'write:statuses' in json.loads(row['scopes_json']) for row in rows))
    registry.set_enabled('account_bluesky_post', any(row['provider'] == 'bluesky' and row.get('service_base') and 'com.atproto.access' in json.loads(row['scopes_json']) for row in rows))
    registry.set_enabled('account_threads_post', any(row['provider'] == 'threads' and {'threads_basic', 'threads_content_publish'}.issubset(json.loads(row['scopes_json'])) for row in rows))
    registry.set_enabled('account_pinterest_pin', any(row['provider'] == 'pinterest' and SCOPES['pinterest'].issubset(json.loads(row['scopes_json'])) for row in rows))
