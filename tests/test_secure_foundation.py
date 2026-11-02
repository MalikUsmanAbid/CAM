"""Foundation regressions: all persistence is owned by pytest temporary paths."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

import httpx
import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.main import app
from services.agent_api.app.mcp import _input_model_from_schema
from services.agent_api.app.memory import MemoryRepository
from services.agent_api.app.providers import OpenAICompatibleHTTPTransport, ProviderUnavailableError
from services.agent_api.app.secure_store import SecureStore
from services.agent_api.app.session_security import SessionManager, execution_session
from services.agent_api.app.tools import ToolExecutor, build_registry
from services.agent_api.app.usage_policy import CloudPolicy, UsagePolicy, budget_task


def test_mcp_property_cannot_shadow_optional_field_metadata():
    model = _input_model_from_schema("collision", {
        "type": "object", "additionalProperties": False,
        "properties": {
            "omitted_fields": {"type": "integer", "minimum": 1, "maximum": 3},
            "note": {"type": "string"},
        }, "required": ["omitted_fields"],
    })
    assert model.model_validate({"omitted_fields": 2}).model_dump() == {"omitted_fields": 2}
    assert model.model_validate({"omitted_fields": 2, "note": "ok"}).model_dump() == {
        "omitted_fields": 2, "note": "ok",
    }
    with pytest.raises(ValueError):
        model.model_validate({"omitted_fields": 4})


@pytest.mark.parametrize("field", ["model_fields_set", "model_fields", "model_dump", "model_json_schema"])
def test_mcp_reserved_model_members_are_explicitly_unsupported(field):
    with pytest.raises(ValueError, match="reserved"):
        _input_model_from_schema("reserved", {
            "type": "object", "additionalProperties": False,
            "properties": {field: {"type": "integer"}}, "required": [field],
        })


@pytest.mark.unpaired
def test_session_denies_unauthorized_actions_and_consumes_pairing_code():
    with TestClient(app) as client:
        assert client.get("/api/v1/health").json()["authentication_required"]
        for path in ["/api/v1/memories", "/api/v1/tools", "/api/v1/tasks", "/docs"]:
            assert client.get(path).status_code == 401
        assert client.post("/api/v1/tools/execute", json={"name": "echo"}).status_code == 401
        denied = client.get("/api/v1/tasks", headers={"Origin": "http://127.0.0.1:5173"})
        assert denied.status_code == 401
        assert denied.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
        code = app.state.sessions.pairing_code
        assert client.post("/api/v1/session/pair", json={"code": "incorrect"}).status_code == 401
        assert client.post("/api/v1/session/pair", json={"code": "غلط"}).status_code == 401
        paired = client.post("/api/v1/session/pair", json={"code": code})
        assert paired.headers["cache-control"] == "no-store"
        token = paired.json()["token"]
        assert client.post("/api/v1/session/pair", json={"code": code}).status_code == 401
        assert client.get("/api/v1/memories?token=" + token).status_code == 401
        client.headers["Authorization"] = "Bearer " + token
        assert client.get("/api/v1/memories").status_code == 200
        assert client.delete("/api/v1/session").status_code == 200
        assert client.get("/api/v1/memories").status_code == 401


def test_sessions_expire_and_restart_invalidates_tokens(monkeypatch):
    manager = SessionManager()
    token = manager.pair(manager.pairing_code)
    assert manager.authenticate(token)
    assert SessionManager().authenticate(token) is None
    monkeypatch.setattr("services.agent_api.app.session_security.time.monotonic", lambda: float("inf"))
    assert manager.authenticate(token) is None


@pytest.mark.anyio
@pytest.mark.parametrize("changed", ["session", "policy"])
async def test_approval_cannot_continue_in_different_session_or_policy(tmp_path, monkeypatch, changed):
    memory = MemoryRepository(tmp_path / "approval.sqlite")
    memory.initialize()
    executor = ToolExecutor(build_registry(tmp_path / "workspace"), memory)
    first = execution_session.set("session-a")
    try:
        result = await executor.execute("write_file", {"path": "note.txt", "content": "test"})
        approval_id = result["approval"]["request_id"]
        memory.decide_approval(approval_id, True)
    finally:
        execution_session.reset(first)
    if changed == "policy":
        monkeypatch.setattr("services.agent_api.app.session_security.POLICY_VERSION", "local-v2")
    second = execution_session.set("session-b" if changed == "session" else "session-a")
    try:
        denied = await executor.continue_approved(approval_id)
        assert denied["status"] == "invalid_approval"
        assert not (tmp_path / "workspace" / "note.txt").exists()
    finally:
        execution_session.reset(second)


def test_migration_requires_consent_backs_up_and_preserves_legacy_data(tmp_path):
    path = tmp_path / "legacy.sqlite"
    memory = MemoryRepository(path)
    from services.agent_api.app.legacy_schema import LEGACY_SQL
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(LEGACY_SQL)
    item = memory.create("note", "existing user note", None, None)
    store = SecureStore(path)
    with pytest.raises(RuntimeError, match="authorization"):
        store.migrate()
    with closing(sqlite3.connect(path)) as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name='schema_versions'").fetchall()
    store.migrate(allow_existing=True)
    store.migrate()
    assert memory.get(item.id).content == "existing user note"
    backups = list((tmp_path / "migration_backups").glob("*.sqlite"))
    assert len(backups) == 1
    with closing(sqlite3.connect(backups[0])) as connection:
        assert connection.execute("SELECT content FROM memories").fetchone()[0] == "existing user note"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_project_registration_does_not_modify_project_and_events_replay(tmp_path):
    store = SecureStore(tmp_path / "foundation.sqlite")
    store.migrate()
    memory = MemoryRepository(store.path)
    memory.initialize()
    project = tmp_path / "project"
    project.mkdir()
    user_file = project / "untouched.txt"
    user_file.write_text("user edits")
    registered = store.register_project("Example", str(project))
    assert store.register_project("Alias", str(project / "."))["project_id"] == registered["project_id"]
    assert user_file.read_text() == "user edits"
    assert len(list(project.iterdir())) == 1
    memory.record_event("echo", "success", "token=sensitive-token-value")
    memory.record_event("echo", "success", "second event")
    first = store.events(0)
    assert len(first) == 2
    assert "sensitive-token-value" not in str(first)
    assert store.events(first[0]["id"]) == first[1:]
    assert store.events(first[-1]["id"]) == []


def test_validation_response_never_echoes_secret_input():
    with TestClient(app) as client:
        result = client.post("/api/v1/session/pair", json={"code": "secret-marker-" * 20})
        assert result.status_code == 422
        assert "secret-marker" not in result.text


def cloud_policy(**changes):
    return CloudPolicy(endpoint="https://fixture.example/v1", model="fixture-model", enabled=True,
                       transmission_consent=True, daily_micro_usd=100000, task_micro_usd=100000,
                       input_micro_usd_per_million_tokens=1000000,
                       output_micro_usd_per_million_tokens=1000000, **changes)


@pytest.mark.anyio
async def test_cloud_dispatch_denies_missing_consent_and_rechecks_disabled_policy(tmp_path):
    store = SecureStore(tmp_path / "budget.sqlite")
    store.migrate()
    usage = UsagePolicy(store.path)
    seen = []
    async def transport_handler(request):
        seen.append(request)
        return httpx.Response(200, json={"choices": []})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport_handler)) as client:
        transport = OpenAICompatibleHTTPTransport("https://fixture.example/v1", "test-only-key", 2, client)
        transport.authorize_dispatch = usage.authorize
        payload = {"model": "fixture-model", "max_tokens": 1}
        with pytest.raises(ProviderUnavailableError, match="disabled"):
            await transport.post(payload)
        assert seen == []
        policy = cloud_policy()
        usage.configure(policy)
        await transport.post(payload)
        assert len(seen) == 1
        usage.configure(policy.model_copy(update={"enabled": False}))
        with pytest.raises(ProviderUnavailableError, match="disabled"):
            await transport.post(payload)
        assert len(seen) == 1
        assert usage.status()["reserved_today_micro_usd"] > 0


def test_unknown_pricing_never_counts_as_zero_cost():
    with pytest.raises(ValueError):
        CloudPolicy(endpoint="https://fixture.example", model="model", enabled=True,
                    transmission_consent=True, daily_micro_usd=100, task_micro_usd=100)


@pytest.mark.parametrize("gate", ["daily", "task"])
def test_competing_cloud_requests_cannot_overspend(tmp_path, gate):
    store = SecureStore(tmp_path / "budget.sqlite")
    store.migrate()
    usage = UsagePolicy(store.path)
    policy = cloud_policy().model_copy(update={"daily_micro_usd": 10000 if gate == "daily" else 100000,
                                               "task_micro_usd": 10000 if gate == "task" else 100000})
    usage.configure(policy)
    barrier = Barrier(2)
    def reserve():
        identity = budget_task.set("same-task")
        try:
            barrier.wait(timeout=10)
            usage.authorize(policy.endpoint, {"model": policy.model, "max_tokens": 1})
            return "accepted"
        except ProviderUnavailableError:
            return "denied"
        finally:
            budget_task.reset(identity)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reserve), pool.submit(reserve)]
        results = [future.result(timeout=15) for future in futures]
    assert sorted(results) == ["accepted", "denied"]
    assert 0 < usage.status()["reserved_today_micro_usd"] <= 10000


@pytest.mark.anyio
async def test_sse_replays_events_and_stops_after_session_revocation():
    from fastapi import Request

    from services.agent_api.app.main import stream_events
    with TestClient(app) as client:
        token = client.headers["Authorization"].removeprefix("Bearer ")
        app.state.memory.record_event("echo", "success", "first")
        app.state.memory.record_event("echo", "success", "second")
        request = Request({"type": "http", "app": app, "method": "GET", "path": "/api/v1/events/stream", "query_string": b"", "headers": [], "state": {"session_token": token}})
        async def connected():
            return False
        request.is_disconnected = connected
        response = await stream_events(request)
        first = await anext(response.body_iterator)
        assert "id: 1" in first and '"detail": "first"' in first
        app.state.sessions.revoke(token)
        with pytest.raises(StopAsyncIteration):
            await anext(response.body_iterator)


def test_authenticated_projects_and_event_cursor_api(tmp_path):
    project = tmp_path / "fixture"
    project.mkdir()
    with TestClient(app) as client:
        assert client.post("/api/v1/projects", json={"name": "Fixture", "root": str(project)}).status_code == 201
        assert len(client.get("/api/v1/projects").json()) == 1
        app.state.memory.record_event("echo", "success", "safe")
        assert client.get("/api/v1/events?after=0").json()[0]["id"] == 1
        assert client.get("/api/v1/events?after=1").json() == []
        assert client.get("/api/v1/events?after=-1").status_code == 400
        assert client.get("/api/v1/events/stream", headers={"Last-Event-ID": "bad"}).status_code == 400
        assert client.get("/api/v1/usage").json()["default_cloud_enabled"] is False


def test_failed_migration_rolls_back_ddl(monkeypatch, tmp_path):
    original = sqlite3.connect
    class FailingConnection(sqlite3.Connection):
        def execute(self, sql, *args):
            if "CREATE TABLE IF NOT EXISTS projects" in sql:
                raise sqlite3.OperationalError("injected migration failure")
            return super().execute(sql, *args)
    path = tmp_path / "rollback.sqlite"
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *args, **kwargs: original(*args, **kwargs, factory=FailingConnection))
        with pytest.raises(sqlite3.OperationalError, match="injected"):
            SecureStore(path).migrate()
    with closing(original(path)) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


def test_audit_redacts_structured_credentials_and_url_passwords(tmp_path):
    memory = MemoryRepository(tmp_path / "audit.sqlite")
    memory.initialize()
    memory.record_event("echo", "failed", '{"api_key":"secret-key","token":"secret-token"} https://name:secret-pass@example.com')
    with closing(sqlite3.connect(memory.database_path)) as connection:
        raw = connection.execute("SELECT detail FROM task_events").fetchone()[0]
    assert "secret-key" not in raw and "secret-token" not in raw and "secret-pass" not in raw
