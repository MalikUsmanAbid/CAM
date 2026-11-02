"""Approved single-URL retrieval through the existing DNS-pinned public proxy."""

import hashlib
import uuid
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx
from pydantic import Field, field_validator

from services.agent_api.app.action_policy import ActionCategory, ActionPolicy
from services.agent_api.app.memory import is_sensitive_content
from services.agent_api.app.network_security import NetworkDestinationPolicy, PinnedHTTPProxy
from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.session_security import execution_dispatch_authorized
from services.agent_api.app.tools import Tool, ToolInput
from services.agent_api.app.workspace_records import WorkspaceRecords, now


class FetchSource(ToolInput):
    url: str = Field(min_length=1, max_length=2000)
    title: str = Field(min_length=1, max_length=200)

    @field_validator('url')
    @classmethod
    def public_https(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None, 443) or is_sensitive_content(value):
            raise ValueError('Use an exact HTTPS source without credentials, query or fragment.')
        return value

    @field_validator('title')
    @classmethod
    def safe_title(cls, value):
        if not value.strip() or is_sensitive_content(value):
            raise ValueError('Source title is blank or contains potential credentials.')
        return value.strip()


class TextExtraction(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.output = []
        self.size = 0

    def handle_starttag(self, tag, attributes):
        if tag in ('script', 'style', 'noscript', 'template'):
            self.hidden += 1
        if not self.hidden and tag in ('p', 'div', 'br', 'li', 'h1', 'h2', 'h3'):
            self.handle_data('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'template'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.size += len(data.encode())
            if self.size > 32000:
                raise ValueError('Extracted source text exceeds its bound; choose a narrower source.')
            self.output.append(data)


def register_research_tool(registry, database, project_id):
    records = WorkspaceRecords(database)
    policy = ActionPolicy(database)

    async def fetch(value):
        previous = execution_dispatch_authorized.get()
        def authorize():
            previous()
            if policy.denied(ActionCategory.EDIT):
                raise ValueError('Current policy blocks storing research snapshots.')
        authorize()
        parsed = urlsplit(value.url)
        proxy = PinnedHTTPProxy(NetworkDestinationPolicy((parsed.hostname,), exact_hosts=True, allowed_ports={443}))
        proxy_url = await proxy.start()
        try:
            async with httpx.AsyncClient(proxy=proxy_url, trust_env=False, follow_redirects=False, timeout=20) as client:
                authorize()
                async with client.stream('GET', value.url, headers={'Accept': 'text/html,text/plain,application/json', 'Accept-Encoding': 'identity'}) as response:
                    if response.status_code != 200:
                        raise ValueError('Source was not returned directly with HTTP 200. Redirects require a separately approved URL.')
                    if response.headers.get('content-encoding', 'identity').strip().lower() != 'identity':
                        raise ValueError('Compressed research responses are excluded from bounded retrieval.')
                    media = response.headers.get('content-type', '').split(';')[0].lower()
                    if media not in ('text/html', 'text/plain', 'application/json', 'application/xhtml+xml'):
                        raise ValueError('Only bounded text/HTML/JSON sources are supported.')
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > 160000:
                            raise ValueError('Source response exceeds its bound.')
                        raw.extend(chunk)
            text = bytes(raw).decode('utf-8', errors='strict')
            if media in ('text/html', 'application/xhtml+xml'):
                parser = TextExtraction()
                parser.feed(text)
                text = ''.join(parser.output).strip()
            if not text.strip() or len(text.encode()) > 32000 or is_sensitive_content(text):
                raise ValueError('Source is empty, oversized or contains potential credentials.')
            authorize()
            identifier = uuid.uuid4().hex
            digest = hashlib.sha256(text.encode()).hexdigest()
            with records.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT COUNT(*) FROM knowledge_documents WHERE project_id=?', (project_id,)).fetchone()[0] >= 1000:
                    raise ValueError('Project knowledge limit reached.')
                db.execute('INSERT INTO knowledge_documents VALUES (?,?,?,?,?,?,?,?)',
                           (identifier, project_id, str(registry.workspace_root.resolve()), value.title, value.url, digest, text, now()))
            return {'document_id': identifier, 'source_url': value.url, 'snapshot_sha256': digest,
                    'response_sha256': hashlib.sha256(raw).hexdigest(), 'content': text,
                    'evidence': 'Fetched source snapshot, not verified factual truth. No scripts, nested resources or redirects executed.'}
        finally:
            await proxy.close()

    registry.register(Tool('research_fetch', 'Fetch the exact approved public HTTPS URL and retain bounded untrusted text with provenance.', FetchSource, RiskLevel.HIGH, 30, fetch,
                           resolve_destination=lambda _: registry.workspace_root.resolve(), category=ActionCategory.READ))
