"""Project context reaches the real transport contract, never foreign data."""

import asyncio
import hashlib
import json
import os
import shutil
import subprocess

import httpx
import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.action_policy import PolicyUpdate
from services.agent_api.app.main import app
from services.agent_api.app.project_runtime import runtime_for
from tests.test_coding_changes import approved, continue_grant, execute, fixture_project
from tests.test_studio_ai import configure_fixture_model


def arguments(root, **extra):
    return {"path": "math.py", "before_sha256": hashlib.sha256((root / "math.py").read_bytes()).hexdigest(), "request": "Fix addition", **extra}


def response(content="def add(a, b):\n    return a + b\n"):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"content": content})}}]})


def test_inherited_instructions_and_explicit_context_reach_transport(tmp_path):
    calls = []
    with TestClient(app) as client:
        configure_fixture_model(lambda request: calls.append(json.loads(request.content)) or response())
        project, root = fixture_project(client, tmp_path)
        (root / "AGENTS.md").write_text("ROOT_FIXTURE_INSTRUCTION")
        (root / "context.txt").write_text("EXPLICIT_CONTEXT_FIXTURE")
        result = execute(client, project, "studio_ai_propose", arguments(root, context_paths=["context.txt"]))
        assert result["status"] == "success"
        assert len(calls) == 1
        payload = json.loads(calls[0]["messages"][1]["content"].split("\n")[-1])
        context = payload["project_context"]
        assert context["project_id"] == project
        assert context["instructions"][0]["content"] == "ROOT_FIXTURE_INSTRUCTION"
        assert context["additional_files"][0]["content"] == "EXPLICIT_CONTEXT_FIXTURE"
        assert context["git"] == {"repository": False}


def test_project_chat_is_scoped_and_personal_chat_does_not_import_project_data(tmp_path):
    calls = []
    with TestClient(app) as client:
        configure_fixture_model(lambda request: calls.append(json.loads(request.content)) or httpx.Response(200, json={"choices": [{"message": {"content": "context answer"}}]}))
        project, root = fixture_project(client, tmp_path)
        (root / "AGENTS.md").write_text("PROJECT_A_REFERENCE")
        foreign = tmp_path / "foreign"
        foreign.mkdir()
        (foreign / "AGENTS.md").write_text("PROJECT_B_REFERENCE")
        client.post("/api/v1/projects", json={"name": "Foreign", "root": str(foreign)}).raise_for_status()
        answer = client.post(f"/api/v1/chat?project_id={project}", json={"message": "Explain project"})
        assert answer.status_code == 200 and answer.json()["message"] == "context answer"
        assert "PROJECT_A_REFERENCE" in calls[0]["messages"][1]["content"]
        assert "PROJECT_B_REFERENCE" not in json.dumps(calls)
        client.post("/api/v1/chat", json={"message": "personal question"}).raise_for_status()
        assert calls[-1]["messages"][1]["content"] == "personal question"
        assert client.post("/api/v1/chat?project_id=missing", json={"message": "unknown"}).status_code == 404
        assert len(calls) == 2


def test_windows_backslash_selection_binds_and_sends_nested_instructions(tmp_path):
    calls = []
    with TestClient(app) as client:
        configure_fixture_model(lambda request: calls.append(json.loads(request.content)) or response())
        project, root = fixture_project(client, tmp_path)
        (root / "AGENTS.md").write_text("ROOT_RULE")
        nested = root / "sub"
        nested.mkdir()
        (nested / "math.py").write_bytes((root / "math.py").read_bytes())
        (nested / "AGENTS.md").write_text("NESTED_RULE")
        runtime_for(app, project).executor.policy.update(PolicyUpdate(require_read_approval=True))
        value = arguments(root, path=r"sub\math.py")
        grant = approved(client, project, "studio_ai_propose", value)
        scope = runtime_for(app, project).memory.get_approval(grant).preview["execution_scope"]
        assert str(nested / "AGENTS.md") in scope["destinations"]
        assert continue_grant(client, project, grant)["status"] == "success"
        context = json.loads(calls[0]["messages"][1]["content"].split("\n")[-1])["project_context"]
        assert [item["path"] for item in context["instructions"]] == ["AGENTS.md", "sub/AGENTS.md"]
        assert [item["content"] for item in context["instructions"]] == ["ROOT_RULE", "NESTED_RULE"]


def test_project_chat_requires_exact_read_approval_before_any_transport(tmp_path):
    calls = []
    with TestClient(app) as client:
        configure_fixture_model(lambda request: calls.append(request) or httpx.Response(200, json={"choices": [{"message": {"content": "approved answer"}}]}))
        project, root = fixture_project(client, tmp_path)
        (root / "AGENTS.md").write_text("approved project reference")
        runtime_for(app, project).executor.policy.update(PolicyUpdate(require_read_approval=True))
        pending = client.post(f"/api/v1/chat?project_id={project}", json={"message": "Explain"})
        assert pending.status_code == 409 and calls == []
        grant = pending.json()["approval"]["request_id"]
        scope = pending.json()["approval"]["preview"]["execution_scope"]
        assert set(scope["destinations"]) == {str(root), str(root / "AGENTS.md")}
        client.post(f"/api/v1/approvals/{grant}/approve?project_id={project}").raise_for_status()
        assert client.post(f"/api/v1/tools/execute/{grant}/continue").status_code == 404
        result = continue_grant(client, project, grant)
        assert result["status"] == "success" and result["result"]["message"] == "approved answer"
        assert len(calls) == 1


