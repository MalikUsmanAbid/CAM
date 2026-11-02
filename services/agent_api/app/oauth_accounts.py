"""Explicit installed-client OAuth/PKCE for Google and Microsoft.

No embedded browser, registered client IDs, credentials or live login are
provided by this module. Refresh is an explicit action and advances account
revision, requiring a new approval for subsequent consequential operations.
"""

import asyncio
import base64
import hashlib
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from services.agent_api.app.account_actions import SCOPES, AccountInput
from services.agent_api.app.action_policy import ActionCategory
from services.agent_api.app.session_security import execution_session
from services.agent_api.app.workspace_records import WorkspaceRecords

DESCRIPTORS = {
    'google': ('https://accounts.google.com/o/oauth2/v2/auth', 'https://oauth2.googleapis.com/token'),
    'microsoft': ('https://login.microsoftonline.com/common/oauth2/v2.0/authorize', 'https://login.microsoftonline.com/common/oauth2/v2.0/token'),
}


class OAuthClientInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    provider: Literal['google', 'microsoft']
    client_id: str = Field(min_length=1, max_length=300, pattern=r'^[A-Za-z0-9._-]+$')
    secret_reference: str | None = Field(default=None, pattern=r'^vault:[a-f0-9]{32}$')


class OAuthStartInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    provider: Literal['google', 'microsoft']
    label: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(min_length=1, max_length=2)
    transmission_consent: bool


