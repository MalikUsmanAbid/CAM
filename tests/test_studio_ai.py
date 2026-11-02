"""Model proposals use real transport contracts and never directly mutate files."""

import hashlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.main import app
from services.agent_api.app.providers import OpenAICompatibleChatProvider
from tests.test_coding_changes import approved, continue_grant, execute, fixture_project


def configure_fixture_model(send, endpoint="http://127.0.0.1:11434"):
    app.state.provider = OpenAICompatibleChatProvider("fixture-coder", endpoint, "fixture-key", client=httpx.AsyncClient(transport=httpx.MockTransport(send)))
    app.state.provider._transport.authorize_dispatch = app.state.usage.authorize
    app.state.provider._transport.dispatch_gate = app.state.inference_gate


def test_model_proposes_actual_reviewable_fix_without_applying_it(tmp_path):
    calls = []
    def send(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"content": "def add(a, b):\r\n    return a + b\r\n"})}}]})
    with TestClient(app) as client:
        configure_fixture_model(send)
        project, root = fixture_project(client, tmp_path)
        original = (root / "math.py").read_bytes()
        result = execute(client, project, "studio_ai_propose", {"path": "math.py", "before_sha256": hashlib.sha256(original).hexdigest(), "request": "Fix addition"})
        assert result["status"] == "success"
        assert len(calls) == 1 and "return a - b" in calls[0]["messages"][1]["content"]
        assert calls[0]["response_format"] == {"type": "json_object"}
        assert (root / "math.py").read_bytes() == original
        assert "+    return a + b" in result["result"]["diff"]
        args = {key: result["result"][key] for key in ("change_id", "path", "proposal_sha256")}
        assert continue_grant(client, project, approved(client, project, "studio_apply", args))["status"] == "success"
        assert (root / "math.py").read_bytes().endswith(b"return a + b\r\n")


@pytest.mark.parametrize("output", ['not json', '{"content":12}', '{"content":"safe","tool":"write_file"}', '{"content":"API_KEY=sk-123456789012345678901234567890"}'])
def test_invalid_model_output_cannot_create_or_apply_a_checkpoint(tmp_path, output):
    import sqlite3
    from contextlib import closing
    with TestClient(app) as client:
        configure_fixture_model(lambda _: httpx.Response(200, json={"choices": [{"message": {"content": output}}]}))
        project, root = fixture_project(client, tmp_path)
        original = (root / "math.py").read_bytes()
        result = execute(client, project, "studio_ai_propose", {"path": "math.py", "before_sha256": hashlib.sha256(original).hexdigest(), "request": "Fix addition"})
        assert result["status"] == "invalid_arguments"
        assert (root / "math.py").read_bytes() == original
        with closing(sqlite3.connect(app.state.settings.database_path)) as db:
            assert db.execute("SELECT COUNT(*) FROM coding_changes").fetchone()[0] == 0


def test_model_cannot_overwrite_user_edit_made_during_inference(tmp_path):
    root = None
    def send(_):
        (root / "math.py").write_text("later user edit")
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"content":"model patch"}'}}]})
    with TestClient(app) as client:
        configure_fixture_model(send)
        project, root = fixture_project(client, tmp_path)
        original = (root / "math.py").read_bytes()
        result = execute(client, project, "studio_ai_propose", {"path": "math.py", "before_sha256": hashlib.sha256(original).hexdigest(), "request": "Fix addition"})
        assert result["status"] == "invalid_arguments"
        assert (root / "math.py").read_text() == "later user edit"


