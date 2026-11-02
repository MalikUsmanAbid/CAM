import asyncio

import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.config import get_settings
from services.agent_api.app.loopback_only import LoopbackOnlyMiddleware
from services.agent_api.app.main import app


def test_unsafe_backend_bind_configuration_is_rejected(monkeypatch):
    monkeypatch.setenv("BACKEND_HOST", "0.0.0.0")
    with pytest.raises(ValueError, match="remote access is unsupported"):
        get_settings()


def test_local_api_allows_frontend_origin_and_rejects_untrusted_origin(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_PROVIDER", "mock")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "jarvis.db"))
    monkeypatch.setenv("SAFE_WORKSPACE", str(tmp_path / "workspace"))
    with TestClient(app) as client:
        local = client.get("/api/v1/health")
        allowed = client.get(
            "/api/v1/health", headers={"Origin": "http://localhost:5173"}
        )
        denied = client.post(
            "/api/v1/approvals/any/approve",
            headers={"Origin": "https://attacker.example"},
        )
    assert local.status_code == 200
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert denied.status_code == 403


@pytest.mark.anyio
async def test_loopback_middleware_rejects_remote_peer_even_with_spoofed_host():
    called = False

    async def downstream(_scope, _receive, _send):
        nonlocal called
        called = True

    middleware = LoopbackOnlyMiddleware(downstream, ())
    scope = {
        "type": "http",
        "client": ("203.0.113.10", 4321),
        "headers": [(b"host", b"127.0.0.1:8000")],
    }
    sent = []

    async def send(event):
        sent.append(event)

    await middleware(scope, asyncio.Queue().get, send)
    assert not called
    assert sent[0]["status"] == 403


@pytest.mark.anyio
async def test_loopback_middleware_rejects_host_header_rebinding():
    called = False

    async def downstream(_scope, _receive, _send):
        nonlocal called
        called = True

    middleware = LoopbackOnlyMiddleware(downstream, ())
    scope = {
        "type": "http",
        "client": ("127.0.0.1", 4321),
        "headers": [(b"host", b"attacker.example")],
    }
    sent = []

    async def send(event):
        sent.append(event)

    await middleware(scope, asyncio.Queue().get, send)
    assert not called
    assert sent[0]["status"] == 403