class OAuthAccounts:
    def __init__(self, app, *, client_factory=httpx.AsyncClient):
        self.app = app
        self.records = WorkspaceRecords(app.state.settings.database_path)
        self.pending = {}
        self.gate = asyncio.Semaphore(1)
        self.client_factory = client_factory
        with self.records.connect() as db:
            db.execute("UPDATE oauth_account_tokens SET state='reconnect_required' WHERE state='refreshing'")

    def authorize(self, owner):
        state = self.app.state
        if not state.sessions.identity_active(owner) or state.executor.policy.denied(ActionCategory.ACCOUNT):
            raise ValueError('Account authorization expired, was revoked or is blocked by policy.')

    def configure(self, value):
        self.authorize(execution_session.get())
        if value.secret_reference and not self.app.state.secret_resolver.resolve(value.secret_reference):
            raise ValueError('Protected OAuth client secret is unavailable.')
        with self.records.connect() as db:
            db.execute('INSERT INTO oauth_clients VALUES (?,?,?,1) ON CONFLICT(provider) DO UPDATE SET client_id=excluded.client_id,secret_reference=excluded.secret_reference,revision=oauth_clients.revision+1',
                       (value.provider, value.client_id, value.secret_reference))

    def client(self, provider):
        with self.records.connect() as db:
            row = db.execute('SELECT * FROM oauth_clients WHERE provider=?', (provider,)).fetchone()
        if row is None:
            raise ValueError('Configure a registered installed/public OAuth client first.')
        return dict(row)

    @staticmethod
    def outgoing_scopes(provider, scopes):
        if not scopes or not set(scopes).issubset(SCOPES[provider]):
            raise ValueError('Only explicitly selected supported scopes may be requested.')
        if provider == 'google':
            return sorted(set(scopes))
        return sorted({'https://graph.microsoft.com/' + value for value in scopes} | {'offline_access'})

    def start(self, value):
        owner = execution_session.get()
        self.authorize(owner)
        if not value.transmission_consent:
            raise ValueError('Explicit account transmission consent is required.')
        # Apply the same label/credential-reference schema before storing flow data.
        AccountInput(provider=value.provider, label=value.label, scopes=value.scopes,
                     secret_reference='vault:' + '0' * 32, transmission_consent=True)
        client = self.client(value.provider)
        scopes = self.outgoing_scopes(value.provider, value.scopes)
        self.pending = {key: row for key, row in self.pending.items() if time.monotonic() - row['created'] < 600}
        if len(self.pending) >= 10:
            raise ValueError('Finish pending account connections first.')
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        redirect = f'http://127.0.0.1:{self.app.state.settings.backend_port}/api/v1/oauth/callback'
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        self.pending[hashlib.sha256(state.encode()).hexdigest()] = {
            'created': time.monotonic(), 'owner': owner, 'verifier': verifier, 'redirect': redirect,
            'client': client, 'value': value, 'requested_scopes': scopes,
        }
        parameters = {'client_id': client['client_id'], 'response_type': 'code', 'redirect_uri': redirect,
                      'scope': ' '.join(scopes), 'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'}
        if value.provider == 'google':
            parameters.update(access_type='offline', prompt='consent')
        return {'authorization_url': DESCRIPTORS[value.provider][0] + '?' + urlencode(parameters),
                'expires_in_seconds': 600, 'redirect_uri': redirect, 'action': 'Open this provider page explicitly to sign in; do not share the link.'}

    def cancel(self):
        owner = execution_session.get()
        self.pending = {key: value for key, value in self.pending.items() if value['owner'] != owner}

    async def token_request(self, provider, parameters, owner, revision):
        async with self.gate:
            self.authorize(owner)
            client_config = self.client(provider)
            if client_config['revision'] != revision:
                raise ValueError('OAuth client configuration changed; start a fresh connection.')
            parameters = dict(parameters, client_id=client_config['client_id'])
            if client_config['secret_reference']:
                secret = self.app.state.secret_resolver.resolve(client_config['secret_reference'])
                if not secret:
                    raise ValueError('OAuth client credential was revoked.')
                parameters['client_secret'] = secret
            async with self.client_factory(trust_env=False, follow_redirects=False, timeout=20) as transport:
                self.authorize(owner)
                if self.client(provider)['revision'] != revision:
                    raise ValueError('OAuth configuration changed before token dispatch.')
                if client_config['secret_reference'] and self.app.state.secret_resolver.resolve(client_config['secret_reference']) != parameters.get('client_secret'):
                    raise ValueError('OAuth client credential changed before token dispatch.')
                async with transport.stream('POST', DESCRIPTORS[provider][1], data=parameters, headers={'Accept': 'application/json'}) as response:
                    if response.status_code != 200:
                        raise ValueError('Provider denied token exchange; reconnect rather than blindly retrying.')
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > 64000:
                            raise ValueError('OAuth response exceeds its bound.')
                        data.extend(chunk)
            self.authorize(owner)
            if self.client(provider)['revision'] != revision:
                raise ValueError('OAuth configuration changed during token exchange.')
            if client_config['secret_reference'] and self.app.state.secret_resolver.resolve(client_config['secret_reference']) != parameters.get('client_secret'):
                raise ValueError('OAuth client credential changed during token exchange.')
            value = json.loads(data)
            if not isinstance(value, dict) or str(value.get('token_type', '')).lower() != 'bearer':
                raise ValueError('Unsupported OAuth token response.')
            for field in ('access_token', 'refresh_token'):
                token = value.get(field)
                if field == 'refresh_token' and token is None:
                    continue
                if not isinstance(token, str) or not 1 <= len(token) <= 16000 or any(character.isspace() for character in token):
                    raise ValueError('Invalid bounded OAuth credential.')
            expiry = value.get('expires_in')
            if expiry is not None and (type(expiry) is not int or not 1 <= expiry <= 86400):
                raise ValueError('Unsupported token expiry.')
            return value

    @staticmethod
    def check_scopes(provider, value, requested):
        returned = value.get('scope')
        if returned is None:
            return
        if not isinstance(returned, str) or len(returned) > 4000:
            raise ValueError('Invalid token scope response.')
        def normalized(scopes):
            return {scope.removeprefix('https://graph.microsoft.com/') if provider == 'microsoft' else scope for scope in scopes}
        if not normalized(returned.split()).issubset(normalized(requested)):
            raise ValueError('Provider returned broader permissions than requested.')
        required = normalized(requested) - {'offline_access'}
        if not required.issubset(normalized(returned.split())):
            raise ValueError('Provider did not grant all selected permissions; reconnect with an explicit narrower selection.')

    async def finish(self, state, code):
        if not isinstance(state, str) or not 1 <= len(state) <= 200 or not isinstance(code, str) or not 1 <= len(code) <= 4096:
            raise ValueError('Invalid authorization callback.')
        pending = self.pending.pop(hashlib.sha256(state.encode()).hexdigest(), None)
        if pending is None or time.monotonic() - pending['created'] >= 600:
            raise ValueError('Authorization state expired or was already consumed.')
        owner, config, selected = pending['owner'], pending['client'], pending['value']
        value = await self.token_request(selected.provider, {'grant_type': 'authorization_code', 'code': code,
            'redirect_uri': pending['redirect'], 'code_verifier': pending['verifier']}, owner, config['revision'])
        self.check_scopes(selected.provider, value, pending['requested_scopes'])
        self.authorize(owner)
        access = self.app.state.secret_resolver.store(value['access_token'])
        refresh = self.app.state.secret_resolver.store(value['refresh_token']) if value.get('refresh_token') else None
        account = self.app.state.account_actions.connect(AccountInput(provider=selected.provider, label=selected.label,
            secret_reference=access, scopes=selected.scopes, transmission_consent=True))
        expires = (datetime.now(UTC) + timedelta(seconds=value['expires_in'])).isoformat() if value.get('expires_in') else None
        with self.records.connect() as db:
            db.execute('INSERT INTO oauth_account_tokens VALUES (?,?,?,?,?,?)', (account, selected.provider, config['revision'], refresh, expires, 'ready'))
        return account

    async def refresh(self, account_id):
        owner = execution_session.get()
        self.authorize(owner)
        with self.records.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT t.*,a.revision,a.secret_reference,a.scopes_json FROM oauth_account_tokens t JOIN connected_accounts a ON a.account_id=t.account_id WHERE t.account_id=? AND a.status='connected' AND t.state='ready'", (account_id,)).fetchone()
            if row is None or not row['refresh_reference']:
                raise ValueError('This account requires a fresh authorization flow.')
            row = dict(row)
            db.execute("UPDATE oauth_account_tokens SET state='refreshing' WHERE account_id=?", (account_id,))
        try:
            token = self.app.state.secret_resolver.resolve(row['refresh_reference'])
            if not token:
                raise ValueError('Refresh credential was revoked.')
            requested = self.outgoing_scopes(row['provider'], json.loads(row['scopes_json']))
            parameters = {'grant_type': 'refresh_token', 'refresh_token': token}
            if row['provider'] == 'microsoft':
                parameters['scope'] = ' '.join(requested)
            result = await self.token_request(row['provider'], parameters, owner, row['client_revision'])
            self.check_scopes(row['provider'], result, requested)
            self.authorize(owner)
            access = self.app.state.secret_resolver.store(result['access_token'])
            refresh = self.app.state.secret_resolver.store(result['refresh_token']) if result.get('refresh_token') else row['refresh_reference']
            expires = (datetime.now(UTC) + timedelta(seconds=result['expires_in'])).isoformat() if result.get('expires_in') else None
            with self.records.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute("UPDATE connected_accounts SET secret_reference=?,revision=revision+1 WHERE account_id=? AND status='connected' AND revision=?", (access, account_id, row['revision'])).rowcount != 1:
                    raise ValueError('Account changed or was revoked during refresh; reconnect explicitly.')
                db.execute("UPDATE oauth_account_tokens SET refresh_reference=?,expires_at=?,state='ready' WHERE account_id=?", (refresh, expires, account_id))
        except BaseException:
            with self.records.connect() as db:
                db.execute("UPDATE oauth_account_tokens SET state='reconnect_required' WHERE account_id=? AND state='refreshing'", (account_id,))
            raise
        return {'status': 'refreshed', 'approvals': 'Account revision advanced; request fresh action approvals.'}


