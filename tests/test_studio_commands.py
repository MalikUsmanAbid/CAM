"""Actual command approvals run disposable fixture code, never user scripts."""

import os
import sys

import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.main import app
from tests.test_coding_changes import approved, continue_grant, execute, fixture_project

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows command dispatch")


def prepare(client, project, args, timeout=5):
    result = execute(client, project, "studio_prepare_command", {"executable": sys.executable, "arguments": args, "cwd": ".", "timeout_seconds": timeout, "output_limit": 2048})
    assert result["status"] == "success"
    return {key: result["result"][key] for key in ("run_id", "contract_sha256")}


def test_command_requires_exact_approval_and_records_actual_failure(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = prepare(client, project, ["-c", "from pathlib import Path; Path('executed').touch(); print('real fixture'); raise SystemExit(7)"])
        assert not (root / "executed").exists()
        grant = approved(client, project, "studio_run_command", args)
        assert not (root / "executed").exists()
        result = continue_grant(client, project, grant)
        assert result["status"] == "failed"
        assert result["result"]["exit_code"] == 7
        assert "real fixture" in result["result"]["output"]
        assert (root / "executed").exists()
        state = execute(client, project, "studio_command_runs", {})["result"]["runs"]
        assert state[0]["status"] == "failed"
        assert continue_grant(client, project, grant)["status"] != "success"


def test_command_runs_only_once_across_two_grants(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = prepare(client, project, ["-c", "from pathlib import Path; p=Path('count'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"])
        grants = [approved(client, project, "studio_run_command", args) for _ in range(2)]
        assert continue_grant(client, project, grants[0])["status"] == "success"
        assert continue_grant(client, project, grants[1])["status"] != "success"
        assert (root / "count").read_text() == "x"


@pytest.mark.anyio
async def test_running_command_inherits_session_revocation_before_later_fixture_effect(tmp_path):
    import _winapi
    import asyncio

    from services.agent_api.app.project_runtime import project_scope, runtime_for
    from services.agent_api.app.session_security import execution_authorized

    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = prepare(client, project, ['-c', "import os,time; from pathlib import Path; Path('running-pid.tmp').write_text(str(os.getpid())); os.replace('running-pid.tmp', 'running-pid'); exec(" + repr("while not Path('release').exists(): time.sleep(0.02)") + " ); Path('forbidden-after-revoke').touch()"], timeout=20)
        runtime = runtime_for(app, project)
        valid = [True]
        token = execution_authorized.set(lambda: valid[0])
        task = None
        try:
            with project_scope(project):
                proposal = await runtime.executor.execute('studio_run_command', args)
                grant = proposal['approval']['request_id']
                runtime.memory.decide_approval(grant, True)
                task = asyncio.create_task(runtime.executor.continue_approved(grant))
                pid = None
                for _ in range(500):
                    try:
                        pid = int((root / 'running-pid').read_text())
                    except (OSError, ValueError):
                        await asyncio.sleep(0.02)
                        continue
                    if pid:
                        break
                assert pid
                valid[0] = False
                result = await task
                assert result['status'] == 'denied'
                assert not (root / 'forbidden-after-revoke').exists()
                try:
                    handle = _winapi.OpenProcess(0x100000, False, pid)
                except OSError as error:
                    assert error.winerror == 87
                else:
                    try:
                        assert _winapi.WaitForSingleObject(handle, 0) == 0
                    finally:
                        _winapi.CloseHandle(handle)
        finally:
            valid[0] = False
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            execution_authorized.reset(token)


@pytest.mark.anyio
@pytest.mark.parametrize("expired", [False, True])
async def test_queued_command_rechecks_revocation_or_expiry_and_cannot_retry(tmp_path, monkeypatch, expired):
    import asyncio
    import sqlite3
    from contextlib import closing
    from datetime import UTC, datetime, timedelta

    import services.agent_api.app.tools as tools_module
    from services.agent_api.app.project_runtime import project_scope, runtime_for
    from services.agent_api.app.session_security import execution_authorized

    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        first_args = prepare(client, project, ["-c", "from pathlib import Path; import time; Path('started').touch(); exec(" + repr("while not Path('release').exists(): time.sleep(0.02)") + " )"], timeout=20)
        second_args = prepare(client, project, ["-c", "from pathlib import Path; Path('queued-forbidden').touch()"])
        runtime = runtime_for(app, project)
        valid = [True]
        token = execution_authorized.set(lambda: valid[0])
        try:
            with project_scope(project):
                grants = [(await runtime.executor.execute("studio_run_command", args))["approval"]["request_id"] for args in (first_args, second_args)]
                for grant in grants:
                    runtime.memory.decide_approval(grant, True)
                first = asyncio.create_task(runtime.executor.continue_approved(grants[0]))
                for _ in range(500):
                    if (root / "started").exists():
                        break
                    await asyncio.sleep(0.02)
                assert (root / "started").exists()
                second = asyncio.create_task(runtime.executor.continue_approved(grants[1]))
                queued = False
                for _ in range(500):
                    with closing(sqlite3.connect(runtime.memory.database_path)) as db:
                        queued = db.execute("SELECT status FROM command_runs WHERE run_id=?", (second_args["run_id"],)).fetchone()[0] == "running"
                    if queued:
                        break
                    await asyncio.sleep(0.02)
                assert queued and not (root / "queued-forbidden").exists()
                if expired:
                    deadline = max(datetime.fromisoformat(runtime.memory.get_approval(grant).expires_at) for grant in grants)

                    class FutureClock(datetime):
                        @classmethod
                        def now(cls, tz=UTC):
                            return deadline + timedelta(seconds=1)

                    monkeypatch.setattr(tools_module, "datetime", FutureClock)
                else:
                    valid[0] = False
                results = await asyncio.gather(first, second)
                assert all(result["status"] == ("invalid_approval" if expired else "denied") for result in results), results
        finally:
            execution_authorized.reset(token)
        assert not (root / "queued-forbidden").exists()
        with closing(sqlite3.connect(runtime.memory.database_path)) as db:
            assert db.execute("SELECT status FROM command_runs WHERE run_id=?", (second_args["run_id"],)).fetchone()[0] == "interrupted"
        assert execute(client, project, "studio_run_command", second_args)["status"] == "invalid_arguments"


@pytest.mark.anyio
async def test_running_command_cancellation_records_interruption_and_stops_effect(tmp_path):
    import asyncio

    from services.agent_api.app.project_runtime import project_scope, runtime_for

    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = prepare(client, project, ["-c", "from pathlib import Path; import time; Path('started').touch(); time.sleep(2); Path('forbidden').touch()"])
        runtime = runtime_for(app, project)
        with project_scope(project):
            grant = (await runtime.executor.execute("studio_run_command", args))["approval"]["request_id"]
            runtime.memory.decide_approval(grant, True)
            operation = asyncio.create_task(runtime.executor.continue_approved(grant))
            for _ in range(500):
                if (root / "started").exists():
                    break
                await asyncio.sleep(0.02)
            assert (root / "started").exists()
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
        await asyncio.sleep(2.1)
        assert not (root / "forbidden").exists()
        state = execute(client, project, "studio_command_runs", {})["result"]["runs"]
        assert state[0]["status"] == "interrupted"
        assert execute(client, project, "studio_run_command", args)["status"] == "invalid_arguments"


def test_command_proposal_cannot_be_used_in_another_project(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        other, other_root = fixture_project(client, tmp_path, "other")
        args = prepare(client, project, ["-c", "from pathlib import Path; Path('forbidden').touch()"])
        assert execute(client, other, "studio_run_command", args)["status"] == "invalid_arguments"
        assert not (root / "forbidden").exists() and not (other_root / "forbidden").exists()


def test_policy_revision_race_at_command_handler_boundary_requires_new_approval(tmp_path, monkeypatch):
    from services.agent_api.app.action_policy import PolicyUpdate
    from services.agent_api.app.studio_commands import StudioCommands

    original = StudioCommands.run

    async def change_policy_at_entry(commands, value):
        commands.policy.update(PolicyUpdate())  # Same allowance, new revision.
        return await original(commands, value)

    monkeypatch.setattr(StudioCommands, "run", change_policy_at_entry)
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = prepare(client, project, ["-c", "from pathlib import Path; Path('forbidden').touch()"])
        grant = approved(client, project, "studio_run_command", args)
        result = continue_grant(client, project, grant)
        assert result["status"] in {"denied", "invalid_approval"}
        assert not (root / "forbidden").exists()


def test_changed_executable_requires_new_preparation_and_approval(tmp_path):
    import shutil

    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        executable = tmp_path / "fixture.exe"
        shutil.copyfile(sys.executable, executable)
        result = execute(client, project, "studio_prepare_command", {"executable": str(executable), "arguments": ["-c", "from pathlib import Path; Path('forbidden').touch()"]})
        assert result["status"] == "success"
        args = {key: result["result"][key] for key in ("run_id", "contract_sha256")}
        grant = approved(client, project, "studio_run_command", args)
        executable.write_bytes(b"changed executable after approval")
        assert continue_grant(client, project, grant)["status"] != "success"
        assert not (root / "forbidden").exists()


def test_altered_saved_arguments_cannot_execute(tmp_path):
    import sqlite3
    from contextlib import closing

    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = prepare(client, project, ["-c", "print('approved harmless fixture')"])
        grant = approved(client, project, "studio_run_command", args)
        with closing(sqlite3.connect(app.state.settings.database_path)) as db, db:
            db.execute("UPDATE command_runs SET contract_json=replace(contract_json,?,?) WHERE run_id=?", ("approved harmless fixture", "tampered contract", args["run_id"]))
        assert continue_grant(client, project, grant)["status"] != "success"
        assert (root / "unrelated.txt").read_text() == "existing user edit"


@pytest.mark.anyio
async def test_synchronized_competing_command_grants_execute_once(tmp_path, monkeypatch):
    import asyncio

    import services.agent_api.app.tools as tools_module
    from services.agent_api.app.project_runtime import project_scope, runtime_for

    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = prepare(client, project, ["-c", "from pathlib import Path; p=Path('count'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"])
        runtime = runtime_for(app, project)
        with project_scope(project):
            grants = [(await runtime.executor.execute("studio_run_command", args))["approval"]["request_id"] for _ in range(2)]
            for grant in grants:
                runtime.memory.decide_approval(grant, True)
            gate = asyncio.Event()
            arrivals = []
            wait_for = asyncio.wait_for

            async def synchronize(awaitable, timeout):
                arrivals.append(True)
                if len(arrivals) == 2:
                    gate.set()
                await gate.wait()
                return await wait_for(awaitable, timeout)

            monkeypatch.setattr(tools_module.asyncio, "wait_for", synchronize)
            results = await asyncio.gather(*(runtime.executor.continue_approved(grant) for grant in grants))
        assert len(arrivals) == 2
        assert sorted(result["status"] for result in results) == ["invalid_arguments", "success"]
        assert (root / "count").read_text() == "x"
