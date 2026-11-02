"""Explicit interrupted-change recovery on disposable Windows projects."""

import os
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.main import app
from tests.test_coding_changes import approved, continue_grant, execute, fixture_project, proposal
from tests.test_studio_create import create_proposal, shared_stream

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows recovery")


def set_status(identifier, status):
    with closing(sqlite3.connect(app.state.settings.database_path)) as db, db:
        db.execute("UPDATE coding_changes SET status=? WHERE change_id=?", (status, identifier))


def inspect(client, project, args):
    result = execute(client, project, "studio_inspect_recovery", args)
    assert result["status"] == "success"
    return result["result"]


def test_interrupted_apply_can_confirm_verified_effect_with_fresh_approval(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = proposal(client, project, root)
        assert continue_grant(client, project, approved(client, project, "studio_apply", args))["status"] == "success"
        set_status(args["change_id"], "applying")  # Crash between native effect and final durable status.
        observation = inspect(client, project, args)
        assert observation["mode"] == "finalize" and observation["final_status"] == "applied"
        recover = {**args, "observation_sha256": observation["observation_sha256"]}
        grant = approved(client, project, "studio_recover", recover)
        assert continue_grant(client, project, grant)["result"]["status"] == "applied"
        assert (root / "math.py").read_bytes().endswith(b"return a + b\r\n")
        assert (root / "unrelated.txt").read_text() == "existing user edit"


def recovery_arguments(client, project, args):
    return {**args, "observation_sha256": inspect(client, project, args)["observation_sha256"]}


def test_restore_missing_original_retains_inode_and_requires_exact_observation(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = proposal(client, project, root)
        original = root / "math.py"
        inode, content = original.stat().st_ino, original.read_bytes()
        backup = root / (".jarvis-backup-" + args["change_id"] + "-apply")
        original.rename(backup)  # Native interruption after first rename.
        set_status(args["change_id"], "applying")
        observation = inspect(client, project, args)
        assert observation["mode"] == "restore_missing"
        args = {**args, "observation_sha256": observation["observation_sha256"]}
        grant = approved(client, project, "studio_recover", args)
        assert not original.exists() and backup.exists()
        result = continue_grant(client, project, grant)
        assert result["status"] == "success" and result["result"]["status"] == "proposed"
        assert original.read_bytes() == content and original.stat().st_ino == inode
        assert not backup.exists()


@pytest.mark.parametrize("at_claim", [False, True])
def test_recovery_never_overwrites_later_public_file(tmp_path, monkeypatch, at_claim):
    from services.agent_api.app.coding_changes import CodingChanges
    claim = CodingChanges._claim
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = proposal(client, project, root)
        target = root / "math.py"
        backup = root / (".jarvis-backup-" + args["change_id"] + "-apply")
        target.rename(backup)
        set_status(args["change_id"], "applying")
        recover = recovery_arguments(client, project, args)
        grant = approved(client, project, "studio_recover", recover)
        if at_claim:
            def later(changes, identifier, before, after, identity=None):
                claim(changes, identifier, before, after, identity)
                if after.startswith("recovery_"):
                    target.write_bytes(b"later user file")
            monkeypatch.setattr(CodingChanges, "_claim", later)
        else:
            target.write_bytes(b"later user file")
        assert continue_grant(client, project, grant)["status"] == "invalid_arguments"
        assert target.read_bytes() == b"later user file"
        assert backup.read_bytes().endswith(b"return a - b\r\n")


def test_recovery_rejects_recreated_identical_checkpoint_and_cross_project(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        other, _ = fixture_project(client, tmp_path, "other")
        args = proposal(client, project, root)
        backup = root / (".jarvis-backup-" + args["change_id"] + "-apply")
        (root / "math.py").rename(backup)
        set_status(args["change_id"], "applying")
        recover = recovery_arguments(client, project, args)
        assert execute(client, other, "studio_inspect_recovery", args)["status"] == "invalid_arguments"
        grant = approved(client, project, "studio_recover", recover)
        content = backup.read_bytes()
        backup.rename(root / "user-retained.py")
        backup.write_bytes(content)
        assert continue_grant(client, project, grant)["status"] == "invalid_arguments"
        assert not (root / "math.py").exists()
        assert backup.read_bytes() == content


def test_recovery_rejects_additional_streams_preserving_every_byte(tmp_path):
    from pathlib import Path
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = proposal(client, project, root)
        backup = root / (".jarvis-backup-" + args["change_id"] + "-apply")
        (root / "math.py").rename(backup)
        set_status(args["change_id"], "applying")
        shared_stream(str(backup) + ":user-data", b"retained")
        assert execute(client, project, "studio_inspect_recovery", args)["status"] == "invalid_arguments"
        assert Path(str(backup) + ":user-data").read_bytes() == b"retained"


def test_created_identity_is_journaled_before_effect_and_survives_interruption(tmp_path, monkeypatch):
    from services.agent_api.app.coding_changes import CodingChanges
    from services.agent_api.app.tools import _InvalidApproval
    claim = CodingChanges._claim
    def interrupted(changes, identifier, before, after, identity=None):
        if before == "applying" and after == "applied":
            raise _InvalidApproval("Simulated interruption after native effect.")
        claim(changes, identifier, before, after, identity)
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = create_proposal(client, project)
        with monkeypatch.context() as patch:
            patch.setattr(CodingChanges, "_claim", interrupted)
            assert continue_grant(client, project, approved(client, project, "studio_apply", args))["status"] == "invalid_approval"
        observed = inspect(client, project, args)
        assert observed["mode"] == "finalize"
        assert int(observed["owned"]["inode"]) == (root / "new.py").stat().st_ino
        result = continue_grant(client, project, approved(client, project, "studio_recover", {**args, "observation_sha256": observed["observation_sha256"]}))
        assert result["result"]["status"] == "applied"


def test_recovery_preserves_identical_recreated_new_file(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = create_proposal(client, project)
        assert continue_grant(client, project, approved(client, project, "studio_apply", args))["status"] == "success"
        set_status(args["change_id"], "applying")
        (root / "new.py").rename(root / "owned-new.py")
        (root / "new.py").write_text("new fixture source")
        assert inspect(client, project, args)["mode"] == "unsupported"
        grant = approved(client, project, "studio_recover", recovery_arguments(client, project, args))
        assert continue_grant(client, project, grant)["status"] == "invalid_arguments"
        assert (root / "new.py").read_text() == "new fixture source"


def test_completed_change_does_not_offer_recovery(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = proposal(client, project, root)
        assert execute(client, project, "studio_inspect_recovery", args)["status"] == "invalid_arguments"


@pytest.mark.anyio
async def test_synchronized_recovery_grants_execute_once(tmp_path, monkeypatch):
    import asyncio

    import services.agent_api.app.tools as tools_module
    from services.agent_api.app.project_runtime import project_scope, runtime_for
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = proposal(client, project, root)
        (root / "math.py").rename(root / (".jarvis-backup-" + args["change_id"] + "-apply"))
        set_status(args["change_id"], "applying")
        recover = recovery_arguments(client, project, args)
        runtime = runtime_for(app, project)
        with project_scope(project):
            grants = [(await runtime.executor.execute("studio_recover", recover))["approval"]["request_id"] for _ in range(2)]
            for grant in grants:
                runtime.memory.decide_approval(grant, True)
            gate = asyncio.Event()
            arrivals = []
            original = asyncio.wait_for
            async def synchronized(awaitable, timeout):
                arrivals.append(True)
                if len(arrivals) == 2:
                    gate.set()
                await gate.wait()
                return await original(awaitable, timeout)
            monkeypatch.setattr(tools_module.asyncio, "wait_for", synchronized)
            results = await asyncio.gather(*(runtime.executor.continue_approved(grant) for grant in grants))
        assert len(arrivals) == 2
        assert sorted(result["status"] for result in results) == ["invalid_arguments", "success"]
        assert (root / "math.py").read_bytes().endswith(b"return a - b\r\n")


@pytest.mark.parametrize("expired", [False, True])
def test_recovery_rechecks_authorization_after_claim(tmp_path, monkeypatch, expired):
    from datetime import UTC, datetime, timedelta

    from services.agent_api.app.coding_changes import CodingChanges
    from services.agent_api.app.session_security import execution_authorized
    from services.agent_api.app.tools import _approved_deadline
    original = CodingChanges._claim
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = proposal(client, project, root)
        backup = root / (".jarvis-backup-" + args["change_id"] + "-apply")
        (root / "math.py").rename(backup)
        set_status(args["change_id"], "applying")
        recover = recovery_arguments(client, project, args)
        grant = approved(client, project, "studio_recover", recover)
        def revoke(changes, identifier, before, after, identity=None):
            original(changes, identifier, before, after, identity)
            if after.startswith("recovery_"):
                if expired:
                    _approved_deadline.set(datetime.now(UTC) - timedelta(seconds=1))
                else:
                    execution_authorized.set(lambda: False)
        monkeypatch.setattr(CodingChanges, "_claim", revoke)
        assert continue_grant(client, project, grant)["status"] == ("invalid_approval" if expired else "denied")
        assert not (root / "math.py").exists() and backup.exists()
