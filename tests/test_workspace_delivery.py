"""M4–M9 regression source. Execution deferred by the implementation request."""

import asyncio
import base64
import hashlib
import io
import json
import sqlite3
import uuid
import wave
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser

import httpx
import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.account_actions import (
    AccountActions,
    AccountInput,
    CalendarInput,
    SendInput,
)
from services.agent_api.app.action_policy import ActionCategory, ActionPolicy
from services.agent_api.app.agent_workspace import AgentJobInput, AgentWorkspace
from services.agent_api.app.backup_bundle import create_bundle, restore_bundle
from services.agent_api.app.main import app
from services.agent_api.app.migrations import initialize_database
from services.agent_api.app.project_runtime import runtime_for
from services.agent_api.app.session_security import execution_session
from services.agent_api.app.voice_capture import CaptureInput, VoiceCapture
from services.agent_api.app.workspace_records import RecordInput, ScheduleInput, WorkspaceRecords


def storage(tmp_path):
    path = tmp_path / "isolated.sqlite"
    initialize_database(path)
    return path, WorkspaceRecords(path)


def test_record_scopes_and_stale_edits(tmp_path):
    _, records = storage(tmp_path)
    value = RecordInput(title="Lead", content="A draft", kind="lead")
    created = records.save_record("A", value)
    assert records.list_records("B") == []
    with pytest.raises(ValueError):
        records.save_record("B", value, created["record_id"], 1)
    updated = records.save_record("A", value, created["record_id"], 1)
    assert updated["revision"] == 2
    with pytest.raises(ValueError):
        records.save_record("A", value, created["record_id"], 1)


def test_question_answer_binds_exact_scope_and_only_answers_once(tmp_path):
    _, records = storage(tmp_path)
    identifier = records.ask("A", "Which option?")
    with pytest.raises(ValueError):
        records.answer("B", identifier, "Yes")
    records.answer("A", identifier, "Use the first option")
    with pytest.raises(ValueError):
        records.answer("A", identifier, "Change the answer")
    assert records.decisions("A")[0]["answer"] == "Use the first option"


def test_briefing_is_scoped_bounded_and_does_not_execute_work(tmp_path):
    _, records = storage(tmp_path)
    records.ask("A", "Project A question")
    records.ask("B", "Private B question")
    records.save_record("B", RecordInput(title="Private B lead", kind="lead"))
    start = datetime(2030, 1, 2, tzinfo=UTC)
    identifier = records.schedule("A", ScheduleInput(title="Due A reminder", due_at=start))
    records.materialize_due(start)
    result = records.briefing("A", start, start + timedelta(days=1))
    assert [item["question"] for item in result["questions"]] == ["Project A question"]
    assert [item["title"] for item in result["due_notifications"]] == ["Due A reminder"]
    assert result["open_work"] == []
    assert "Private B" not in json.dumps(result)
    assert records.occurrences("A")[0]["status"] == "pending"
    assert records.schedules("A")[0]["schedule_id"] == identifier
    with pytest.raises(ValueError):
        records.briefing("A", start, start + timedelta(days=3))


def test_scheduler_restart_duplicate_ticks_and_missed_occurrences(tmp_path):
    database, records = storage(tmp_path)
    instant = datetime(2030, 1, 2, tzinfo=UTC)
    identifier = records.schedule(
        "A",
        ScheduleInput(title="Reminder", due_at=instant - timedelta(days=2), interval_seconds=60),
    )
    assert len(records.materialize_due(instant)) == 1
    reopened = WorkspaceRecords(database)
    assert reopened.materialize_due(instant) == []
    assert len(reopened.occurrences("A")) == 1
    assert datetime.fromisoformat(reopened.schedules("A")[0]["next_due"]) > instant
    reopened.cancel_schedule("A", identifier)
    assert reopened.occurrences("A")[0]["status"] == "cancelled"
    assert reopened.materialize_due(instant + timedelta(days=2)) == []


def test_emergency_stop_persists_and_preserves_user_policy(tmp_path):
    database, _ = storage(tmp_path)
    action_policy = ActionPolicy(database)
    initial = action_policy.snapshot()["revision"]
    action_policy.emergency_stop(True)
    assert ActionPolicy(database).denied(ActionCategory.READ)
    assert not action_policy.allowed(ActionCategory.PUBLISH, approved=True)
    action_policy.emergency_stop(False)
    assert action_policy.snapshot()["revision"] == initial + 2
    assert action_policy.allowed(ActionCategory.READ)


