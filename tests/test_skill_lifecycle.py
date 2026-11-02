"""Deferred lifecycle regressions using temporary database/workspace fixtures."""

import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.agent_api.app import skill_manifests
from services.agent_api.app.capabilities import CapabilityRegistry, Skill, SkillRegistry
from services.agent_api.app.main import app
from services.agent_api.app.migrations import initialize_database
from services.agent_api.app.session_security import execution_dispatch_authorized
from services.agent_api.app.tools import build_registry


def manifest(version="1.0.0"):
    return Skill(
        skill_id="project_reader",
        name="Project reader",
        description="Read bounded project files.",
        version=version,
        allowed_tools=["read_file"],
        required_capabilities=["tool.read_file"],
        risk_level="low",
        instructions="Read the selected file.",
    )


def fixture(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    database = tmp_path / "skills.sqlite"
    initialize_database(database)
    registry = build_registry(root)
    return SimpleNamespace(
        project_id="project-A",
        root=root,
        memory=SimpleNamespace(database_path=database),
        executor=SimpleNamespace(registry=registry),
        skills=SkillRegistry(CapabilityRegistry.from_tools(registry)),
    )


def test_semantic_version_input_is_bounded_before_numeric_comparison():
    with pytest.raises(ValueError):
        manifest("9" * 61 + ".0.0")


def exact(runtime):
    item = skill_manifests.inspect(runtime, "project_reader")
    return skill_manifests.UninstallManifest(
        skill_id=item["skill_id"],
        expected_version=item["version"],
        manifest_sha256=item["manifest_sha256"],
        confirm_uninstall=True,
    )


def test_inspect_uninstall_removes_loaded_guidance_and_preserves_user_files(tmp_path):
    runtime = fixture(tmp_path)
    original = runtime.root / "user.txt"
    original.write_text("preserve")
    skill_manifests.install(runtime, manifest(), consent=True)
    skill_manifests.set_enabled(runtime, "project_reader", True)
    skill_manifests.load(runtime)
    assert runtime.skills.get("project_reader").status == "enabled"
    value = exact(runtime)
    result = skill_manifests.uninstall(runtime, value)
    assert result["status"] == "uninstalled"
    assert skill_manifests.list_manifests(runtime) == []
    with pytest.raises(KeyError):
        runtime.skills.get("project_reader")
    assert original.read_text() == "preserve"
    assert runtime.executor.registry.get("read_file").enabled


def test_stale_activation_and_uninstall_refuse_an_updated_manifest(tmp_path):
    runtime = fixture(tmp_path)
    skill_manifests.install(runtime, manifest(), consent=True)
    value = exact(runtime)
    skill_manifests.install(runtime, manifest("2.0.0"), consent=True)
    with pytest.raises(ValueError, match="changed"):
        skill_manifests.set_enabled(
            runtime,
            value.skill_id,
            True,
            expected_version=value.expected_version,
            manifest_sha256=value.manifest_sha256,
        )
    with pytest.raises(ValueError, match="changed"):
        skill_manifests.uninstall(runtime, value)
    assert skill_manifests.inspect(runtime, value.skill_id)["version"] == "2.0.0"
    assert skill_manifests.inspect(runtime, value.skill_id)["enabled"] is False


def test_activation_holds_sqlite_writer_lease_during_current_manifest_validation(
    tmp_path, monkeypatch
):
    runtime = fixture(tmp_path)
    skill_manifests.install(runtime, manifest(), consent=True)
    validate = skill_manifests.validate_manifest
    observations = []

    def guarded_validation(active_runtime, skill):
        # Deterministic dispatch-boundary probe: a competing writer cannot
        # replace the row between validation and activation. No sleeps/threads.
        with closing(sqlite3.connect(runtime.memory.database_path, timeout=0)) as competing:
            try:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    competing.execute("BEGIN IMMEDIATE")
                observations.append("writer excluded during validation")
            finally:
                competing.rollback()
        validate(active_runtime, skill)

    monkeypatch.setattr(skill_manifests, "validate_manifest", guarded_validation)
    skill_manifests.set_enabled(runtime, "project_reader", True)
    assert observations == ["writer excluded during validation"]


def test_uninstall_rechecks_scope_and_dispatch_before_mutation(tmp_path):
    runtime = fixture(tmp_path)
    skill_manifests.install(runtime, manifest(), consent=True)
    value = exact(runtime)
    other = SimpleNamespace(**vars(runtime))
    other.project_id = "project-B"
    with pytest.raises(ValueError, match="unavailable"):
        skill_manifests.uninstall(other, value)

    def deny():
        raise ValueError("dispatch revoked")

    token = execution_dispatch_authorized.set(deny)
    try:
        with pytest.raises(ValueError, match="revoked"):
            skill_manifests.uninstall(runtime, value)
    finally:
        execution_dispatch_authorized.reset(token)
    assert skill_manifests.inspect(runtime, value.skill_id)["version"] == "1.0.0"


def test_api_uninstall_approval_cannot_remove_a_replacement_version(tmp_path):
    root = tmp_path / "api-project"
    root.mkdir()
    with TestClient(app) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "Fixture", "root": str(root)}
        ).json()["project_id"]
        endpoint = f"/api/v1/workspace/skill-manifests?project_id={project}"
        assert (
            client.post(
                endpoint, json={"manifest": manifest().model_dump(mode="json"), "consent": True}
            ).status_code
            == 201
        )
        execute = f"/api/v1/workspace/knowledge/execute?project_id={project}"
        inspected = client.post(
            execute, json={"tool": "skill_inspect", "arguments": {"skill_id": "project_reader"}}
        ).json()
        assert inspected["status"] == "success"
        item = inspected["result"]
        pending = client.post(
            execute,
            json={
                "tool": "skill_uninstall",
                "arguments": {
                    "skill_id": item["skill_id"],
                    "expected_version": item["version"],
                    "manifest_sha256": item["manifest_sha256"],
                    "confirm_uninstall": True,
                },
            },
        ).json()
        assert pending["status"] == "approval_required"
        grant = pending["approval"]["request_id"]
        assert (
            client.post(f"/api/v1/approvals/{grant}/approve?project_id={project}").status_code
            == 200
        )
        assert (
            client.post(
                endpoint,
                json={"manifest": manifest("2.0.0").model_dump(mode="json"), "consent": True},
            ).status_code
            == 201
        )
        result = client.post(f"/api/v1/tools/execute/{grant}/continue?project_id={project}").json()
        assert result["status"] != "success"
        assert client.get(endpoint).json()[0]["version"] == "2.0.0"
        assert client.get(endpoint).json()[0]["enabled"] == 0
