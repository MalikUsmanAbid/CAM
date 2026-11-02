"""Independent-review reproductions: atomic scope, expiry, schemas and Windows cleanup."""

import asyncio
import gc
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.main import app
from services.agent_api.app.memory import MemoryRepository
from services.agent_api.app.migrations import initialize_database
from services.agent_api.app.persistence import Phase5Store
from services.agent_api.app.tools import ToolExecutor, build_registry


@pytest.mark.parametrize("boundary", ["task_scopes", "task_inputs", "event_scopes"])
def test_project_task_interruption_rolls_back_task_inputs_and_initial_event(tmp_path, monkeypatch, boundary):
    project = tmp_path / "project"
    project.mkdir()
    with TestClient(app, raise_server_exceptions=False) as client:
        project_id = client.post("/api/v1/projects", json={"name": "fixture", "root": str(project)}).json()["project_id"]
        original = sqlite3.connect

        class InterruptedConnection(sqlite3.Connection):
            def execute(self, sql, *arguments):
                if sql.startswith("INSERT INTO " + boundary):
                    raise sqlite3.OperationalError("injected transaction interruption")
                return super().execute(sql, *arguments)

        with monkeypatch.context() as patch:
            patch.setattr(sqlite3, "connect", lambda *args, **kwargs: original(*args, **kwargs, factory=InterruptedConnection))
            result = client.post("/api/v1/tasks", json={"project_id": project_id, "request": "private project fixture request", "memory_ids": [1]})
            assert result.status_code == 500
        assert client.get("/api/v1/tasks").json() == []
        assert client.get(f"/api/v1/tasks?project_id={project_id}").json() == []
        assert client.get("/api/v1/events").json() == []
        assert client.get(f"/api/v1/events?project_id={project_id}").json() == []
        with closing(original(app.state.settings.database_path)) as db:
            for table in ("tasks", "task_scopes", "task_inputs", "task_events", "event_scopes"):
                assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("boundary", ["dispatch", "effect"])
async def test_consumed_approval_expiry_prevents_handler_or_enforceable_file_effect(tmp_path, monkeypatch, boundary):
    memory = MemoryRepository(tmp_path / "expiry.sqlite")
    memory.initialize()
    registry = build_registry(tmp_path / "workspace")
    executor = ToolExecutor(registry, memory)
    clock = [datetime.now(UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr("services.agent_api.app.tools.datetime", Clock)
    calls = []
    original_handler = registry.get("write_file").handler
    expired_time = None

    async def tracked_handler(value):
        calls.append(True)
        if boundary == "effect":
            clock[0] = expired_time
        return await original_handler(value)

    registry.get("write_file").handler = tracked_handler
    pending = await executor.execute("write_file", {"path": "denied.txt", "content": "fixture"})
    identifier = pending["approval"]["request_id"]
    approval = memory.decide_approval(identifier, True)
    expired_time = datetime.fromisoformat(approval.expires_at) + timedelta(microseconds=1)
    original_wait = asyncio.wait_for

    async def dispatch(awaitable, timeout):
        assert memory.get_approval(identifier).status == "consumed"
        if boundary == "dispatch":
            clock[0] = expired_time
        return await original_wait(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", dispatch)
    result = await executor.continue_approved(identifier)
    assert result["status"] == "invalid_approval"
    assert calls == ([] if boundary == "dispatch" else [True])
    assert not (tmp_path / "workspace" / "denied.txt").exists()


def test_additional_unsupported_schema_columns_fail_without_destroying_rows(tmp_path):
    path = tmp_path / "extra.sqlite"
    memory = MemoryRepository(path)
    memory.initialize()
    record = memory.create("note", "existing user data", None, None)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("ALTER TABLE memories ADD COLUMN extra TEXT")
        db.execute("UPDATE memories SET extra='preserve this field'")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="incompatible"):
        initialize_database(path, allow_existing=True)
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT content,extra FROM memories WHERE id=?", (record.id,)).fetchone() == ("existing user data", "preserve this field")


def test_reordered_scope_columns_are_rejected_without_reclassifying_records(tmp_path):
    path = tmp_path / "reordered.sqlite"
    initialize_database(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE task_scopes")
        db.execute("CREATE TABLE task_scopes(project_id TEXT NOT NULL,task_id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO task_scopes(project_id,task_id) VALUES('project-fixture','task-fixture')")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="incompatible"):
        initialize_database(path, allow_existing=True)
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT project_id,task_id FROM task_scopes").fetchone() == ("project-fixture", "task-fixture")


@pytest.mark.skipif(os.name != "nt", reason="Windows file-handle cleanup acceptance")
@pytest.mark.parametrize("repository", ["memory", "legacy_store"])
def test_repository_handles_close_without_garbage_collection(tmp_path, repository):
    path = tmp_path / "cleanup.sqlite"
    enabled = gc.isenabled()
    gc.disable()
    try:
        if repository == "memory":
            memory = MemoryRepository(path)
            memory.initialize()
            item = memory.create("note", "fixture", None, None)
            assert memory.get(item.id).content == "fixture"
            memory.record_event("echo", "success", "fixture")
        else:
            store = Phase5Store(path)
            store.initialize()
            store.create_workflow("fixture", "fixture-key", None)
            assert len(store.list_workflows()) == 1
        # Windows refuses this operation while a SQLite handle remains open.
        path.unlink()
        assert not path.exists()
    finally:
        if enabled:
            gc.enable()
