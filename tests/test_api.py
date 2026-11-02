from pathlib import Path

from fastapi.testclient import TestClient

from services.agent_api.app.main import app


def test_health_reports_mock_provider(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "mock")
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["provider"] == "mock"


def test_chat_returns_mock_response(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "mock")
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json={"message": "Hello JARVIS"})
    assert response.status_code == 200
    assert "Hello JARVIS" in response.json()["message"]
    assert response.json()["provider"] == "mock"


def test_chat_rejects_empty_message(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "mock")
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json={"message": "   "})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_unknown_provider_is_safe_unavailable_error(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "openai")
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json={"message": "Hello"})
    assert response.status_code == 503
    assert response.json()["code"] == "provider_unavailable"


def test_unavailable_configured_chat_provider_returns_explicit_deterministic_fallback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AI_PROVIDER", "openai_compatible")
    monkeypatch.delenv("AI_ENDPOINT", raising=False)
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("AI_API_KEY_REF", raising=False)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "jarvis.db"))
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json={"message": "Hello"})
    assert response.status_code == 200
    assert response.json()["provider"] == "deterministic_fallback"
    assert "did not run a model" in response.json()["message"]


def test_phase_two_tool_activity_and_memory_api(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AI_PROVIDER", "mock")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "jarvis.db"))
    monkeypatch.setenv("SAFE_WORKSPACE", str(tmp_path / "workspace"))
    with TestClient(app) as client:
        tools = client.get("/api/v1/tools")
        assert tools.status_code == 200
        assert {
            "echo",
            "list_directory",
            "read_file",
            "write_file",
            "create_directory",
            "system_time",
        } <= {tool["name"] for tool in tools.json()}
        executed = client.post(
            "/api/v1/tools/execute", json={"name": "echo", "arguments": {"message": "safe"}}
        )
        assert executed.json()["status"] == "success"
        assert client.get("/api/v1/activity").json()[0]["tool_name"] == "echo"
        memory = client.post(
            "/api/v1/memories",
            json={"memory_type": "note", "content": "Keep this safe"},
        )
        assert memory.status_code == 200
        assert client.get("/api/v1/memories?query=safe").json()[0]["content"] == "Keep this safe"
        rejected = client.post(
            "/api/v1/memories",
            json={"memory_type": "note", "content": "password=not-safe"},
        )
        assert rejected.status_code == 400


def test_task_approval_resume_executes_once_and_records_result(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AI_PROVIDER", "mock")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "jarvis.db"))
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("SAFE_WORKSPACE", str(workspace))
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/tasks",
            json={"request": 'tool:write_file {"path":"note.txt","content":"safe note"}'},
        )
        assert created.status_code == 201
        task = created.json()
        assert task["status"] == "waiting_for_approval"

        steps = client.get(f"/api/v1/tasks/{task['task_id']}/steps").json()
        approval_id = steps[0]["approval_id"]
        assert steps[0]["status"] == "approval_required"
        assert client.post(f"/api/v1/approvals/{approval_id}/approve").json()["status"] == "approved"

        completed = client.post(f"/api/v1/tasks/{task['task_id']}/resume").json()
        assert completed["status"] == "completed"
        assert (workspace / "note.txt").read_text(encoding="utf-8") == "safe note"
        assert client.get(f"/api/v1/tasks/{task['task_id']}/steps").json()[0]["status"] == "success"

        replay = client.post(f"/api/v1/tasks/{task['task_id']}/resume").json()
        assert replay["status"] == "completed"
        assert [event["status"] for event in client.get("/api/v1/activity").json()].count(
            "tool_execution_succeeded"
        ) == 1


def test_phase5_workflows_and_schedules_use_durable_store(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AI_PROVIDER", "mock")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "jarvis.db"))
    monkeypatch.setenv("SAFE_WORKSPACE", str(tmp_path / "workspace"))
    with TestClient(app) as client:
        workflow = client.post(
            "/api/v1/workflows",
            json={"kind": "research", "idempotency_key": "request-1"},
        ).json()
        duplicate = client.post(
            "/api/v1/workflows",
            json={"kind": "research", "idempotency_key": "request-1"},
        ).json()
        assert duplicate["id"] == workflow["id"]
        assert client.get("/api/v1/workflows").json()[0]["id"] == workflow["id"]
        changed = client.post(
            f"/api/v1/workflows/{workflow['id']}/transition",
            json={"status": "running"},
        )
        assert changed.status_code == 200 and changed.json()["status"] == "running"

        schedule = client.post(
            "/api/v1/schedules",
            json={
                "task_id": "task-1",
                "schedule_type": "weekly",
                "next_run": "2026-10-10T09:00:00+05:00",
                "retry_limit": 1,
            },
        ).json()
        assert client.get("/api/v1/schedules").json()[0]["id"] == schedule["schedule_id"]
        assert client.delete(f"/api/v1/schedules/{schedule['schedule_id']}").status_code == 204
