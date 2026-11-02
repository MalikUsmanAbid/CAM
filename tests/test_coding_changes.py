"""Native checkpoint actions through actual approvals on disposable projects."""

import asyncio
import hashlib
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.coding_changes import CodingChanges
from services.agent_api.app.main import app
from services.agent_api.app.session_security import execution_authorized
from services.agent_api.app.tools import _approved_deadline, _InvalidApproval, _PolicyDenied


def fixture_project(client, tmp_path, name="project"):
    root = tmp_path / name
    root.mkdir()
    (root / "math.py").write_bytes(b"def add(a, b):\r\n    return a - b\r\n")
    (root / "unrelated.txt").write_text("existing user edit")
    project = client.post("/api/v1/projects", json={"name": name, "root": str(root)}).json()["project_id"]
    return project, root


def execute(client, project, tool, arguments):
    return client.post("/api/v1/tools/execute", json={"name": tool, "project_id": project, "arguments": arguments}).json()


def proposal(client, project, root):
    original = (root / "math.py").read_bytes()
    result = execute(client, project, "studio_propose", {"path": "math.py", "before_sha256": hashlib.sha256(original).hexdigest(), "content": "def add(a, b):\r\n    return a + b\r\n"})
    assert result["status"] == "success"
    assert (root / "math.py").read_bytes() == original
    assert "+    return a + b" in result["result"]["diff"]
    data = result["result"]
    return {key: data[key] for key in ("change_id", "path", "proposal_sha256")}


def approved(client, project, tool, arguments):
    result = execute(client, project, tool, arguments)
    assert result["status"] == "approval_required"
    grant = result["approval"]["request_id"]
    assert client.post(f"/api/v1/approvals/{grant}/approve?project_id={project}").status_code == 200
    return grant


def continue_grant(client, project, grant):
    return client.post(f"/api/v1/tools/execute/{grant}/continue?project_id={project}").json()


