"""Real API and executor regressions with temporary projects and persistence."""

import asyncio
import os
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.context_builder import ContextBuildOptions
from services.agent_api.app.main import app
from services.agent_api.app.project_runtime import runtime_for


def projects(client, tmp_path):
    identifiers = []
    for name in ("a", "b"):
        root = tmp_path / name
        root.mkdir()
        (root / "AGENTS.md").write_text(f"Instructions for project {name}", encoding="utf-8")
        identifiers.append(client.post("/api/v1/projects", json={"name": name, "root": str(root)}).json()["project_id"])
    return identifiers


def test_project_memory_context_crud_and_events_are_isolated(tmp_path):
    with TestClient(app) as client:
        a, b = projects(client, tmp_path)
        personal = client.post("/api/v1/memories", json={"memory_type": "note", "content": "private personal context"}).json()
        created = client.post(f"/api/v1/memories?project_id={a}", json={"memory_type": "note", "content": "private project alpha context"}).json()
        assert client.get(f"/api/v1/memories?project_id={b}").json() == []
        assert client.put(f"/api/v1/memories/{created['id']}?project_id={b}", json={"memory_type": "note", "content": "overwrite"}).status_code == 404
        assert client.delete(f"/api/v1/memories/{created['id']}?project_id={b}").status_code == 404
        assert client.get("/api/v1/memories").json() == [personal]
        context = runtime_for(app, b).context.build(ContextBuildOptions(task_text="private context", memory_ids=[created["id"]]))
        assert context.context.memories == []
        encoded = context.model_dump_json()
        assert "private project alpha" not in encoded and "private personal" not in encoded
        assert "Instructions for project b" in encoded
        client.post("/api/v1/tools/execute", json={"name": "echo", "arguments": {"message": "test"}, "project_id": a})
        assert client.get(f"/api/v1/events?project_id={b}").json() == []
        assert client.get("/api/v1/events").json() == []
        assert client.get(f"/api/v1/events?project_id={a}").json()[0]["project_id"] == a


def test_project_task_cannot_resume_or_consume_from_other_scope(tmp_path):
    with TestClient(app) as client:
        a, b = projects(client, tmp_path)
        task = client.post("/api/v1/tasks", json={"project_id": a, "request": 'tool:write_file {"path":"note.txt","content":"alpha"}'}).json()
        assert task["status"] == "waiting_for_approval"
        assert client.get("/api/v1/tasks").json() == []
        assert client.get(f"/api/v1/tasks?project_id={b}").json() == []
        assert len(client.get(f"/api/v1/tasks?project_id={a}").json()) == 1
        assert client.post(f"/api/v1/tasks/{task['task_id']}/resume?project_id={b}").status_code == 404
        steps = client.get(f"/api/v1/tasks/{task['task_id']}/steps?project_id={a}").json()
        approval_id = steps[0]["approval_id"]
        assert client.post(f"/api/v1/approvals/{approval_id}/approve?project_id={b}").status_code == 404
        assert client.post(f"/api/v1/approvals/{approval_id}/approve?project_id={a}").status_code == 200
        assert client.post(f"/api/v1/tools/execute/{approval_id}/continue?project_id={b}").status_code == 404
        assert client.post(f"/api/v1/tasks/{task['task_id']}/resume?project_id={a}").json()["status"] == "completed"
        assert (tmp_path / "a" / "note.txt").read_text() == "alpha"
        assert not (tmp_path / "b" / "note.txt").exists()
        assert client.get("/api/v1/events").json() == []
        assert client.get(f"/api/v1/events?project_id={b}").json() == []
        events = client.get(f"/api/v1/events?project_id={a}").json()
        assert {item["type"] for item in events} >= {"task.created", "task.completed", "approval.approved"}
        assert all(item["task_id"] == task["task_id"] for item in events)


def test_policy_revision_invalidates_grant_and_blocks_initial_low_risk_dispatch(tmp_path):
    with TestClient(app) as client:
        result = client.post("/api/v1/tools/execute", json={"name": "write_file", "arguments": {"path": "note.txt", "content": "test"}}).json()
        approval_id = result["approval"]["request_id"]
        client.post(f"/api/v1/approvals/{approval_id}/approve")
        before = client.get("/api/v1/policy").json()["revision"]
        assert client.put("/api/v1/policy", json={"require_read_approval": True}).json()["revision"] == before + 1
        assert client.post(f"/api/v1/tools/execute/{approval_id}/continue").json()["status"] == "invalid_approval"
        assert client.post("/api/v1/tools/execute", json={"name": "echo", "arguments": {"message": "test"}}).json()["status"] == "approval_required"
        client.put("/api/v1/policy", json={"blocked_categories": ["read_only"]})
        assert client.post("/api/v1/tools/execute", json={"name": "echo", "arguments": {"message": "test"}}).json()["status"] == "denied"


def test_all_repository_initializers_share_versioned_schema(tmp_path):
    from services.agent_api.app.memory import MemoryRepository
    from services.agent_api.app.migrations import MIGRATIONS
    from services.agent_api.app.persistence import Phase5Store
    path = tmp_path / "versioned.sqlite"
    MemoryRepository(path).initialize()
    Phase5Store(path).initialize()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT version FROM schema_versions ORDER BY version").fetchall() == [(number,) for number in MIGRATIONS]
        assert db.execute("SELECT COUNT(*) FROM migration_checksums").fetchone()[0] == len(MIGRATIONS)


