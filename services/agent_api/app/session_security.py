"""Process-local sessions. Pairing secrets and bearer tokens never enter SQLite."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import HTTPException

POLICY_VERSION = "local-v1"
execution_session: ContextVar[str] = ContextVar("execution_session", default="internal")
execution_agent_job: ContextVar[str | None] = ContextVar("execution_agent_job", default=None)
execution_authorized: ContextVar[Callable[[], bool]] = ContextVar("execution_authorized", default=lambda: True)
execution_dispatch_authorized: ContextVar[Callable[[], None]] = ContextVar("execution_dispatch_authorized", default=lambda: None)


@dataclass(frozen=True)
class Session:
    identity: str
    expires: float


class SessionManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self.pairing_code = secrets.token_urlsafe(24)
        self._pairing_expires = time.monotonic() + 600
        self._failures = 0

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def pair(self, code: str) -> str:
        with self._lock:
            if (self._failures >= 10 or time.monotonic() >= self._pairing_expires
                    or not self.pairing_code
                    or not secrets.compare_digest(code.encode(), self.pairing_code.encode())):
                self._failures += 1
                raise HTTPException(401, "Pairing denied. Use the current code from the backend terminal.")
            token = secrets.token_urlsafe(32)
            self._sessions[self._digest(token)] = Session(secrets.token_hex(16), time.monotonic() + 43200)
            self.pairing_code = ""
            return token

    def authenticate(self, token: str) -> Session | None:
        with self._lock:
            digest = self._digest(token)
            session = self._sessions.get(digest)
            if session and time.monotonic() < session.expires:
                return session
            self._sessions.pop(digest, None)
            return None

    def identity_active(self, identity: str) -> bool:
        with self._lock:
            instant = time.monotonic()
            return any(session.identity == identity and instant < session.expires for session in self._sessions.values())

    def revoke(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(self._digest(token), None)

    def rotate_pairing(self) -> str:
        with self._lock:
            self.pairing_code = secrets.token_urlsafe(24)
            self._pairing_expires = time.monotonic() + 600
            self._failures = 0
            return self.pairing_code


class SessionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] == "OPTIONS" or scope["path"] in {
            "/api/v1/health", "/api/v1/session/pair", "/api/v1/oauth/callback",
        }:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        token = authorization[7:] if authorization.startswith("Bearer ") else ""
        manager = scope["app"].state.sessions
        session = manager.authenticate(token) if token else None
        if session is None:
            body = json.dumps({"detail": "Pair with the local backend to continue.", "code": "session_required"}).encode()
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": body})
            return
        identity = execution_session.set(session.identity)
        validity = execution_authorized.set(lambda: manager.authenticate(token) is not None)
        scope.setdefault("state", {})["session_token"] = token
        async def private_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = [(name, value) for name, value in message.get("headers", []) if name.lower() != b"cache-control"] + [(b"cache-control", b"no-store")]
            await send(message)

        try:
            await self.app(scope, receive, private_send)
        finally:
            execution_session.reset(identity)
            execution_authorized.reset(validity)