router = APIRouter(prefix='/api/v1/oauth', tags=['oauth'])


def personal(request):
    if request.query_params.get('project_id'):
        raise HTTPException(400, 'OAuth connections require Personal scope.')


@router.put('/client')
async def configure_client(value: OAuthClientInput, request: Request):
    personal(request)
    request.app.state.oauth_accounts.configure(value)
    return {'status': 'configured', 'verification': 'Registered provider application and redirect setup must be verified separately.'}


@router.post('/start')
async def start(value: OAuthStartInput, request: Request):
    personal(request)
    return request.app.state.oauth_accounts.start(value)


@router.post('/cancel')
async def cancel(request: Request):
    personal(request)
    request.app.state.oauth_accounts.cancel()
    return {'status': 'pending_connections_cancelled', 'provider_consent': 'Manage already granted provider access at the provider; no remote revocation is inferred.'}


@router.post('/accounts/{account_id}/refresh')
async def refresh(account_id: str, request: Request):
    personal(request)
    return await request.app.state.oauth_accounts.refresh(account_id)


@router.get('/callback')
async def callback(request: Request):
    parameters = request.query_params
    request.scope['query_string'] = b''
    try:
        if parameters.get('error'):
            raise ValueError('Provider authorization was declined.')
        await request.app.state.oauth_accounts.finish(parameters.get('state'), parameters.get('code'))
        from services.agent_api.app.workspace_api import refresh_account_availability
        refresh_account_availability(request.app)
        message = 'Account configuration stored. Return to JARVIS and refresh Connections. No message or calendar action was executed.'
        status = 200
    except Exception:
        message = 'Account authorization did not complete. Return to JARVIS and start a fresh sign-in after checking client registration, consent and session expiry.'
        status = 400
    return HTMLResponse(message, status_code=status, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', 'Content-Security-Policy': "default-src 'none'"})


class OAuthCallbackPrivacy:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def private_send(message):
            if scope.get('path') == '/api/v1/oauth/callback' and message['type'] == 'http.response.start':
                scope['query_string'] = b''
            await send(message)
        await self.app(scope, receive, private_send)