@pytest.mark.anyio
@pytest.mark.parametrize("blocked", [False, True])
async def test_durable_policy_change_at_actual_dispatch(tmp_path, monkeypatch, blocked):
    from services.agent_api.app.action_policy import PolicyUpdate
    from services.agent_api.app.memory import MemoryRepository
    from services.agent_api.app.permissions import RiskLevel
    from services.agent_api.app.tools import EmptyInput, Tool, ToolExecutor, build_registry

    memory = MemoryRepository(tmp_path / "race.sqlite")
    memory.initialize()
    registry = build_registry(tmp_path / "workspace")
    executed = []

    async def handler(_):
        executed.append(True)
        return {}

    registry.register(Tool("effect", "fixture", EmptyInput, RiskLevel.LOW, 2, handler))
    executor = ToolExecutor(registry, memory)
    original = asyncio.wait_for

    async def boundary(awaitable, timeout):
        executor.policy.update(PolicyUpdate(blocked_categories=["read_only"] if blocked else [], require_read_approval=True))
        return await original(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", boundary)
    result = await executor.execute("effect", {})
    assert result["status"] == ("denied" if blocked else "approval_required")
    assert executed == []


def test_registered_project_root_change_invalidates_existing_approval(tmp_path):
    with TestClient(app) as client:
        a, _ = projects(client, tmp_path)
        task = client.post("/api/v1/tasks", json={"project_id": a, "request": 'tool:write_file {"path":"note.txt","content":"alpha"}'}).json()
        approval = client.get(f"/api/v1/approvals?project_id={a}").json()[0]["request_id"]
        client.post(f"/api/v1/approvals/{approval}/approve?project_id={a}")
        with closing(sqlite3.connect(app.state.settings.database_path)) as db, db:
            # Simulate a changed registry root while an old exact grant exists.
            db.execute("DELETE FROM projects WHERE root=?", (os.path.normcase(str(tmp_path / "b")),))
            db.execute("UPDATE projects SET root=? WHERE project_id=?", (os.path.normcase(str(tmp_path / "b")), a))
        result = client.post(f"/api/v1/tasks/{task['task_id']}/resume?project_id={a}").json()
        assert result["status"] == "failed"
        steps = client.get(f"/api/v1/tasks/{task['task_id']}/steps?project_id={a}").json()
        assert steps[0]["result"]["status"] == "invalid_approval"
        assert not (tmp_path / "a" / "note.txt").exists()
        assert not (tmp_path / "b" / "note.txt").exists()


def test_actual_planner_receives_only_selected_project_memories(tmp_path):
    captured = []

    class InspectBrain:
        async def plan(self, request):
            captured.append(request)
            return {"kind": "action", "summary": "fixture", "action": {"tool_name": "echo", "arguments": {"message": "fixture"}}}

    with TestClient(app) as client:
        a, b = projects(client, tmp_path)
        client.post("/api/v1/memories", json={"memory_type": "note", "content": "personal private note"})
        first = client.post(f"/api/v1/memories?project_id={a}", json={"memory_type": "note", "content": "alpha private note"}).json()
        selected = client.post(f"/api/v1/memories?project_id={b}", json={"memory_type": "note", "content": "beta selected note"}).json()
        client.post(f"/api/v1/memories?project_id={b}", json={"memory_type": "note", "content": "beta unrelated note"})
        app.state.brain_provider = InspectBrain()
        app.state.project_runtimes.clear()
        result = client.post("/api/v1/tasks", json={"project_id": b, "request": "echo fixture", "memory_ids": [first["id"], selected["id"]]})
        assert result.json()["status"] == "completed"
        assert len(captured) == 1
        encoded = captured[0].model_dump_json()
        assert "beta selected note" in encoded
        assert "alpha private note" not in encoded and "personal private note" not in encoded
        assert "beta unrelated note" not in encoded
        assert captured[0].context.metadata["project_id"] == b


def test_expiring_project_approval_does_not_leak_events_into_personal_scope(tmp_path):
    with TestClient(app) as client:
        a, b = projects(client, tmp_path)
        result = client.post("/api/v1/tools/execute", json={"name": "write_file", "arguments": {"path": "note.txt", "content": "fixture"}, "project_id": a}).json()
        approval = result["approval"]["request_id"]
        with closing(sqlite3.connect(app.state.settings.database_path)) as db, db:
            db.execute("UPDATE approvals SET expires_at='2000-01-01T00:00:00+00:00' WHERE request_id=?", (approval,))
        assert client.get(f"/api/v1/approvals?project_id={b}").json() == []
        assert client.get("/api/v1/events").json() == []
        assert client.get(f"/api/v1/events?project_id={b}").json() == []
        assert "approval.expired" in {item["type"] for item in client.get(f"/api/v1/events?project_id={a}").json()}


def test_restart_recovery_events_keep_their_registered_project_scope(tmp_path):
    with TestClient(app) as client:
        a, _ = projects(client, tmp_path)
        task = client.post("/api/v1/tasks", json={"project_id": a, "request": "tool:missing {}"}).json()
        with closing(sqlite3.connect(app.state.settings.database_path)) as db, db:
            db.execute("UPDATE tasks SET status='running' WHERE task_id=?", (task["task_id"],))
        # Startup recovery uses the personal repository without an active request context.
        assert app.state.memory.recover_interrupted_tasks() == [task["task_id"]]
        assert client.get("/api/v1/events").json() == []
        events = client.get(f"/api/v1/events?project_id={a}").json()
        assert events[-1]["type"] == "task.failed"
        assert events[-1]["task_id"] == task["task_id"]
