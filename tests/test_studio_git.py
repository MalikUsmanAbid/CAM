"""Git inspection executes only approved commands in disposable repositories."""

import os
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.main import app
from tests.test_coding_changes import approved, continue_grant, execute, fixture_project

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Git command supervision")


def git(root, *arguments):
    executable = shutil.which("git.exe")
    assert executable, "Fixture requires installed Git"
    return subprocess.check_output([executable, "-C", str(root), *arguments], stderr=subprocess.STDOUT, text=True)


def test_prepared_git_status_reports_actual_dirty_files_without_modifying_them(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        git(root, "init", "-q")
        before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
        result = execute(client, project, "studio_prepare_git", {"operation": "status"})
        assert result["status"] == "success"
        args = {key: result["result"][key] for key in ("run_id", "contract_sha256")}
        grant = approved(client, project, "studio_run_command", args)
        outcome = continue_grant(client, project, grant)
        assert outcome["status"] == "success"
        assert "unrelated.txt" in outcome["result"]["output"]
        assert before == {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
        assert not (root / ".git" / "index.lock").exists()


def test_git_diff_disables_external_diff_and_text_conversion(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        git(root, "init", "-q")
        git(root, "add", "math.py")
        git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
        git(root, "config", "diff.external", "cmd.exe /c echo unsafe > external-effect")
        (root / "math.py").write_text("changed fixture source")
        result = execute(client, project, "studio_prepare_git", {"operation": "diff"})
        assert result["status"] == "success"
        assert "--no-ext-diff" in result["result"]["command"]["arguments"]
        assert "--no-textconv" in result["result"]["command"]["arguments"]
        args = {key: result["result"][key] for key in ("run_id", "contract_sha256")}
        grant = approved(client, project, "studio_run_command", args)
        outcome = continue_grant(client, project, grant)
        assert outcome["status"] == "success"
        assert "+changed fixture source" in outcome["result"]["output"]
        assert not (root / "external-effect").exists()


def test_non_repository_and_external_git_pointer_fail_closed(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        assert execute(client, project, "studio_prepare_git", {"operation": "status"})["status"] == "invalid_arguments"
        (root / ".git").write_text("gitdir: " + str(tmp_path / "external-git-data"))
        assert execute(client, project, "studio_prepare_git", {"operation": "status"})["status"] == "invalid_arguments"


def test_git_metadata_replacement_after_approval_cannot_execute(tmp_path, monkeypatch):
    dispatched = []

    async def forbidden_dispatch(*args, **kwargs):
        dispatched.append(True)
        raise AssertionError("Changed Git metadata must not dispatch.")

    monkeypatch.setattr("services.agent_api.app.studio_commands.run_process", forbidden_dispatch)
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        git(root, "init", "-q")
        prepared = execute(client, project, "studio_prepare_git", {"operation": "status"})["result"]
        args = {key: prepared[key] for key in ("run_id", "contract_sha256")}
        grant = approved(client, project, "studio_run_command", args)
        (root / ".git").rename(root / ".git-original")
        git(root, "init", "-q")
        result = continue_grant(client, project, grant)
        assert result["status"] == "invalid_arguments"
        assert result["result"] is None
        assert not dispatched


def test_git_log_reads_actual_commit_without_signature_helpers(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        git(root, "init", "-q")
        git(root, "add", "math.py")
        git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "real-fixture-commit")
        result = execute(client, project, "studio_prepare_git", {"operation": "log"})["result"]
        assert "--no-show-signature" in result["command"]["arguments"]
        args = {key: result[key] for key in ("run_id", "contract_sha256")}
        outcome = continue_grant(client, project, approved(client, project, "studio_run_command", args))
        assert outcome["status"] == "success"
        assert "real-fixture-commit" in outcome["result"]["output"]


def test_git_config_cannot_redirect_inspection_to_another_project(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        outside = tmp_path / "other-project"
        outside.mkdir()
        (outside / "outside-private-name.txt").write_text("private fixture")
        git(root, "init", "-q")
        git(root, "config", "core.worktree", str(outside))
        result = execute(client, project, "studio_prepare_git", {"operation": "status"})["result"]
        args = {key: result[key] for key in ("run_id", "contract_sha256")}
        outcome = continue_grant(client, project, approved(client, project, "studio_run_command", args))
        assert outcome["status"] == "success"
        assert "unrelated.txt" in outcome["result"]["output"]
        assert "outside-private-name" not in outcome["result"]["output"]


def test_git_filters_and_includes_are_rejected_without_running_helpers(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        git(root, "init", "-q")
        (root / ".gitattributes").write_text("*.py filter=fixture\n")
        git(root, "config", "filter.fixture.clean", "cmd.exe /c echo unsafe > helper-effect")
        assert execute(client, project, "studio_prepare_git", {"operation": "diff"})["status"] == "invalid_arguments"
        assert not (root / "helper-effect").exists()
        git(root, "config", "--remove-section", "filter.fixture")
        git(root, "config", "include.path", str(tmp_path / "external-config"))
        assert execute(client, project, "studio_prepare_git", {"operation": "status"})["status"] == "invalid_arguments"


def test_git_filter_added_after_approval_prevents_dispatch(tmp_path, monkeypatch):
    dispatched = []

    async def forbidden_dispatch(*args, **kwargs):
        dispatched.append(True)
        raise AssertionError("Changed configuration must not dispatch.")

    monkeypatch.setattr("services.agent_api.app.studio_commands.run_process", forbidden_dispatch)
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        git(root, "init", "-q")
        result = execute(client, project, "studio_prepare_git", {"operation": "diff"})["result"]
        args = {key: result[key] for key in ("run_id", "contract_sha256")}
        grant = approved(client, project, "studio_run_command", args)
        git(root, "config", "filter.fixture.clean", "cmd.exe /c echo unsafe > helper-effect")
        assert continue_grant(client, project, grant)["status"] == "invalid_arguments"
        assert not dispatched
        assert not (root / "helper-effect").exists()


@pytest.mark.parametrize("section", ['filter "fixture"', 'include', 'includeIf "gitdir:**"'])
def test_bom_prefixed_git_helper_configuration_is_rejected(tmp_path, section):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        git(root, "init", "-q")
        configuration = root / ".git" / "config"
        original = configuration.read_text()
        configuration.write_text("\ufeff[" + section + "]\n" + ('clean = "cmd.exe /c echo unsafe > helper-effect"\n' if section.startswith("filter") else "path = nonexistent\n") + original, encoding="utf-8")
        assert execute(client, project, "studio_prepare_git", {"operation": "diff"})["status"] == "invalid_arguments"
        assert not (root / "helper-effect").exists()


def test_nested_git_objects_junction_cannot_read_another_repository(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        git(outside, "init", "-q")
        (outside / "private.txt").write_text("private fixture")
        git(outside, "add", "private.txt")
        git(outside, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "outside-private-commit")
        git(root, "init", "-q")
        (root / ".git" / "objects").rename(root / ".git" / "original-objects")
        subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(root / ".git" / "objects"), str(outside / ".git" / "objects")], check=True, capture_output=True)
        shutil.copytree(outside / ".git" / "refs", root / ".git" / "refs", dirs_exist_ok=True)
        result = execute(client, project, "studio_prepare_git", {"operation": "log"})["result"]
        args = {key: result[key] for key in ("run_id", "contract_sha256")}
        outcome = continue_grant(client, project, approved(client, project, "studio_run_command", args))
        assert outcome["status"] == "invalid_arguments"
        assert outcome["result"] is None
        assert (outside / "private.txt").read_text() == "private fixture"


def test_worktree_configuration_and_partial_clones_fail_closed(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        git(root, "init", "-q")
        git(root, "config", "extensions.worktreeConfig", "true")
        git(root, "config", "--worktree", "filter.fixture.clean", "cmd.exe /c echo unsafe > helper-effect")
        assert execute(client, project, "studio_prepare_git", {"operation": "diff"})["status"] == "invalid_arguments"
        assert not (root / "helper-effect").exists()
        (root / ".git" / "config.worktree").unlink()
        git(root, "config", "--remove-section", "extensions")
        git(root, "config", "remote.origin.promisor", "true")
        assert execute(client, project, "studio_prepare_git", {"operation": "log"})["status"] == "invalid_arguments"


def test_new_git_metadata_added_during_reader_start_prevents_resume(tmp_path, monkeypatch):
    import threading

    start = threading.Thread.start
    injected = []
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        git(root, "init", "-q")
        result = execute(client, project, "studio_prepare_git", {"operation": "status"})["result"]
        args = {key: result[key] for key in ("run_id", "contract_sha256")}
        grant = approved(client, project, "studio_run_command", args)

        def add_metadata(thread):
            start(thread)
            if thread.name == "jarvis-command-output":
                (root / ".git" / "objects" / "new-entry").mkdir()
                injected.append(True)

        monkeypatch.setattr(threading.Thread, "start", add_metadata)
        outcome = continue_grant(client, project, grant)
        assert injected
        assert outcome["status"] == "invalid_arguments"
        assert outcome["result"] is None
