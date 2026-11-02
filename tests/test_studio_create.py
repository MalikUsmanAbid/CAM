"""Approved new-file changes never overwrite or delete later user files."""

import os

import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.main import app
from tests.test_coding_changes import approved, continue_grant, execute, fixture_project

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows checkpoint writes")


def shared_stream(path, content=b"later"):
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(path), 0x40000000, 7, None, 1, 0x80, None)
    assert handle not in {None, ctypes.c_void_p(-1).value}
    try:
        count = ctypes.c_uint32()
        assert kernel.WriteFile(handle, content, len(content), ctypes.byref(count), None)
        assert count.value == len(content)
    finally:
        kernel.CloseHandle(handle)


def create_proposal(client, project, content="new fixture source"):
    result = execute(client, project, "studio_propose_create", {"path": "new.py", "content": content})
    assert result["status"] == "success"
    return {key: result["result"][key] for key in ("change_id", "path", "proposal_sha256")}


def test_new_file_requires_exact_approval_and_selective_rollback(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = create_proposal(client, project)
        assert not (root / "new.py").exists()
        grant = approved(client, project, "studio_apply", args)
        assert not (root / "new.py").exists()
        assert continue_grant(client, project, grant)["status"] == "success"
        assert (root / "new.py").read_text() == "new fixture source"
        assert continue_grant(client, project, approved(client, project, "studio_rollback_create", args))["status"] == "success"
        assert not (root / "new.py").exists()
        assert (root / "unrelated.txt").read_text() == "existing user edit"


def test_new_file_appearing_after_approval_is_preserved(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = create_proposal(client, project)
        grant = approved(client, project, "studio_apply", args)
        (root / "new.py").write_text("later user file")
        assert continue_grant(client, project, grant)["status"] == "invalid_arguments"
        assert (root / "new.py").read_text() == "later user file"


@pytest.mark.parametrize("same_content", [False, True])
def test_new_file_rollback_preserves_later_edits_and_recreated_files(tmp_path, same_content):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = create_proposal(client, project)
        assert continue_grant(client, project, approved(client, project, "studio_apply", args))["status"] == "success"
        grant = approved(client, project, "studio_rollback_create", args)
        if same_content:
            (root / "new.py").rename(root / "retained-created-file.py")
        (root / "new.py").write_text("new fixture source" if same_content else "later edit")
        assert continue_grant(client, project, grant)["status"] == "invalid_arguments"
        assert (root / "new.py").exists()


def test_create_proposal_does_not_replace_existing_or_create_directories(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        for path in ("unrelated.txt", "missing/new.py", ".env", "../outside.py"):
            assert execute(client, project, "studio_propose_create", {"path": path, "content": "fixture"})["status"] == "invalid_arguments"
        assert not (root / "missing").exists()
        assert (root / "unrelated.txt").read_text() == "existing user edit"


def test_created_file_rollback_preserves_stream_added_after_durable_claim(tmp_path, monkeypatch):
    from pathlib import Path

    from services.agent_api.app.coding_changes import CodingChanges

    original = CodingChanges._claim
    injected = []

    def add_stream(changes, identifier, before, after, identity=None):
        original(changes, identifier, before, after, identity)
        if after == "rolling_back":
            import ctypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
            kernel.CreateFileW.restype = ctypes.c_void_p
            kernel.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            stream = kernel.CreateFileW(str(changes.files.guard.root / "new.py") + ":later-user-stream", 0x40000000, 7, None, 1, 0x80, None)
            assert stream not in {None, ctypes.c_void_p(-1).value}
            try:
                count = ctypes.c_uint32()
                assert kernel.WriteFile(stream, b"later user data", 15, ctypes.byref(count), None)
                assert count.value == 15
            finally:
                kernel.CloseHandle(stream)
            injected.append(True)

    monkeypatch.setattr(CodingChanges, "_claim", add_stream)
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = create_proposal(client, project)
        assert continue_grant(client, project, approved(client, project, "studio_apply", args))["status"] == "success"
        outcome = continue_grant(client, project, approved(client, project, "studio_rollback_create", args))
        assert injected
        assert outcome["status"] == "invalid_arguments"
        assert (root / "new.py").read_text() == "new fixture source"
        assert Path(str(root / "new.py") + ":later-user-stream").read_bytes() == b"later user data"


def test_existing_replacement_retains_original_stream_added_after_claim(tmp_path):
    import ctypes
    from pathlib import Path

    from services.agent_api.app.native_replace import replace
    from services.agent_api.app.project_files import ProjectFiles

    target = tmp_path / "source.py"
    target.write_bytes(b"before")

    def add_stream():
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.CreateFileW(str(target) + ":late-stream", 0x40000000, 7, None, 1, 0x80, None)
        assert handle not in {None, ctypes.c_void_p(-1).value}
        try:
            count = ctypes.c_uint32()
            assert kernel.WriteFile(handle, b"later", 5, ctypes.byref(count), None)
            assert count.value == 5
        finally:
            kernel.CloseHandle(handle)

    with pytest.raises(RuntimeError, match="backup retained"):
        replace(ProjectFiles(tmp_path), target, b"after", expected=b"before", before_commit=add_stream)
    assert target.read_bytes() == b"after"
    backups = list(tmp_path.glob(".jarvis-backup-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"before"
    assert Path(str(backups[0]) + ":late-stream").read_bytes() == b"later"


def test_file_created_at_native_commit_boundary_is_never_overwritten(tmp_path, monkeypatch):
    from services.agent_api.app.coding_changes import CodingChanges

    original = CodingChanges._claim

    def insert_later_file(changes, identifier, before, after, identity=None):
        original(changes, identifier, before, after, identity)
        if after == "applying":
            (changes.files.guard.root / "new.py").write_text("native boundary user file")

    monkeypatch.setattr(CodingChanges, "_claim", insert_later_file)
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = create_proposal(client, project)
        grant = approved(client, project, "studio_apply", args)
        assert continue_grant(client, project, grant)["status"] == "invalid_arguments"
        assert (root / "new.py").read_text() == "native boundary user file"
        assert not list(root.glob(".jarvis-stage-*"))


def test_created_file_cannot_be_deleted_through_edit_rollback_tool(tmp_path):
    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = create_proposal(client, project)
        assert continue_grant(client, project, approved(client, project, "studio_apply", args))["status"] == "success"
        assert continue_grant(client, project, approved(client, project, "studio_rollback", args))["status"] == "invalid_arguments"
        assert (root / "new.py").exists()


@pytest.mark.parametrize("existing_replacement", [False, True])
def test_retirement_preserves_stream_added_after_final_query_without_physical_deletion(tmp_path, monkeypatch, existing_replacement):
    from pathlib import Path

    import services.agent_api.app.native_replace as native
    from services.agent_api.app.project_files import ProjectFiles

    target = tmp_path / "target.py"
    target.write_bytes(b"original")
    stat = target.stat()
    original_kernel = native._kernel
    injected = []
    deletion_calls = []

    class Kernel:
        def __init__(self):
            self.real = original_kernel()

        def __getattr__(self, key):
            return getattr(self.real, key)

        def SetFileInformationByHandle(self, handle, kind, pointer, size):
            if kind == 4:
                deletion_calls.append(True)
                return 0  # Physical deletion/compensation is deliberately unavailable.
            return self.real.SetFileInformationByHandle(handle, kind, pointer, size)

        def GetFileInformationByHandleEx(self, handle, kind, pointer, size):
            result = self.real.GetFileInformationByHandleEx(handle, kind, pointer, size)
            backups = list(tmp_path.glob(".jarvis-backup-*"))
            if kind == 7 and backups and not injected:
                shared_stream(str(backups[0]) + ":disposition-race")
                injected.append(backups[0])
            return result

    monkeypatch.setattr(native, "_kernel", Kernel)
    if existing_replacement:
        native.replace(ProjectFiles(tmp_path), target, b"replacement", expected=b"original")
    else:
        native.remove_created(ProjectFiles(tmp_path), target, b"original", (stat.st_dev, stat.st_ino), before_commit=lambda: None)
    assert not deletion_calls
    assert injected
    assert injected[0].read_bytes() == b"original"
    assert Path(str(injected[0]) + ":disposition-race").read_bytes() == b"later"


@pytest.mark.anyio
async def test_competing_new_file_grants_synchronize_at_dispatch(tmp_path, monkeypatch):
    import asyncio

    import services.agent_api.app.tools as tools_module
    from services.agent_api.app.project_runtime import project_scope, runtime_for

    with TestClient(app) as client:
        project, root = fixture_project(client, tmp_path)
        args = create_proposal(client, project)
        runtime = runtime_for(app, project)
        with project_scope(project):
            grants = [(await runtime.executor.execute("studio_apply", args))["approval"]["request_id"] for _ in range(2)]
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
        assert (root / "new.py").read_text() == "new fixture source"


def test_retirement_keeps_owned_inode_and_bytes(tmp_path):
    import services.agent_api.app.native_replace as native
    from services.agent_api.app.project_files import ProjectFiles

    target = tmp_path / "pending.py"
    target.write_bytes(b"preserved")
    identity = target.stat()
    backup = native.remove_created(ProjectFiles(tmp_path), target, b"preserved", (identity.st_dev, identity.st_ino), before_commit=lambda: None)
    assert not target.exists()
    assert backup.stat().st_ino == identity.st_ino
    assert backup.read_bytes() == b"preserved"


@pytest.mark.parametrize("expired", [False, True])
def test_created_rollback_rechecks_authorization_after_last_stream_query(tmp_path, monkeypatch, expired):
    from datetime import UTC, datetime, timedelta

    import services.agent_api.app.native_replace as native
    from services.agent_api.app.project_files import ProjectFiles
    from services.agent_api.app.session_security import execution_authorized
    from services.agent_api.app.tools import _approved_deadline, _InvalidApproval, _PolicyDenied

    target = tmp_path / "created.py"
    target.write_bytes(b"created")
    identity = target.stat()
    real = native._kernel()
    queries = []
    valid = [True]

    class Kernel:
        def __getattr__(self, key):
            return getattr(real, key)

        def GetFileInformationByHandleEx(self, handle, kind, pointer, size):
            result = real.GetFileInformationByHandleEx(handle, kind, pointer, size)
            if kind == 7:
                queries.append(True)
                if len(queries) == 2:
                    if expired:
                        _approved_deadline.set(datetime.now(UTC) - timedelta(seconds=1))
                    else:
                        valid[0] = False
            return result

    monkeypatch.setattr(native, "_kernel", lambda: Kernel())
    authorized = execution_authorized.set(lambda: valid[0])
    deadline = _approved_deadline.set(None)
    try:
        with pytest.raises(_InvalidApproval if expired else _PolicyDenied):
            native.remove_created(ProjectFiles(tmp_path), target, b"created", (identity.st_dev, identity.st_ino), before_commit=lambda: None)
    finally:
        execution_authorized.reset(authorized)
        _approved_deadline.reset(deadline)
    assert len(queries) == 2
    assert target.read_bytes() == b"created"
    assert not list(tmp_path.glob(".jarvis-backup-*"))