def test_mock_model_is_honestly_disabled_for_coding(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        result = execute(client, project, "studio_ai_propose", {"path": "math.py", "before_sha256": hashlib.sha256((root / "math.py").read_bytes()).hexdigest(), "request": "Fix addition"})
        assert result["status"] == "disabled"


def test_cloud_source_is_not_sent_without_explicit_consent_and_budget(tmp_path):
    calls = []
    def send(request):
        calls.append(request)
        return httpx.Response(200, json={})
    with TestClient(app) as client:
        configure_fixture_model(send, "https://fixture.example")
        project, root = fixture_project(client, tmp_path)
        result = execute(client, project, "studio_ai_propose", {"path": "math.py", "before_sha256": hashlib.sha256((root / "math.py").read_bytes()).hexdigest(), "request": "Fix addition"})
        assert result["status"] == "failed" and not calls
        assert (root / "math.py").read_bytes().endswith(b"return a - b\r\n")


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["read_approval", "blocked", "risk"])
async def test_queued_coding_model_rechecks_policy_and_risk_before_transport(tmp_path, change):
    from services.agent_api.app.action_policy import PolicyUpdate
    from services.agent_api.app.permissions import RiskLevel
    from services.agent_api.app.project_runtime import project_scope, runtime_for
    calls = []
    class ChangeAtQueue:
        async def __aenter__(self):
            if change == "risk":
                runtime.executor.registry.get("studio_ai_propose").risk = RiskLevel.HIGH
            else:
                runtime.executor.policy.update(PolicyUpdate(require_read_approval=change == "read_approval", blocked_categories=["read_only"] if change == "blocked" else []))
        async def __aexit__(self, *_):
            pass
    with TestClient(app) as client:
        configure_fixture_model(lambda request: calls.append(request) or httpx.Response(200, json={}))
        project, root = fixture_project(client, tmp_path)
        runtime = runtime_for(app, project)
        app.state.provider._transport.dispatch_gate = ChangeAtQueue()
        with project_scope(project):
            result = await runtime.executor.execute("studio_ai_propose", {"path": "math.py", "before_sha256": hashlib.sha256((root / "math.py").read_bytes()).hexdigest(), "request": "Fix addition"})
        assert not calls
        assert result["status"] == ("denied" if change == "blocked" else "approval_required")


def test_read_approval_is_required_before_selected_context_transmission(tmp_path):
    from services.agent_api.app.action_policy import PolicyUpdate
    from services.agent_api.app.project_runtime import runtime_for
    calls = []
    with TestClient(app) as client:
        configure_fixture_model(lambda request: calls.append(json.loads(request.content)) or httpx.Response(200, json={"choices": [{"message": {"content": '{"content":"reviewed source"}'}}]}))
        project, root = fixture_project(client, tmp_path)
        runtime_for(app, project).executor.policy.update(PolicyUpdate(require_read_approval=True))
        args = {"path": "math.py", "before_sha256": hashlib.sha256((root / "math.py").read_bytes()).hexdigest(), "request": "Fix addition"}
        grant = approved(client, project, "studio_ai_propose", args)
        assert not calls
        assert continue_grant(client, project, grant)["status"] == "success"
        assert len(calls) == 1 and (root / "math.py").read_bytes().endswith(b"return a - b\r\n")


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["expiry", "scope"])
async def test_consumed_coding_grant_is_rechecked_after_model_queue(tmp_path, monkeypatch, change):
    from datetime import datetime, timedelta

    import services.agent_api.app.tools as tools_module
    from services.agent_api.app.action_policy import PolicyUpdate
    from services.agent_api.app.project_runtime import project_scope, runtime_for

    elapsed = [timedelta(0)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + elapsed[0]
    calls = []
    class ChangeAtQueue:
        async def __aenter__(self):
            if change == "expiry":
                elapsed[0] = timedelta(days=1)
            else:
                other = tmp_path / "other-scope"
                other.mkdir()
                runtime.executor.registry.workspace_root = other
        async def __aexit__(self, *_):
            pass
    with TestClient(app) as client:
        configure_fixture_model(lambda request: calls.append(request) or httpx.Response(200, json={}))
        project, root = fixture_project(client, tmp_path)
        runtime = runtime_for(app, project)
        runtime.executor.policy.update(PolicyUpdate(require_read_approval=True))
        with project_scope(project):
            args = {"path": "math.py", "before_sha256": hashlib.sha256((root / "math.py").read_bytes()).hexdigest(), "request": "Fix addition"}
            grant = (await runtime.executor.execute("studio_ai_propose", args))["approval"]["request_id"]
            runtime.memory.decide_approval(grant, True)
            app.state.provider._transport.dispatch_gate = ChangeAtQueue()
            monkeypatch.setattr(tools_module, "datetime", Clock)
            result = await runtime.executor.continue_approved(grant)
        assert not calls and result["status"] == "invalid_approval"
        assert (root / "math.py").read_bytes().endswith(b"return a - b\r\n")


@pytest.mark.parametrize("content", ["API_KEY=sk-123456789012345678901234567890", "x" * 8001])
def test_excluded_or_oversized_source_never_reaches_transport(tmp_path, content):
    calls = []
    with TestClient(app) as client:
        configure_fixture_model(lambda request: calls.append(request) or httpx.Response(200, json={}))
        project, root = fixture_project(client, tmp_path)
        (root / "math.py").write_bytes(content.encode())
        result = execute(client, project, "studio_ai_propose", {"path": "math.py", "before_sha256": hashlib.sha256(content.encode()).hexdigest(), "request": "Fix"})
        assert result["status"] == "invalid_arguments" and not calls