@pytest.mark.parametrize("tool,field", [("studio_project_chat", "message"), ("studio_ai_propose", "request")])
def test_credentials_are_rejected_before_approval_persistence(tmp_path, tool, field):
    calls = []
    with TestClient(app) as client:
        configure_fixture_model(lambda request: calls.append(request) or response())
        project, root = fixture_project(client, tmp_path)
        runtime_for(app, project).executor.policy.update(PolicyUpdate(require_read_approval=True))
        value = arguments(root) if tool == "studio_ai_propose" else {}
        value[field] = "API_KEY=sk-123456789012345678901234567890"
        assert execute(client, project, tool, value)["status"] == "invalid_arguments"
        assert client.get(f"/api/v1/approvals?project_id={project}").json() == []
        assert calls == []


def test_bounded_context_approval_does_not_grant_other_files(tmp_path, monkeypatch):
    from services.agent_api.app.project_model_context import ProjectModelContext
    original = ProjectModelContext.snapshot
    calls = []
    with TestClient(app) as client:
        configure_fixture_model(lambda request: calls.append(request) or response())
        project, root = fixture_project(client, tmp_path)
        (root / "unexpected.txt").write_text("must not read")
        runtime_for(app, project).executor.policy.update(PolicyUpdate(require_read_approval=True))
        grant = approved(client, project, "studio_ai_propose", arguments(root))
        def unexpected(self, *args, **kwargs):
            self.files.read("unexpected.txt")
            return original(self, *args, **kwargs)
        monkeypatch.setattr(ProjectModelContext, "snapshot", unexpected)
        assert continue_grant(client, project, grant)["status"] == "invalid_approval"
        assert calls == []


@pytest.mark.parametrize("context_path", ["../outside.txt", ".env", "binary.dat"])
def test_unsafe_or_binary_additional_context_never_transmits(tmp_path, context_path):
    calls = []
    with TestClient(app) as client:
        configure_fixture_model(lambda request: calls.append(request) or response())
        project, root = fixture_project(client, tmp_path)
        (root / ".env").write_text("API_KEY=fixture-secret")
        (root / "binary.dat").write_bytes(b"\x00binary")
        result = execute(client, project, "studio_ai_propose", arguments(root, context_paths=[context_path]))
        assert result["status"] == "invalid_arguments" and calls == []


def test_instruction_change_during_inference_rejects_proposal(tmp_path):
    root = None
    def send(_):
        (root / "AGENTS.md").write_text("later instructions")
        return response()
    with TestClient(app) as client:
        configure_fixture_model(send)
        project, root = fixture_project(client, tmp_path)
        (root / "AGENTS.md").write_text("original instructions")
        assert execute(client, project, "studio_ai_propose", arguments(root))["status"] == "invalid_arguments"
        assert execute(client, project, "studio_changes", {})["result"]["changes"] == []


@pytest.mark.anyio
async def test_queued_instruction_change_prevents_transport(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    class Gate:
        async def __aenter__(self):
            entered.set()
            await release.wait()
        async def __aexit__(self, *_):
            return None
    calls = []
    with TestClient(app) as client:
        configure_fixture_model(lambda request: calls.append(request) or response())
        app.state.provider._transport.dispatch_gate = Gate()
        project, root = fixture_project(client, tmp_path)
        (root / "AGENTS.md").write_text("original instructions")
        pending = asyncio.create_task(runtime_for(app, project).executor.execute("studio_ai_propose", arguments(root)))
        await asyncio.wait_for(entered.wait(), 2)
        (root / "AGENTS.md").write_text("changed before dispatch")
        release.set()
        result = await pending
        assert result["status"] == "invalid_arguments" and calls == []


def test_git_status_context_requires_actual_separately_approved_executor(tmp_path):
    calls = []
    with TestClient(app) as client:
        configure_fixture_model(lambda request: calls.append(json.loads(request.content)) or response())
        project, root = fixture_project(client, tmp_path)
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        subprocess.run([shutil.which("git.exe"), "-C", str(root), "init", "-q", "--template="], env=environment, check=True, capture_output=True)
        assert execute(client, project, "studio_ai_propose", arguments(root))["status"] == "invalid_arguments"
        assert calls == []
        prepared = execute(client, project, "studio_prepare_git", {"operation": "status"})
        command = {key: prepared["result"][key] for key in ("run_id", "contract_sha256")}
        assert continue_grant(client, project, approved(client, project, "studio_run_command", command))["status"] == "success"
        assert execute(client, project, "studio_ai_propose", arguments(root))["status"] == "success"
        context = json.loads(calls[0]["messages"][1]["content"].split("\n")[-1])["project_context"]["git"]
        assert context["run_id"] == command["run_id"]
        assert "unrelated.txt" in context["output"] and "Previously approved" in context["meaning"]
        assert context["observation_time_unknown"] is True
        assert "prepared_at" in context and "observed_at" not in context
