"""Native DPAPI and dispatch revocation tests using disposable credential files."""

import os
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.config import _database_path, get_settings
from services.agent_api.app.credentials import CredentialVault
from services.agent_api.app.main import app
from services.agent_api.app.providers import ProviderUnavailableError, create_provider


@pytest.mark.skipif(os.name != "nt", reason="Live Windows DPAPI acceptance")
def test_native_credentials_round_trip_corruption_and_revoke(tmp_path):
    vault = CredentialVault(tmp_path / "credentials")
    secret = "temporary-fixture-credential"
    reference = vault.store(secret)
    blob = next(vault.directory.iterdir())
    assert secret.encode() not in blob.read_bytes()
    assert vault.resolve(reference) == secret
    blob.write_bytes(b"invalid dpapi data")
    with pytest.raises(ValueError, match="protection failed"):
        vault.resolve(reference)
    vault.revoke(reference)
    assert vault.resolve(reference) is None
    with pytest.raises(ValueError, match="reference"):
        vault.resolve("vault:../../outside")


@pytest.mark.anyio
@pytest.mark.skipif(os.name != "nt", reason="Live Windows DPAPI acceptance")
async def test_revoke_blocks_cached_provider_before_transport(tmp_path):
    vault = CredentialVault(tmp_path / "credentials")
    reference = vault.store("disposable-test-key")
    calls = []

    def response(request):
        calls.append(request.headers["Authorization"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "fixture"}}]})

    settings = replace(get_settings(), ai_provider="openai_compatible", ai_endpoint="https://fixture.example", ai_api_key_ref=reference)
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        provider = create_provider(settings, vault, client)
        await provider._transport.post({"model": "fixture"})
        vault.revoke(reference)
        with pytest.raises(ProviderUnavailableError, match="revoked"):
            await provider._transport.post({"model": "fixture"})
    assert calls == ["Bearer disposable-test-key"]


@pytest.mark.skipif(os.name != "nt", reason="Live Windows DPAPI acceptance")
def test_credential_api_returns_only_reference_and_excludes_audit(tmp_path):
    with TestClient(app) as client:
        secret = "temporary-fixture-key"
        result = client.post("/api/v1/credentials", json={"secret": secret})
        assert result.status_code == 201
        assert set(result.json()) == {"reference"}
        assert result.headers["cache-control"] == "no-store"
        assert secret not in client.get("/api/v1/activity").text
        reference = result.json()["reference"]
        assert client.delete(f"/api/v1/credentials/{reference}").status_code == 200
        assert app.state.secret_resolver.resolve(reference) is None


def test_session_pairing_rotates_without_invalidating_other_sessions():
    with TestClient(app) as client:
        old_token = client.headers["Authorization"]
        fresh = app.state.sessions.pairing_code
        other = client.post("/api/v1/session/pair", json={"code": fresh})
        assert other.status_code == 200
        assert client.post("/api/v1/session/pair", json={"code": fresh}).status_code == 401
        assert app.state.sessions.pairing_code != fresh
        assert client.get("/api/v1/projects", headers={"Authorization": old_token}).status_code == 200


def test_default_local_data_and_explicit_portable_data(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_PATH")
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert _database_path(tmp_path) == tmp_path / "local" / "USMANJARVIS" / "jarvis.db"
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "portable" / "data"))
    assert _database_path(tmp_path) == tmp_path / "portable" / "data" / "jarvis.db"


@pytest.mark.parametrize("location", [r"\\server\share\jarvis.db", "OneDrive/data/jarvis.db", "Dropbox/data/jarvis.db"])
def test_network_and_cloudsynced_database_paths_rejected(monkeypatch, tmp_path, location):
    monkeypatch.setenv("DATABASE_PATH", location if location.startswith("\\") else str(tmp_path / location))
    with pytest.raises(ValueError, match="unsupported"):
        _database_path(tmp_path)


@pytest.mark.anyio
async def test_session_revoked_at_dispatch_prevents_actual_handler(tmp_path, monkeypatch):
    import asyncio

    from services.agent_api.app.memory import MemoryRepository
    from services.agent_api.app.permissions import RiskLevel
    from services.agent_api.app.session_security import SessionManager, execution_authorized
    from services.agent_api.app.tools import EmptyInput, Tool, ToolExecutor, build_registry

    memory = MemoryRepository(tmp_path / "revoke.sqlite")
    memory.initialize()
    registry = build_registry(tmp_path / "workspace")
    manager = SessionManager()
    token = manager.pair(manager.pairing_code)
    called = []

    async def effect(_):
        called.append(True)
        return {}

    registry.register(Tool("effect", "fixture", EmptyInput, RiskLevel.LOW, 2, effect))
    executor = ToolExecutor(registry, memory)
    original = asyncio.wait_for

    async def boundary(awaitable, timeout):
        manager.revoke(token)
        return await original(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", boundary)
    validity = execution_authorized.set(lambda: manager.authenticate(token) is not None)
    try:
        assert (await executor.execute("effect", {}))["status"] == "denied"
    finally:
        execution_authorized.reset(validity)
    assert called == []
