"""Empty project creation uses exact native approval in a disposable workspace."""

import os

import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.main import app

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Verified Windows directory creation")


def request(client, args):
    return client.post("/api/v1/tools/execute", json={"name": "studio_create_project", "project_id": None, "arguments": args}).json()


def grant(client, args):
    result = request(client, args)
    assert result["status"] == "approval_required"
    identifier = result["approval"]["request_id"]
    assert client.post(f"/api/v1/approvals/{identifier}/approve").status_code == 200
    return identifier


def test_new_project_creates_only_after_exact_approval(tmp_path):
    with TestClient(app) as client:
        identifier = grant(client, {"name": "New fixture", "path": "new-fixture"})
        root = app.state.settings.safe_workspace / "new-fixture"
        assert not root.exists() and client.get("/api/v1/projects").json() == []
        result = client.post(f"/api/v1/tools/execute/{identifier}/continue").json()
        assert result["status"] == "success"
        assert root.is_dir() and not list(root.iterdir())
        registered = client.get("/api/v1/projects").json()
        assert len(registered) == 1 and registered[0]["root"].lower() == str(root).lower()
        assert client.post(f"/api/v1/tools/execute/{identifier}/continue").json()["status"] == "consumed"


def test_later_user_directory_is_not_reused_or_registered(tmp_path):
    with TestClient(app) as client:
        identifier = grant(client, {"name": "Fixture", "path": "later"})
        root = app.state.settings.safe_workspace / "later"
        root.mkdir()
        (root / "user.txt").write_text("later user data")
        result = client.post(f"/api/v1/tools/execute/{identifier}/continue").json()
        assert result["status"] == "invalid_arguments"
        assert (root / "user.txt").read_text() == "later user data"
        assert client.get("/api/v1/projects").json() == []


@pytest.mark.parametrize("path", ["../outside", ".env", "missing/child", ".git", "name.", "C:/outside"])
def test_invalid_new_project_destinations_have_no_side_effect(tmp_path, path):
    with TestClient(app) as client:
        result = request(client, {"name": "Fixture", "path": path})
        assert result["status"] == "invalid_arguments"
        assert not list(app.state.settings.safe_workspace.iterdir())
        assert client.get("/api/v1/projects").json() == []


@pytest.mark.parametrize("name", ["", " ", "\t"])
def test_invalid_name_is_rejected_before_creating_a_directory(tmp_path, name):
    with TestClient(app) as client:
        assert request(client, {"name": name, "path": "must-not-create"})["status"] == "invalid_arguments"
        assert not (app.state.settings.safe_workspace / "must-not-create").exists()


def test_registration_interruption_retains_created_directory_without_claiming_success(tmp_path, monkeypatch):
    with TestClient(app) as client:
        identifier = grant(client, {"name": "Fixture", "path": "interrupted"})
        def fail(*_):
            raise RuntimeError("Fixture registration interruption")
        monkeypatch.setattr(app.state.foundation, "register_project", fail)
        result = client.post(f"/api/v1/tools/execute/{identifier}/continue").json()
        assert result["status"] == "failed"
        assert (app.state.settings.safe_workspace / "interrupted").is_dir()
        assert client.get("/api/v1/projects").json() == []


def test_changed_workspace_or_policy_denies_old_project_approval(tmp_path):
    from services.agent_api.app.action_policy import PolicyUpdate
    with TestClient(app) as client:
        identifier = grant(client, {"name": "Fixture", "path": "old-policy"})
        app.state.executor.policy.update(PolicyUpdate(blocked_categories=["local_edit"]))
        assert client.post(f"/api/v1/tools/execute/{identifier}/continue").json()["status"] == "invalid_approval"
        assert not (app.state.settings.safe_workspace / "old-policy").exists()
        app.state.executor.policy.update(PolicyUpdate())
        identifier = grant(client, {"name": "Fixture", "path": "old-scope"})
        app.state.executor.registry.workspace_root = tmp_path / "new-workspace"
        app.state.executor.registry.workspace_root.mkdir()
        assert client.post(f"/api/v1/tools/execute/{identifier}/continue").json()["status"] == "invalid_approval"
        assert not (app.state.settings.safe_workspace / "old-scope").exists()


@pytest.mark.anyio
async def test_synchronized_new_project_grants_create_once(tmp_path, monkeypatch):
    import asyncio

    import services.agent_api.app.tools as tools_module
    with TestClient(app) as client:
        args = {"name": "Fixture", "path": "competing"}
        grants = [(await app.state.executor.execute("studio_create_project", args))["approval"]["request_id"] for _ in range(2)]
        for identifier in grants:
            app.state.memory.decide_approval(identifier, True)
        arrivals = []
        gate = asyncio.Event()
        original = asyncio.wait_for
        async def synchronized(awaitable, timeout):
            arrivals.append(True)
            if len(arrivals) == 2:
                gate.set()
            await gate.wait()
            return await original(awaitable, timeout)
        monkeypatch.setattr(tools_module.asyncio, "wait_for", synchronized)
        results = await asyncio.gather(*(app.state.executor.continue_approved(identifier) for identifier in grants))
        assert len(arrivals) == 2
        assert sorted(result["status"] for result in results) == ["invalid_arguments", "success"]
        assert len(client.get("/api/v1/projects").json()) == 1


def test_native_create_boundary_never_replaces_later_user_directory(tmp_path, monkeypatch):
    from contextlib import contextmanager

    import services.agent_api.app.project_creation as creation
    original = creation._create_owned_directory
    @contextmanager
    def later_directory(target):
        target.mkdir()
        (target / "user.txt").write_text("boundary user data")
        with original(target) as handle:
            yield handle
    with TestClient(app) as client:
        identifier = grant(client, {"name": "Fixture", "path": "boundary"})
        monkeypatch.setattr(creation, "_create_owned_directory", later_directory)
        assert client.post(f"/api/v1/tools/execute/{identifier}/continue").json()["status"] == "invalid_arguments"
        assert (app.state.settings.safe_workspace / "boundary/user.txt").read_text() == "boundary user data"
        assert client.get("/api/v1/projects").json() == []


def test_created_directory_cannot_be_substituted_before_registration(tmp_path, monkeypatch):
    from contextlib import contextmanager

    import services.agent_api.app.project_creation as creation
    original = creation._create_owned_directory
    attempted = []
    @contextmanager
    def substitute_after_creation(target):
        with original(target) as handle:
            attempted.append(True)
            with pytest.raises(OSError):
                target.rename(target.with_name("owned-original"))
            assert target.is_dir()
            assert not target.with_name("owned-original").exists()
            yield handle
    with TestClient(app) as client:
        identifier = grant(client, {"name": "Fixture", "path": "owned"})
        monkeypatch.setattr(creation, "_create_owned_directory", substitute_after_creation)
        assert client.post(f"/api/v1/tools/execute/{identifier}/continue").json()["status"] == "success"
        assert attempted == [True]
        root = app.state.settings.safe_workspace / "owned"
        assert not list(root.iterdir())
        assert len(client.get("/api/v1/projects").json()) == 1
