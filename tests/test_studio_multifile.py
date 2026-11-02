"""Multiple durable proposals retain individual file scope and approvals."""

import os

import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.main import app
from tests.test_coding_changes import approved, continue_grant, execute, fixture_project, proposal
from tests.test_studio_create import create_proposal

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows file effects")


def test_reopen_multiple_file_proposals_and_rollback_only_selected_checkpoint(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        existing = proposal(client, project, root)
        created = create_proposal(client, project)
        history = execute(client, project, "studio_changes", {})["result"]["changes"]
        assert {row["change_id"] for row in history} == {existing["change_id"], created["change_id"]}
        first = execute(client, project, "studio_inspect_change", existing)
        second = execute(client, project, "studio_inspect_change", created)
        assert first["result"]["before_exists"] and not second["result"]["before_exists"]
        assert first["result"]["content"].endswith("return a + b\r\n")
        assert first["result"]["before_content"].endswith("return a - b\r\n")
        assert second["result"]["content"] == "new fixture source"
        assert not (root / "new.py").exists()
        assert continue_grant(client, project, approved(client, project, "studio_apply", existing))["status"] == "success"
        assert execute(client, project, "studio_inspect_change", existing)["result"]["status"] == "applied"
        assert execute(client, project, "studio_inspect_change", created)["result"]["status"] == "proposed"
        assert continue_grant(client, project, approved(client, project, "studio_apply", created))["status"] == "success"
        assert continue_grant(client, project, approved(client, project, "studio_rollback", existing))["status"] == "success"
        assert (root / "math.py").read_bytes().endswith(b"return a - b\r\n")
        assert (root / "new.py").read_text() == "new fixture source"
        assert (root / "unrelated.txt").read_text() == "existing user edit"


def test_stale_second_file_is_preserved_without_reverting_first_success(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        first = proposal(client, project, root)
        second = create_proposal(client, project)
        assert continue_grant(client, project, approved(client, project, "studio_apply", first))["status"] == "success"
        grant = approved(client, project, "studio_apply", second)
        (root / "new.py").write_text("later user file")
        assert continue_grant(client, project, grant)["status"] == "invalid_arguments"
        assert execute(client, project, "studio_inspect_change", second)["status"] == "invalid_arguments"
        assert (root / "math.py").read_bytes().endswith(b"return a + b\r\n")
        assert (root / "new.py").read_text() == "later user file"
        states = {row["path"]: row["status"] for row in execute(client, project, "studio_changes", {})["result"]["changes"]}
        assert states == {"math.py": "applied", "new.py": "proposed"}


def test_proposal_reopen_rejects_other_project_and_tampered_contract(tmp_path):
    import sqlite3
    from contextlib import closing

    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        other, _ = fixture_project(client, tmp_path, "other")
        args = proposal(client, project, root)
        assert execute(client, other, "studio_inspect_change", args)["status"] == "invalid_arguments"
        with closing(sqlite3.connect(app.state.settings.database_path)) as db, db:
            db.execute("UPDATE coding_changes SET after_content=? WHERE change_id=?", (b"tampered", args["change_id"]))
        assert execute(client, project, "studio_inspect_change", args)["status"] == "invalid_arguments"