def test_agent_run_uses_the_requested_executor_and_dependencies(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    calls = []
    with TestClient(app) as client:
        identifier = client.post(
            "/api/v1/projects", json={"name": "Fixture", "root": str(project)}
        ).json()["project_id"]
        runtime = runtime_for(app, identifier)

        async def actual_handler(value):
            calls.append(value.message)
            return {"message": "Executed role handler", "provider": "fixture", "model": "fixture"}

        runtime.executor.registry.get("studio_project_chat").handler = actual_handler
        endpoint = "/api/v1/workspace/agents?project_id=" + identifier
        parent = client.post(
            endpoint, json={"role": "coordinator", "arguments": {"message": "Inspect plan"}}
        ).json()
        child = client.post(
            endpoint,
            json={
                "role": "researcher",
                "arguments": {"message": "Assess parent"},
                "dependencies": [parent["job_id"]],
            },
        ).json()
        assert (
            client.post(
                f"/api/v1/workspace/agents/{child['job_id']}/run?project_id={identifier}"
            ).status_code
            == 400
        )
        response = client.post(
            f"/api/v1/workspace/agents/{parent['job_id']}/run?project_id={identifier}"
        )
        assert response.json()["status"] == "completed"
        assert len(calls) == 1 and "Inspect plan" in calls[0]
        assert (
            client.post(
                f"/api/v1/workspace/agents/{parent['job_id']}/run?project_id={identifier}"
            ).status_code
            == 400
        )
        assert len(calls) == 1
        assert (
            client.post(
                f"/api/v1/workspace/agents/{child['job_id']}/run?project_id={identifier}"
            ).json()["status"]
            == "completed"
        )
        assert len(calls) == 2 and "Executed role handler" in calls[1]


def test_knowledge_import_requires_approval_exact_hash_and_project_scope(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir()
    b.mkdir()
    content = "Fixture source with a useful reference.\n"
    (a / "notes.md").write_bytes(content.encode())
    with TestClient(app) as client:
        projects = [
            client.post("/api/v1/projects", json={"name": path.name, "root": str(path)}).json()[
                "project_id"
            ]
            for path in (a, b)
        ]
        endpoint = "/api/v1/workspace/knowledge/execute?project_id="
        proposal = client.post(
            endpoint + projects[0],
            json={
                "tool": "knowledge_import",
                "arguments": {
                    "path": "notes.md",
                    "title": "Notes",
                    "source_sha256": hashlib.sha256(content.encode()).hexdigest(),
                },
            },
        ).json()
        assert proposal["status"] == "approval_required"
        grant = proposal["approval"]["request_id"]
        assert (
            client.post(f"/api/v1/approvals/{grant}/approve?project_id={projects[0]}").status_code
            == 200
        )
        result = client.post(
            f"/api/v1/tools/execute/{grant}/continue?project_id={projects[0]}"
        ).json()
        assert result["status"] == "success"
        matches = client.post(
            endpoint + projects[1],
            json={"tool": "knowledge_search", "arguments": {"query": "useful"}},
        ).json()
        assert matches["result"]["matches"] == []
        matches = client.post(
            endpoint + projects[0],
            json={"tool": "knowledge_search", "arguments": {"query": "useful"}},
        ).json()
        assert (
            matches["result"]["matches"][0]["source_sha"]
            == hashlib.sha256(content.encode()).hexdigest()
        )


def test_backup_preserves_database_refuses_overwrite_and_restores_only_new_directory(tmp_path):
    database, records = storage(tmp_path)
    records.save_record("A", RecordInput(title="Preserved draft", kind="content_draft"))
    original = database.read_bytes()
    bundle = tmp_path / "backup.jarvis"
    create_bundle(database, bundle)
    assert database.read_bytes() == original
    with pytest.raises(ValueError):
        create_bundle(database, bundle)
    restored = restore_bundle(bundle, tmp_path / "restored")
    assert (
        WorkspaceRecords(restored["database_path"]).list_records("A")[0]["title"]
        == "Preserved draft"
    )
    with pytest.raises(ValueError):
        restore_bundle(bundle, tmp_path / "restored")


def test_backup_rejects_unexpected_schema_without_creating_destination(tmp_path):
    database, _ = storage(tmp_path)
    with sqlite3.connect(database) as db:
        db.execute("CREATE VIEW unexpected AS SELECT 1")
    with pytest.raises(ValueError, match="schema"):
        create_bundle(database, tmp_path / "rejected.jarvis")
    assert not (tmp_path / "rejected.jarvis").exists()


@pytest.mark.anyio
async def test_email_transport_inspects_exact_payload_no_proxy_and_duplicate_denial(tmp_path):
    database, _ = storage(tmp_path)
    requests = []
    options = []

    class Vault:
        def resolve(self, reference):
            return "fixture-access-value"

    def transport(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "fixture"})

    def factory(**kwargs):
        options.append(kwargs)
        return httpx.AsyncClient(transport=httpx.MockTransport(transport), **kwargs)

    actions = AccountActions(database, Vault(), client_factory=factory)
    account = actions.connect(
        AccountInput(
            provider="google",
            label="Fixture",
            secret_reference="vault:" + "a" * 32,
            scopes=["https://www.googleapis.com/auth/gmail.send"],
            transmission_consent=True,
        )
    )
    value = SendInput(
        operation_id=uuid.uuid4().hex,
        account_id=account,
        account_revision=1,
        recipients=["receiver@example.com"],
        subject="Exact subject",
        body="Exact message",
    )
    assert (await actions.dispatch(value))["status"] == "accepted"
    payload = json.loads(requests[0].content)
    message = BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(payload["raw"])
    )
    assert str(message["To"]) == "receiver@example.com"
    assert str(message["Subject"]) == "Exact subject"
    assert message.get_body().get_content().strip() == "Exact message"
    assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False
    with pytest.raises(ValueError, match="already dispatched"):
        await actions.dispatch(value)
    assert len(requests) == 1
    actions.revoke(account)
    with pytest.raises(ValueError, match="revoked"):
        await actions.dispatch(value.model_copy(update={"operation_id": uuid.uuid4().hex}))
    assert len(requests) == 1


