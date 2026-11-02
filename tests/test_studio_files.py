"""Coding context uses real temporary files and actual dispatch/approval paths."""

import os
import subprocess

import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.main import app
from services.agent_api.app.project_files import ProjectFiles
from services.agent_api.app.project_runtime import runtime_for


def test_explorer_and_reads_honor_nested_ignores_and_secret_exclusions(tmp_path):
    (tmp_path / ".gitignore").write_text("ignored/\n*.log\n!keep.log\n", encoding="utf-8")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "bug.py").write_text("sensitive ignored context")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / ".gitignore").write_text("generated.py\n")
    for path in ("main.py", "debug.log", "keep.log", ".env", "private.pem", "src/generated.py", "src/ok.py"):
        (tmp_path / path).write_text("print('fixture')", encoding="utf-8")
    files = ProjectFiles(tmp_path)
    names = {e["name"] for e in files.listing()["entries"]}
    assert {"main.py", "keep.log", "src"} <= names
    assert not names & {"ignored", "debug.log", ".env", "private.pem"}
    assert files.read("src/ok.py")["content"] == "print('fixture')"
    for path in ("ignored/bug.py", "debug.log", ".env", "private.pem", "src/generated.py"):
        with pytest.raises(ValueError):
            files.read(path)


@pytest.mark.parametrize("path", ["../outside.py", "main.py:stream", "main.py.", "main.py "])
def test_ambiguous_or_escaping_paths_rejected(tmp_path, path):
    with pytest.raises(ValueError):
        ProjectFiles(tmp_path).read(path)


def test_text_limits_binary_and_potential_credentials_fail_closed(tmp_path):
    files = ProjectFiles(tmp_path)
    for name, content in (("large.txt", b"a" * 64001), ("binary.txt", b"a\x00b"), ("credential.txt", b"api_key = abcdefghijklmnop")):
        (tmp_path / name).write_bytes(content)
        with pytest.raises(ValueError):
            files.read(name)
    (tmp_path / ".gitignore").write_bytes(b"#" * 16001)
    with pytest.raises(ValueError):
        files.read("credential.txt")


def test_nested_negation_matches_git_but_ignored_parent_cannot_reinclude(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "excluded").mkdir()
    (tmp_path / ".gitignore").write_text("*.log\nexcluded/\n")
    (tmp_path / "src" / ".gitignore").write_text("!keep.log\n")
    (tmp_path / "excluded" / ".gitignore").write_text("!keep.log\n")
    for path in ("src/keep.log", "src/no.log", "excluded/keep.log"):
        (tmp_path / path).write_text("fixture")
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True, capture_output=True)
    for path, expected in (("src/keep.log", False), ("src/no.log", True), ("excluded/keep.log", True)):
        result = subprocess.run(["git", "-C", str(tmp_path), "check-ignore", "--no-index", path], capture_output=True)
        assert result.returncode == int(not expected)
        assert ProjectFiles(tmp_path)._ignored(path, False) == expected


@pytest.mark.skipif(os.name != "nt", reason="Native Windows junction/rename sharing boundary")
def test_directory_handle_pins_route_against_junction_swap(tmp_path, monkeypatch):
    root = tmp_path / "root"
    target = root / "src"
    target.mkdir(parents=True)
    (target / "safe.py").write_text("fixture")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("outside")
    scan = os.scandir
    attempts = []

    def swapping(path):
        if Path(path) == target:
            try:
                target.rename(root / "original")
            except PermissionError:
                attempts.append("blocked")
            else:
                attempts.append("swapped")
                subprocess.run(["cmd", "/c", "mklink", "/J", str(target), str(outside)], check=True, capture_output=True)
        return scan(path)

    from pathlib import Path
    monkeypatch.setattr(os, "scandir", swapping)
    listing = ProjectFiles(root).listing("src")
    assert attempts == ["blocked"]
    assert [e["name"] for e in listing["entries"]] == ["safe.py"]


def test_opened_handle_is_checked_after_destination_swap(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    target = root / "main.py"
    target.write_text("safe")
    outside = tmp_path / "outside.py"
    outside.write_text("outside secret")
    original_open = os.open

    def swapped(path, flags, *args, **kwargs):
        # Deterministic equivalent of a path replaced by a link between check/open.
        return original_open(outside if path == target else path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapped)
    with pytest.raises(ValueError, match="destination changed"):
        ProjectFiles(root).read("main.py")


def test_studio_tools_use_real_executor_project_scope_and_read_policy(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "bug.py").write_text("def add(a,b): return a-b", encoding="utf-8")
    (root / ".env").write_text("do not expose", encoding="utf-8")
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "fixture", "root": str(root)}).json()["project_id"]
        payload = {"name": "studio_read", "arguments": {"path": "bug.py"}, "project_id": project}
        assert client.post("/api/v1/tools/execute", json=payload).json()["result"]["content"] == "def add(a,b): return a-b"
        assert client.post("/api/v1/tools/execute", json={**payload, "project_id": None}).json()["status"] == "not_found"
        assert client.post("/api/v1/tools/execute", json={**payload, "arguments": {"path": ".env"}}).json()["status"] == "invalid_arguments"
        for name in ("studio_read", "read_file"):
            assert client.post("/api/v1/tools/execute", json={**payload, "name": name, "arguments": {"path": ".env"}}).json()["status"] == "invalid_arguments"
        listing = client.post("/api/v1/tools/execute", json={**payload, "name": "list_directory", "arguments": {"path": ""}}).json()
        assert ".env" not in {item["name"] for item in listing["result"]["entries"]}
        client.put("/api/v1/policy", json={"require_read_approval": True})
        approval = client.post("/api/v1/tools/execute", json=payload).json()["approval"]["request_id"]
        assert client.post(f"/api/v1/approvals/{approval}/approve?project_id={project}").status_code == 200
        result = client.post(f"/api/v1/tools/execute/{approval}/continue?project_id={project}").json()
        assert result["status"] == "success"
        assert result["result"]["content"] == "def add(a,b): return a-b"
        assert (root / "bug.py").read_text() == "def add(a,b): return a-b"


@pytest.mark.parametrize("content", ['{"api_key": "short", "refresh_token": "abcdefghijklmnop"}', "Authorization: Bearer abcdefghijklmnop", "sk-abcdefghijklmnop", "-----BEGIN RSA PRIVATE KEY-----", '"postgres://fixture:fixture-password@localhost/db"', "ghp_abcdefghijklmnop"])
def test_credential_content_cannot_enter_any_project_read_or_instructions(tmp_path, content):
    root = tmp_path / "project"
    root.mkdir()
    for name in ("settings.json", "AGENTS.md"):
        (root / name).write_text(content, encoding="utf-8")
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "fixture", "root": str(root)}).json()["project_id"]
        for tool in ("read_file", "studio_read"):
            result = client.post("/api/v1/tools/execute", json={"name": tool, "arguments": {"path": "settings.json"}, "project_id": project}).json()
            assert result["status"] == "invalid_arguments"
            assert result["result"] is None and content not in str(result)
        assert runtime_for(app, project).instructions() == ""
