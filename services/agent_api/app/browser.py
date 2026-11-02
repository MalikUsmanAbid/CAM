"""Bounded browser adapter registered exclusively through ToolRegistry."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlparse

from pydantic import Field, field_validator

from services.agent_api.app.memory import is_sensitive_content
from services.agent_api.app.network_security import (
    NetworkDestinationPolicy,
    Resolver,
)
from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.session_security import execution_dispatch_authorized, execution_session
from services.agent_api.app.tools import Tool, ToolInput, ToolRegistry

MAX_ACTIONS = 12
MAX_PAGE_CHARS = 20_000
MAX_BROWSER_SESSIONS = 3


class BrowserAdapter(Protocol):
    async def open_url(self, url: str) -> dict[str, str]: ...
    async def inspect_page(self) -> dict[str, str]: ...
    async def act(self, action: str, target: str, value: str = "") -> dict[str, str]: ...
    async def close(self) -> None: ...


class OpenInput(ToolInput):
    session_id: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2_000)


class TargetInput(ToolInput):
    session_id: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=300)
    value: str = Field(default="", max_length=2_000)

    @field_validator('value')
    @classmethod
    def no_credentials(cls, value):
        if is_sensitive_content(value) or '\x00' in value:
            raise ValueError('Credential-shaped input is excluded; perform account login manually.')
        return value


class SessionInput(ToolInput):
    session_id: str = Field(min_length=1, max_length=100)


class BrowserNetworkPolicy:
    def __init__(
        self, *, allow_private_addresses: bool = False, resolver: Resolver | None = None
    ) -> None:
        self.allow_private_addresses = allow_private_addresses
        self.resolver = resolver

    async def validate(self, url: str, allowed_domains: tuple[str, ...]) -> None:
        parsed = urlparse(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or (
                parsed.port not in {None, 80 if parsed.scheme == "http" else 443}
                and not (self.allow_private_addresses and parsed.hostname in {"127.0.0.1", "::1", "localhost"})
            )
        ):
            raise ValueError("Browser URL is not allowed.")
        policy = NetworkDestinationPolicy(
            allowed_domains,
            allow_private_addresses=self.allow_private_addresses,
            resolver=self.resolver,
        )
        await policy.resolve(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            parsed.scheme,
        )


@dataclass
class BrowserSession:
    session_id: str
    adapter: BrowserAdapter
    allowed_domains: tuple[str, ...]
    task_id: str | None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_activity: datetime = field(default_factory=lambda: datetime.now(UTC))
    actions: int = 0
    url: str = ""
    owner: str = field(default_factory=lambda: execution_session.get())
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class BrowserManager:
    def __init__(self, network_policy: BrowserNetworkPolicy | None = None, *, runtime=None) -> None:
        self._sessions: dict[str, BrowserSession] = {}
        self.network_policy = network_policy or BrowserNetworkPolicy()
        self.runtime = runtime

    @property
    def available(self):
        return bool(self.runtime is not None and self.runtime.available)

    def create(
        self,
        session_id: str,
        adapter: BrowserAdapter,
        allowed_domains: list[str],
        task_id: str | None = None,
    ) -> BrowserSession:
        if (
            session_id in self._sessions
            or len(self._sessions) >= MAX_BROWSER_SESSIONS
            or not allowed_domains
            or len(allowed_domains) > 20
            or any(not domain or len(domain) > 253 for domain in allowed_domains)
        ):
            raise ValueError("Browser session is invalid.")
        session = BrowserSession(session_id, adapter, tuple(allowed_domains), task_id)
        self._sessions[session_id] = session
        return session

    async def close(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        if session.owner != execution_session.get():
            raise ValueError('Browser session belongs to another authenticated session.')
        self._sessions.pop(session_id, None)
        await session.adapter.close()

    async def close_all(self) -> None:
        sessions = list(self._sessions.values())
        self._sessions.clear()
        await asyncio.gather(*(session.adapter.close() for session in sessions), return_exceptions=True)

    async def open(self, data: OpenInput) -> dict[str, str]:
        session = await self._get(data.session_id)
        async with session.lock:
            await self._get(data.session_id)
            await self.network_policy.validate(data.url, session.allowed_domains)
            self._tick(session)
            execution_dispatch_authorized.get()()
            result = await session.adapter.open_url(data.url)
            final_url = result.get("url", data.url)
            await self.network_policy.validate(final_url, session.allowed_domains)
            session.url = final_url
            return {"url": final_url, "verification": "url_allowed"}

    async def inspect(self, data: SessionInput) -> dict[str, str]:
        session = await self._get(data.session_id)
        async with session.lock:
            await self._get(data.session_id)
            self._tick(session)
            execution_dispatch_authorized.get()()
            result = await session.adapter.inspect_page()
            current_url = result.get('url', session.url)
            if current_url:
                await self.network_policy.validate(current_url, session.allowed_domains)
                session.url = current_url
        text = result.get("text", "")
        if len(text) > MAX_PAGE_CHARS:
            raise ValueError("Page content exceeded the allowed size.")
        return {"url": current_url, "content": text, "trust": "untrusted_data"}

    async def action(self, action: str, data: TargetInput) -> dict[str, str]:
        session = await self._get(data.session_id)
        async with session.lock:
            await self._get(data.session_id)
            self._tick(session)
            if action == "upload" and not data.value.startswith("user-selected:"):
                raise ValueError("Uploads require a user-selected file reference.")
            execution_dispatch_authorized.get()()
            result = await session.adapter.act(action, data.target, data.value)
            if result.get('url'):
                await self.network_policy.validate(result['url'], session.allowed_domains)
                session.url = result['url']
            return result

    async def _get(self, session_id: str) -> BrowserSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError("Browser session is unavailable.")
        if session.owner != execution_session.get():
            raise ValueError('Browser session belongs to another authenticated session.')
        if datetime.now(UTC) - session.last_activity > timedelta(minutes=10):
            self._sessions.pop(session_id, None)
            await session.adapter.close()
            raise ValueError("Browser session expired.")
        return session

    def _tick(self, session: BrowserSession) -> None:
        session.actions += 1
        session.last_activity = datetime.now(UTC)
        if session.actions > MAX_ACTIONS:
            raise ValueError("Browser action limit exceeded.")



class BrowserNetworkIsolationUnavailable(RuntimeError):
    """Raised until browser process egress is constrained by the operating system."""


class PlaywrightBrowserAdapter:
    """Fail-closed browser entry point pending enforceable OS-level network isolation."""

    @classmethod
    async def create(
        cls, allowed_domains: tuple[str, ...], policy: BrowserNetworkPolicy, *, runtime=None
    ) -> "PlaywrightBrowserAdapter":
        if runtime is not None:
            try:
                return await runtime.create(allowed_domains, policy)
            except (ValueError, OSError) as error:
                raise BrowserNetworkIsolationUnavailable('Native browser isolation could not be established; no unrestricted fallback.') from error
        raise BrowserNetworkIsolationUnavailable(
            "Browser sessions are disabled because OS-level outbound network isolation "
            "is unavailable. Chromium proxy settings cannot prevent direct WebRTC TCP/UDP traffic."
        )


def register_browser_tools(registry: ToolRegistry, manager: BrowserManager) -> None:
    registry.register(
        Tool(
            "browser_open_url",
            "Open an allowlisted web URL.",
            OpenInput,
            RiskLevel.LOW,
            10,
            manager.open,
            enabled=manager.available or bool(manager._sessions),
        )
    )
    registry.register(
        Tool(
            "browser_inspect_page",
            "Read bounded untrusted page content.",
            SessionInput,
            RiskLevel.LOW,
            10,
            manager.inspect,
            enabled=manager.available or bool(manager._sessions),
        )
    )
    for action in (
        "click",
        "type",
        "select",
        "scroll",
        "wait",
        "go_back",
        "go_forward",
        "refresh",
        "download",
        "upload",
    ):
        registry.register(
            Tool(
                f"browser_{action}",
                f"Controlled browser {action}.",
                TargetInput,
                RiskLevel.MEDIUM,
                10,
                lambda data, name=action: manager.action(name, TargetInput.model_validate(data)),
                enabled=manager.available or bool(manager._sessions),
            )
        )