def test_voice_capture_is_bound_to_session_and_never_stored_in_database(tmp_path):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\0\0" * 160)
    voice = VoiceCapture("http://127.0.0.1:8181/v1/audio/transcriptions", asyncio.Semaphore(1))
    session = execution_session.set("A")
    try:
        capture = voice.stage(buffer.getvalue())
        execution_session.set("B")
        voice.discard(capture["capture_id"])
        assert capture["capture_id"] in voice.captures
        value = CaptureInput(**capture)
        with pytest.raises(ValueError, match="another session"):
            asyncio.run(voice.transcribe(value))
        execution_session.set("A")
        voice.discard(capture["capture_id"])
        assert voice.captures == {}
    finally:
        execution_session.reset(session)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_synchronized_competing_agent_claims_execute_once(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from pydantic import Field

    from services.agent_api.app import agent_workspace
    from services.agent_api.app.memory import MemoryRepository
    from services.agent_api.app.permissions import RiskLevel
    from services.agent_api.app.tools import Tool, ToolExecutor, ToolInput, ToolRegistry

    database, _ = storage(tmp_path)
    root = tmp_path / "project"
    root.mkdir()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    class Message(ToolInput):
        message: str = Field(min_length=1, max_length=4000)

    async def handler(value):
        calls.append(value.message)
        entered.set()
        await release.wait()
        return {"message": "Real job handler executed", "provider": "fixture", "model": "fixture"}

    registry = ToolRegistry(root)
    registry.register(
        Tool("studio_project_chat", "Fixture role handler", Message, RiskLevel.LOW, 5, handler)
    )
    runtime = SimpleNamespace(
        root=root,
        executor=ToolExecutor(registry, MemoryRepository(database)),
        memory=MemoryRepository(database),
    )
    fixture = SimpleNamespace(
        state=SimpleNamespace(settings=SimpleNamespace(database_path=database))
    )
    monkeypatch.setattr(agent_workspace, "runtime_for", lambda app_, project_id: runtime)
    jobs = AgentWorkspace(fixture)
    job = jobs.create("A", AgentJobInput(role="coordinator", arguments={"message": "Inspect"}))
    first = asyncio.create_task(jobs.run("A", job["job_id"]))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        with pytest.raises(ValueError, match="already claimed"):
            await jobs.run("A", job["job_id"])
        assert len(calls) == 1
    finally:
        release.set()
        await first
    assert jobs.get("A", job["job_id"])["status"] == "completed"


def test_cancelled_agent_approval_cannot_bypass_job_via_generic_continue(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    calls = []
    with TestClient(app) as client:
        identifier = client.post(
            "/api/v1/projects", json={"name": "Fixture", "root": str(project)}
        ).json()["project_id"]
        runtime = runtime_for(app, identifier)

        async def handler(value):
            calls.append(value.message)
            return {"message": "Must not run after cancellation"}

        runtime.executor.registry.get("studio_project_chat").handler = handler
        client.put("/api/v1/policy", json={"blocked_categories": [], "require_read_approval": True})
        job = client.post(
            "/api/v1/workspace/agents?project_id=" + identifier,
            json={"role": "coordinator", "arguments": {"message": "Inspect"}},
        ).json()
        waiting = client.post(
            f"/api/v1/workspace/agents/{job['job_id']}/run?project_id={identifier}"
        ).json()
        grant = waiting["approval_id"]
        assert waiting["status"] == "waiting_for_approval"
        client.post(f"/api/v1/approvals/{grant}/approve?project_id={identifier}")
        client.post(f"/api/v1/workspace/agents/{job['job_id']}/cancel?project_id={identifier}")
        assert (
            client.post(
                f"/api/v1/tools/execute/{grant}/continue?project_id={identifier}"
            ).status_code
            == 400
        )
        assert calls == []


@pytest.mark.anyio
async def test_account_approval_preview_contains_exact_payload_and_no_credential(tmp_path):
    from services.agent_api.app.account_actions import register_account_tools
    from services.agent_api.app.memory import MemoryRepository
    from services.agent_api.app.tools import ToolExecutor, ToolRegistry

    database, _ = storage(tmp_path)

    class Vault:
        def resolve(self, reference):
            return "fixture-private-credential"

    actions = AccountActions(database, Vault())
    identifier = actions.connect(
        AccountInput(
            provider="microsoft",
            label="Fixture",
            secret_reference="vault:" + "a" * 32,
            scopes=["Mail.Send"],
            transmission_consent=True,
        )
    )
    root = tmp_path / "workspace"
    root.mkdir()
    registry = ToolRegistry(root)
    register_account_tools(registry, actions)
    outcome = await ToolExecutor(registry, MemoryRepository(database)).execute(
        "account_send_email",
        {
            "operation_id": uuid.uuid4().hex,
            "account_id": identifier,
            "account_revision": 1,
            "subject": "Approved subject",
            "body": "Approved body",
            "recipients": ["receiver@example.com"],
        },
    )
    assert outcome["status"] == "approval_required"
    preview = outcome["approval"]["preview"]
    assert preview["exact_payload"]["body"] == "Approved body"
    assert preview["exact_payload"]["recipients"] == ["receiver@example.com"]
    assert "fixture-private-credential" not in json.dumps(preview)


@pytest.mark.anyio
@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_calendar_transport_uses_exact_times_attendees_and_scope(tmp_path, provider):
    database, _ = storage(tmp_path)
    calls = []

    class Vault:
        def resolve(self, reference):
            return "fixture-access-value"

    def transport(request):
        calls.append(request)
        return httpx.Response(201, json={"id": "fixture-event"})

    actions = AccountActions(
        database,
        Vault(),
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(transport), **kwargs
        ),
    )
    calendar_scope = (
        "Calendars.ReadWrite"
        if provider == "microsoft"
        else "https://www.googleapis.com/auth/calendar.events.owned"
    )
    identifier = actions.connect(
        AccountInput(
            provider=provider,
            label="Fixture",
            secret_reference="vault:" + "a" * 32,
            scopes=[calendar_scope],
            transmission_consent=True,
        )
    )
    value = CalendarInput(
        operation_id=uuid.uuid4().hex,
        account_id=identifier,
        account_revision=1,
        title="Exact event",
        body="Exact description",
        start="2030-01-02T10:00:00+05:00",
        end="2030-01-02T11:00:00+05:00",
        attendees=["guest@example.com"],
        send_invitations=True,
    )
    assert (await actions.dispatch(value))["status"] == "accepted"
    payload = json.loads(calls[0].content)
    if provider == "google":
        assert payload["start"]["dateTime"] == "2030-01-02T05:00:00+00:00"
        assert payload["attendees"] == [{"email": "guest@example.com"}]
        assert calls[0].url.params["sendUpdates"] == "all"
        assert payload["id"] == value.operation_id
    else:
        assert payload["start"] == {"dateTime": "2030-01-02T05:00:00", "timeZone": "UTC"}
        assert payload["attendees"] == [
            {"emailAddress": {"address": "guest@example.com"}, "type": "required"}
        ]
        assert payload["transactionId"] == str(uuid.UUID(hex=value.operation_id))
    with pytest.raises(ValueError):
        CalendarInput.model_validate(value.model_dump() | {"send_invitations": False})
    with pytest.raises(ValueError):
        CalendarInput.model_validate(value.model_dump() | {"start": "2030-01-02T05:00:00"})
    with pytest.raises(ValueError):
        CalendarInput.model_validate(value.model_dump() | {"end": value.start})