@pytest.mark.skipif(os.name != "nt", reason="Verified Windows exclusive sharing executor")
def test_exact_apply_and_selective_rollback_preserve_unrelated_dirty_files(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        original = (root / "math.py").read_bytes()
        arguments = proposal(client, project, root)
        grant = approved(client, project, "studio_apply", arguments)
        assert (root / "math.py").read_bytes() == original
        result = continue_grant(client, project, grant)
        assert result["status"] == "success" and result["result"]["status"] == "applied"
        assert (root / "math.py").read_bytes() == b"def add(a, b):\r\n    return a + b\r\n"
        assert continue_grant(client, project, grant)["status"] == "consumed"
        rollback = approved(client, project, "studio_rollback", arguments)
        assert continue_grant(client, project, rollback)["result"]["status"] == "rolled_back"
        assert (root / "math.py").read_bytes() == original
        assert (root / "unrelated.txt").read_text() == "existing user edit"


@pytest.mark.skipif(os.name != "nt", reason="Verified Windows exclusive sharing executor")
def test_apply_and_rollback_deny_file_changes_after_exact_approval(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        arguments = proposal(client, project, root)
        grant = approved(client, project, "studio_apply", arguments)
        (root / "math.py").write_text("later user change")
        assert continue_grant(client, project, grant)["status"] == "invalid_arguments"
        assert (root / "math.py").read_text() == "later user change"
        (root / "math.py").write_bytes(b"def add(a, b):\r\n    return a - b\r\n")
        grant = approved(client, project, "studio_apply", arguments)
        assert continue_grant(client, project, grant)["status"] == "success"
        rollback = approved(client, project, "studio_rollback", arguments)
        (root / "math.py").write_text("user edit after apply")
        assert continue_grant(client, project, rollback)["status"] == "invalid_arguments"
        assert (root / "math.py").read_text() == "user edit after apply"


def test_wrong_project_or_tampered_checkpoint_cannot_apply(tmp_path):
    with TestClient(app) as client:
        a, root = fixture_project(client, tmp_path, "a")
        b, other = fixture_project(client, tmp_path, "b")
        args = proposal(client, a, root)
        assert execute(client, b, "studio_apply", args)["status"] == "invalid_arguments"
        with closing(sqlite3.connect(app.state.settings.database_path)) as db, db:
            db.execute("UPDATE coding_changes SET after_content=? WHERE change_id=?", (b"modified checkpoint", args["change_id"]))
        assert execute(client, a, "studio_apply", args)["status"] == "invalid_arguments"
        assert (root / "math.py").read_bytes() == (other / "math.py").read_bytes()


def test_stale_proposal_rejected_before_checkpoint_creation(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        result = execute(client, project, "studio_propose", {"path": "math.py", "before_sha256": "0" * 64, "content": "proposed"})
        assert result["status"] == "invalid_arguments"
        with closing(sqlite3.connect(app.state.settings.database_path)) as db:
            assert db.execute("SELECT COUNT(*) FROM coding_changes").fetchone()[0] == 0


@pytest.mark.skipif(os.name != "nt", reason="Native Windows write boundary")
def test_failure_after_durable_claim_is_uncertain_and_never_retried(tmp_path, monkeypatch):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = proposal(client, project, root)
        grant = approved(client, project, "studio_apply", args)

        claim = CodingChanges._claim

        def interrupted(changes, identifier, before, after):
            if after == "applied":
                raise OSError("fixture simulated interrupted final checkpoint update")
            return claim(changes, identifier, before, after)

        monkeypatch.setattr(CodingChanges, "_claim", interrupted)
        assert continue_grant(client, project, grant)["status"] == "failed"
        with closing(sqlite3.connect(app.state.settings.database_path)) as db:
            assert db.execute("SELECT status FROM coding_changes WHERE change_id=?", (args["change_id"],)).fetchone()[0] == "applying"
        grant = approved(client, project, "studio_apply", args)
        assert continue_grant(client, project, grant)["status"] == "invalid_arguments"
        assert (root / "unrelated.txt").read_text() == "existing user edit"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows hard-link and writer boundaries")
def test_outside_hard_link_is_rejected_by_all_reads_and_legacy_writer(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("outside preserved")
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        os.link(outside, root / "linked.txt")
        for tool in ("read_file", "studio_read"):
            assert execute(client, project, tool, {"path": "linked.txt"})["status"] == "invalid_arguments"
        grant = approved(client, project, "write_file", {"path": "linked.txt", "content": "must not write outside", "overwrite": True})
        assert continue_grant(client, project, grant)["status"] == "invalid_arguments"
        assert outside.read_text() == "outside preserved"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows hard-link boundary")
@pytest.mark.parametrize("rollback", [False, True])
def test_link_added_after_proposal_or_apply_blocks_checkpoint_effect(tmp_path, rollback):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = proposal(client, project, root)
        if rollback:
            grant = approved(client, project, "studio_apply", args)
            assert continue_grant(client, project, grant)["status"] == "success"
        outside = tmp_path / "outside.txt"
        os.link(root / "math.py", outside)
        original = outside.read_bytes()
        grant = approved(client, project, "studio_rollback" if rollback else "studio_apply", args)
        assert continue_grant(client, project, grant)["status"] == "invalid_arguments"
        assert outside.read_bytes() == original


@pytest.mark.skipif(os.name != "nt", reason="Native Windows exclusive writer")
def test_already_open_writer_is_refused_without_mutation(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = proposal(client, project, root)
        grant = approved(client, project, "studio_apply", args)
        before = (root / "math.py").read_bytes()
        with open(root / "math.py", "r+b"):
            assert continue_grant(client, project, grant)["status"] == "invalid_arguments"
        assert (root / "math.py").read_bytes() == before


@pytest.mark.skipif(os.name != "nt", reason="Native Windows checkpoint effects")
def test_separate_approved_grants_cannot_execute_same_change_twice(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = proposal(client, project, root)
        first = approved(client, project, "studio_apply", args)
        second = approved(client, project, "studio_apply", args)
        assert first != second
        assert continue_grant(client, project, first)["status"] == "success"
        assert continue_grant(client, project, second)["status"] == "invalid_arguments"
        assert (root / "math.py").read_bytes().endswith(b"return a + b\r\n")


@pytest.mark.skipif(os.name != "nt", reason="Native Windows dispatch/effect boundary")
@pytest.mark.parametrize("expired", [False, True])
def test_session_revocation_or_expiry_after_durable_claim_prevents_write(tmp_path, monkeypatch, expired):
    original_claim = CodingChanges._claim
    valid = [True]

    def claim(changes, identifier, before, after):
        original_claim(changes, identifier, before, after)
        if after == "applying":
            valid[0] = False
            if expired:
                _approved_deadline.set(datetime.now(UTC) - timedelta(seconds=1))

    monkeypatch.setattr(CodingChanges, "_claim", claim)
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = proposal(client, project, root)
        from services.agent_api.app.project_runtime import project_scope, runtime_for
        runtime = runtime_for(app, project)
        from services.agent_api.app.coding_changes import ChangeInput
        token = execution_authorized.set(lambda: valid[0])
        if expired:
            execution_authorized.reset(token)
            token = execution_authorized.set(lambda: True)
        deadline = _approved_deadline.set(None)
        before = (root / "math.py").read_bytes()
        try:
            with project_scope(project), pytest.raises(_InvalidApproval if expired else _PolicyDenied):
                # Direct native effect test isolates the post-claim checkpoint;
                # API grants/dispatch are covered by the other regressions.
                CodingChanges(root, runtime.memory.database_path, project).apply(ChangeInput(**args))
        finally:
            execution_authorized.reset(token)
            _approved_deadline.reset(deadline)
        assert not valid[0]
        assert (root / "math.py").read_bytes() == before


@pytest.mark.anyio
@pytest.mark.skipif(os.name != "nt", reason="Native Windows checkpoint dispatch")
async def test_competing_grants_synchronize_before_actual_handler_dispatch(tmp_path, monkeypatch):
    import services.agent_api.app.tools as tools_module
    from services.agent_api.app.project_runtime import project_scope, runtime_for

    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = proposal(client, project, root)
        runtime = runtime_for(app, project)
        with project_scope(project):
            grants = [(await runtime.executor.execute("studio_apply", args))["approval"]["request_id"] for _ in range(2)]
            for grant in grants:
                runtime.memory.decide_approval(grant, True)
            gate = asyncio.Event()
            arrivals = []
            wait_for = asyncio.wait_for

            async def synchronized(awaitable, timeout):
                arrivals.append(True)
                if len(arrivals) == 2:
                    gate.set()
                await gate.wait()
                return await wait_for(awaitable, timeout)

            monkeypatch.setattr(tools_module.asyncio, "wait_for", synchronized)
            results = await asyncio.gather(*(runtime.executor.continue_approved(grant) for grant in grants))
        assert len(arrivals) == 2
        assert sorted(result["status"] for result in results) == ["invalid_arguments", "success"]
        with closing(sqlite3.connect(runtime.memory.database_path)) as db:
            assert db.execute("SELECT status FROM coding_changes WHERE change_id=?", (args["change_id"],)).fetchone()[0] == "applied"


@pytest.mark.skipif(os.name != "nt", reason="Native creation effect boundary")
def test_legacy_new_file_creation_rechecks_expiry_after_route_pinning(tmp_path, monkeypatch):
    import services.agent_api.app.tools as tools_module
    from services.agent_api.app.project_files import ProjectFiles

    scan = ProjectFiles._scan
    deadline = [None]

    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return deadline[0] + timedelta(seconds=1)

    @contextmanager
    def expire_after_pin(files, target, **kwargs):
        with scan(files, target, **kwargs) as items:
            deadline[0] = _approved_deadline.get()
            monkeypatch.setattr(tools_module, "datetime", ExpiredClock)
            yield items

    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        grant = approved(client, project, "write_file", {"path": "new.txt", "content": "must not create"})
        monkeypatch.setattr(ProjectFiles, "_scan", expire_after_pin)
        assert continue_grant(client, project, grant)["status"] == "invalid_approval"
        assert not (root / "new.txt").exists()
        assert (root / "unrelated.txt").read_text() == "existing user edit"


@pytest.mark.skipif(os.name != "nt", reason="Native NTFS handle replacement boundary")
@pytest.mark.parametrize("tool", ["studio_apply", "studio_rollback", "write_file"])
def test_link_created_at_final_effect_boundary_retains_original_outside_bytes(tmp_path, monkeypatch, tool):
    import services.agent_api.app.native_replace as native

    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = proposal(client, project, root)
        if tool == "studio_rollback":
            grant = approved(client, project, "studio_apply", args)
            assert continue_grant(client, project, grant)["status"] == "success"
        outside = tmp_path / "outside.txt"
        before = (root / "math.py").read_bytes()
        rename = native.rename_replacement
        linked = []

        def add_alias_then_rename(kernel, file, target, exists, parent):
            if target.name.startswith(".jarvis-backup-"):
                os.link(root / "math.py", outside)
                linked.append(True)
            return rename(kernel, file, target, exists, parent)

        monkeypatch.setattr(native, "rename_replacement", add_alias_then_rename)
        if tool == "write_file":
            args = {"path": "math.py", "content": "legacy approved content", "overwrite": True}
        grant = approved(client, project, tool, args)
        assert continue_grant(client, project, grant)["status"] == "success"
        assert linked == [True]
        assert outside.read_bytes() == before
        assert (root / "math.py").read_bytes() != before
        assert not list(root.glob(".jarvis-stage-*"))
        backups = list(root.glob(".jarvis-backup-*"))
        assert len(backups) == (2 if tool == "studio_rollback" else 1)
        assert any(path.read_bytes() == before and path.stat().st_ino == outside.stat().st_ino for path in backups)


@pytest.mark.skipif(os.name != "nt", reason="Native rename compensation boundary")
@pytest.mark.parametrize("later_user_file", [False, True])
def test_failed_replacement_restores_original_or_preserves_later_file_and_backup(tmp_path, monkeypatch, later_user_file):
    import services.agent_api.app.native_replace as native

    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        original = (root / "math.py").read_bytes()
        args = proposal(client, project, root)
        grant = approved(client, project, "studio_apply", args)
        rename = native.rename_replacement
        attempts = []

        def interrupt_second_rename(kernel, file, target, exists, parent):
            attempts.append(target.name)
            if len(attempts) == 2:
                if later_user_file:
                    (root / "math.py").write_text("later user file")
                raise ValueError("fixture failure between handle renames")
            return rename(kernel, file, target, exists, parent)

        monkeypatch.setattr(native, "rename_replacement", interrupt_second_rename)
        assert continue_grant(client, project, grant)["status"] == "invalid_arguments"
        backups = list(root.glob(".jarvis-backup-*"))
        if later_user_file:
            assert (root / "math.py").read_text() == "later user file"
            assert len(backups) == 1 and backups[0].read_bytes() == original
            assert all(not e["name"].startswith(".jarvis-") for e in execute(client, project, "studio_list", {"path": ""})["result"]["entries"])
        else:
            assert (root / "math.py").read_bytes() == original and backups == []
        state = execute(client, project, "studio_changes", {})["result"]["changes"]
        assert state[0]["status"] == "applying"


@pytest.mark.skipif(os.name != "nt", reason="Native stream preservation contract")
def test_checkpoint_rejects_additional_streams_without_losing_them(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        stream = str(root / "math.py") + ":user-data"
        with open(stream, "wb") as file:
            file.write(b"preserved alternate stream")
        args = proposal(client, project, root)
        grant = approved(client, project, "studio_apply", args)
        before = (root / "math.py").read_bytes()
        assert continue_grant(client, project, grant)["status"] == "invalid_arguments"
        assert (root / "math.py").read_bytes() == before
        with open(stream, "rb") as file:
            assert file.read() == b"preserved alternate stream"


@pytest.mark.skipif(os.name != "nt", reason="Native stage ownership boundary")
def test_failed_stage_cleanup_preserves_file_created_after_handle_release(tmp_path, monkeypatch):
    import services.agent_api.app.native_replace as native
    from services.agent_api.app.project_files import ProjectFiles

    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "original.txt"
    target.write_bytes(b"original")
    later = []

    class TransferAfterClose(native.ExitStack):
        def close(self):
            stages = list(root.glob(".jarvis-stage-*"))
            super().close()
            if stages and not later:
                stages[0].unlink(missing_ok=True)
                stages[0].write_bytes(b"later user file")
                later.append(stages[0])

    monkeypatch.setattr(native, "ExitStack", TransferAfterClose)

    def fail_commit():
        raise ValueError("fixture failure before commit")

    with pytest.raises(ValueError, match="fixture failure"):
        native.replace(ProjectFiles(root), target, b"replacement", expected=b"original", before_commit=fail_commit)
    assert target.read_bytes() == b"original"
    assert len(later) == 1 and later[0].read_bytes() == b"later user file"


@pytest.mark.skipif(os.name != "nt", reason="Native staging security boundary")
def test_original_security_is_preserved_before_any_staging_bytes(tmp_path, monkeypatch):
    import services.agent_api.app.native_replace as native
    from services.agent_api.app.project_files import ProjectFiles

    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "original.txt"
    target.write_bytes(b"original")
    metadata = native._metadata
    inspected = []

    def inspect_empty_stage(kernel, source, destination):
        assert os.fstat(destination.fileno()).st_size == 0
        metadata(kernel, source, destination)
        inspected.append(True)

    monkeypatch.setattr(native, "_metadata", inspect_empty_stage)
    native.replace(ProjectFiles(root), target, b"replacement", expected=b"original")
    assert inspected == [True]
    assert target.read_bytes() == b"replacement"


@pytest.mark.skipif(os.name != "nt", reason="Native protected ACL preservation")
def test_checkpoint_preserves_restricted_acl_and_creation_time(tmp_path, monkeypatch):
    import ctypes

    import services.agent_api.app.native_replace as native
    from services.agent_api.app.project_files import ProjectFiles

    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "original.txt"
    target.write_bytes(b"original")

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.GetFileSecurityW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_wchar_p), ctypes.c_void_p]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    advapi.SetFileSecurityW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p]
    kernel = native._kernel()
    kernel.LocalFree.argtypes = [ctypes.c_void_p]

    def security(path):
        descriptor = ctypes.create_string_buffer(65536)
        needed = ctypes.c_uint32()
        assert advapi.GetFileSecurityW(str(path), 7, descriptor, len(descriptor), ctypes.byref(needed))
        output = ctypes.c_wchar_p()
        assert advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 7, ctypes.byref(output), None)
        try:
            # Windows may add the auto-inherited bookkeeping flag when
            # copying a protected DACL. Compare owner/group, protection and
            # every ACE; this flag does not grant an additional permission.
            return output.value.replace("D:PAI", "D:P")
        finally:
            kernel.LocalFree(output)

    # Restrict only this disposable fixture, never repository/user persistence.
    owner = security(target).split("O:", 1)[1].split("G:", 1)[0]
    protected = ctypes.c_void_p()
    assert advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(f"D:P(A;;FA;;;{owner})", 1, ctypes.byref(protected), None)
    try:
        assert advapi.SetFileSecurityW(str(target), 0x80000004, protected)
    finally:
        kernel.LocalFree(protected)
    original_security = security(target)
    created = target.stat().st_birthtime_ns
    metadata = native._metadata
    inspected = []

    def verify_stage_security(kernel, source, destination):
        metadata(kernel, source, destination)
        stage = next(root.glob(".jarvis-stage-*"))
        assert stage.stat().st_size == 0
        assert security(stage) == original_security
        inspected.append(True)

    monkeypatch.setattr(native, "_metadata", verify_stage_security)
    native.replace(ProjectFiles(root), target, b"replacement", expected=b"original")
    assert inspected == [True]
    assert security(target) == original_security
    assert target.stat().st_birthtime_ns == created


@pytest.mark.skipif(os.name != "nt", reason="Native read-only metadata contract")
def test_checkpoint_refuses_read_only_original_without_mutation(tmp_path):
    import stat

    import services.agent_api.app.native_replace as native
    from services.agent_api.app.project_files import ProjectFiles

    target = tmp_path / "readonly.txt"
    target.write_bytes(b"original")
    target.chmod(stat.S_IREAD)
    try:
        with pytest.raises(ValueError, match="Read-only"):
            native.replace(ProjectFiles(tmp_path), target, b"replacement", expected=b"original")
        assert target.read_bytes() == b"original"
        assert not list(tmp_path.glob(".jarvis-*"))
    finally:
        target.chmod(stat.S_IWRITE)


@pytest.mark.skipif(os.name != "nt", reason="Native cleanup failure preservation")
def test_failed_handle_cleanup_retains_stage_instead_of_path_deletion(tmp_path, monkeypatch):
    import services.agent_api.app.native_replace as native
    from services.agent_api.app.project_files import ProjectFiles

    target = tmp_path / "original.txt"
    target.write_bytes(b"original")
    real_kernel = native._kernel()

    class RefuseDisposition:
        def __getattr__(self, name):
            return getattr(real_kernel, name)

        def SetFileInformationByHandle(self, handle, kind, data, size):
            if kind == 4:
                return 0
            return real_kernel.SetFileInformationByHandle(handle, kind, data, size)

    monkeypatch.setattr(native, "_kernel", lambda: RefuseDisposition())

    def fail_commit():
        raise ValueError("fixture commit failure")

    with pytest.raises(ValueError, match="fixture commit failure"):
        native.replace(ProjectFiles(tmp_path), target, b"replacement", expected=b"original", before_commit=fail_commit)
    stages = list(tmp_path.glob(".jarvis-stage-*"))
    assert len(stages) == 1 and stages[0].read_bytes() == b"replacement"
    assert target.read_bytes() == b"original"


@pytest.mark.skipif(os.name != "nt", reason="Native authorization between rename phases")
def test_revocation_between_handle_renames_restores_original(tmp_path, monkeypatch):
    import services.agent_api.app.native_replace as native
    from services.agent_api.app.project_files import ProjectFiles

    target = tmp_path / "original.txt"
    target.write_bytes(b"original")
    valid = [True]
    token = execution_authorized.set(lambda: valid[0])
    rename = native.rename_replacement

    def revoke_after_backup(kernel, file, destination, exists, parent):
        rename(kernel, file, destination, exists, parent)
        if destination.name.startswith(".jarvis-backup-"):
            valid[0] = False

    monkeypatch.setattr(native, "rename_replacement", revoke_after_backup)
    try:
        with pytest.raises(_PolicyDenied):
            native.replace(ProjectFiles(tmp_path), target, b"replacement", expected=b"original")
    finally:
        execution_authorized.reset(token)
    assert target.read_bytes() == b"original"
    assert not list(tmp_path.glob(".jarvis-*"))


@pytest.mark.skipif(os.name != "nt", reason="Native short rename buffer")
@pytest.mark.parametrize("interrupt", [False, True])
def test_one_character_destination_supports_replace_and_compensation(tmp_path, monkeypatch, interrupt):
    import services.agent_api.app.native_replace as native
    from services.agent_api.app.project_files import ProjectFiles

    target = tmp_path / "a"
    target.write_bytes(b"original")
    files = ProjectFiles(tmp_path)
    rename = native.rename_replacement
    attempts = []

    def maybe_interrupt(kernel, file, destination, exists, parent):
        attempts.append(destination.name)
        if interrupt and len(attempts) == 2:
            raise ValueError("fixture interruption")
        return rename(kernel, file, destination, exists, parent)

    monkeypatch.setattr(native, "rename_replacement", maybe_interrupt)
    if interrupt:
        with pytest.raises(ValueError, match="fixture interruption"):
            native.replace(files, target, b"replacement", expected=b"original")
    else:
        native.replace(files, target, b"replacement", expected=b"original")
        assert target.read_bytes() == b"replacement"
        native.replace(files, target, b"original", expected=b"replacement")
    assert target.read_bytes() == b"original"
    assert not list(tmp_path.glob(".jarvis-stage-*"))
    backups = list(tmp_path.glob(".jarvis-backup-*"))
    assert len(backups) == (0 if interrupt else 2)
    assert {path.read_bytes() for path in backups} == (set() if interrupt else {b"original", b"replacement"})
