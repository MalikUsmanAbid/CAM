"""Run only disposable Python children; never invoke user project commands."""

import asyncio
import hashlib
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows process jobs")


@pytest.mark.anyio
async def test_private_worker_input_is_received_on_stdin_not_command_line(tmp_path):
    from services.agent_api.app.windows_commands import run_process

    payload = b'{"fixture":"private text over pipe"}\n'
    expected = hashlib.sha256(payload).hexdigest()
    script = f"import sys,hashlib; data=sys.stdin.buffer.readline(); assert hashlib.sha256(data).hexdigest()=={expected!r}; print('private input received'); assert len(sys.argv)==1"
    result = await run_process(Path(sys.executable), ['-I', '-c', script], tmp_path,
        timeout=5, output_limit=2048, input_data=payload)
    assert result['status'] == 'succeeded' and result['output'].strip() == 'private input received'


@pytest.mark.anyio
async def test_private_worker_cancellation_terminates_before_fixture_write(tmp_path):
    from services.agent_api.app.windows_commands import run_process

    script = 'import sys,time,os; from pathlib import Path; sys.stdin.buffer.readline(); Path(\'worker-started.tmp\').write_text(str(os.getpid())); os.replace(\'worker-started.tmp\', \'worker-started\'); exec("while not Path(\'release-worker\').exists(): time.sleep(0.02)"); Path(\'forbidden-effect\').touch()'
    task = asyncio.create_task(run_process(Path(sys.executable), ['-I', '-c', script], tmp_path,
        timeout=20, output_limit=2048, input_data=b'{"fixture":true}\n'))
    pid = None
    try:
        for _ in range(500):
            try:
                pid = int((tmp_path / 'worker-started').read_text())
            except (OSError, ValueError):
                await asyncio.sleep(0.02)
                continue
            if pid:
                break
        assert pid
        import _winapi
        # Retain identity before cancellation; a completed process ID may be reused.
        handle = _winapi.OpenProcess(0x100000, False, pid)
        try:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert _winapi.WaitForSingleObject(handle, 0) == 0
        finally:
            _winapi.CloseHandle(handle)
        assert not (tmp_path / 'forbidden-effect').exists()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_unconfirmed_cleanup_does_not_report_clean_cancellation(tmp_path, monkeypatch):
    import services.agent_api.app.windows_commands as commands

    original = commands.terminate_owned_job

    def unconfirmed(kernel, job):
        original(kernel, job)
        raise RuntimeError('fixture termination confirmation failed')

    monkeypatch.setattr(commands, 'terminate_owned_job', unconfirmed)
    script = "from pathlib import Path; import time; Path('started').touch(); time.sleep(30)"
    task = asyncio.create_task(commands.run_process(Path(sys.executable), ['-c', script], tmp_path,
        timeout=20, output_limit=2048))
    try:
        for _ in range(500):
            if (tmp_path / 'started').exists():
                break
            await asyncio.sleep(0.02)
        assert (tmp_path / 'started').exists()
        task.cancel()
        with pytest.raises(RuntimeError, match='cleanup requires inspection'):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_command_captures_actual_exit_and_bounded_output(tmp_path):
    from services.agent_api.app.windows_commands import run_process

    result = await run_process(Path(sys.executable), ["-c", "print('fixture execution'); raise SystemExit(7)"], tmp_path, timeout=5, output_limit=2048)
    assert result["exit_code"] == 7
    assert result["status"] == "failed"
    assert "fixture execution" in result["output"]


@pytest.mark.anyio
async def test_job_assignment_failure_never_runs_suspended_child(tmp_path, monkeypatch):
    import services.agent_api.app.windows_commands as commands

    marker = tmp_path / "must-not-exist"
    monkeypatch.setattr(commands, "assign_job", lambda *_: (_ for _ in ()).throw(ValueError("fixture assignment failure")))
    with pytest.raises(ValueError, match="fixture assignment failure"):
        await commands.run_process(Path(sys.executable), ["-c", "from pathlib import Path; Path('must-not-exist').write_text('unsafe')"], tmp_path, timeout=5, output_limit=2048)
    assert not marker.exists()


@pytest.mark.anyio
async def test_timeout_terminates_owned_child_tree(tmp_path):
    from services.agent_api.app.windows_commands import run_process

    child = "from pathlib import Path; import time; time.sleep(2); Path('escaped').write_text('unsafe')"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(10)"
    result = await run_process(Path(sys.executable), ["-c", parent], tmp_path, timeout=0.4, output_limit=2048)
    assert result["status"] == "timed_out"
    await asyncio.sleep(2.2)
    assert not (tmp_path / "escaped").exists()


@pytest.mark.anyio
async def test_cancellation_kills_child_before_cooperative_side_effect(tmp_path):
    from services.agent_api.app.windows_commands import run_process

    task = asyncio.create_task(run_process(Path(sys.executable), ["-c", "import time; from pathlib import Path; time.sleep(2); Path('cancelled-effect').write_text('unsafe')"], tmp_path, timeout=5, output_limit=2048))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(2)
    assert not (tmp_path / "cancelled-effect").exists()


@pytest.mark.anyio
async def test_output_overflow_stops_execution_and_keeps_bounded_result(tmp_path):
    from services.agent_api.app.windows_commands import run_process

    result = await run_process(Path(sys.executable), ["-c", "import sys,time; sys.stdout.write('x'*100000); sys.stdout.flush(); time.sleep(5)"], tmp_path, timeout=5, output_limit=1024)
    assert result["status"] == "output_limit"
    assert len(result["output"].encode()) <= 1024


@pytest.mark.anyio
async def test_backend_secrets_are_not_inherited_by_command(tmp_path, monkeypatch):
    from services.agent_api.app.windows_commands import run_process

    monkeypatch.setenv("JARVIS_FIXTURE_SECRET", "never inherit this")
    result = await run_process(Path(sys.executable), ["-c", "import os; print(os.environ.get('JARVIS_FIXTURE_SECRET','absent'))"], tmp_path, timeout=5, output_limit=2048)
    assert result["status"] == "succeeded"
    assert result["output"].strip() == "absent"


@pytest.mark.anyio
async def test_final_authorization_failure_runs_no_child_instruction(tmp_path, monkeypatch):
    import services.agent_api.app.windows_commands as commands

    allowed = [True]
    assignment = commands.assign_job

    def revoke_after_assignment(*args):
        assignment(*args)
        allowed[0] = False

    def authorize():
        if not allowed[0]:
            raise ValueError("fixture authorization revoked")

    monkeypatch.setattr(commands, "assign_job", revoke_after_assignment)
    with pytest.raises(ValueError, match="fixture authorization revoked"):
        await commands.run_process(Path(sys.executable), ["-c", "from pathlib import Path; Path('forbidden').write_text('unsafe')"], tmp_path, timeout=5, output_limit=2048, authorize=authorize)
    assert not (tmp_path / "forbidden").exists()


@pytest.mark.anyio
async def test_shared_queue_rechecks_authorization_after_wait(tmp_path):
    from services.agent_api.app.windows_commands import run_process

    started = tmp_path / "started"
    first = asyncio.create_task(run_process(Path(sys.executable), ["-c", "from pathlib import Path; import time; Path('started').touch(); time.sleep(0.5)"], tmp_path, timeout=5, output_limit=2048))
    for _ in range(500):
        if started.exists():
            break
        await asyncio.sleep(0.02)
    assert started.exists()
    allowed = [True]

    def authorize():
        if not allowed[0]:
            raise ValueError("fixture queued grant expired")

    second = asyncio.create_task(run_process(Path(sys.executable), ["-c", "from pathlib import Path; Path('queued-effect').touch()"], tmp_path, timeout=5, output_limit=2048, authorize=authorize))
    await asyncio.sleep(0.1)
    assert not (tmp_path / "queued-effect").exists()
    allowed[0] = False
    assert (await first)["status"] == "succeeded"
    with pytest.raises(ValueError, match="fixture queued grant expired"):
        await second
    assert not (tmp_path / "queued-effect").exists()


@pytest.mark.anyio
async def test_exit_code_259_is_a_completed_failure_not_a_running_process(tmp_path):
    from services.agent_api.app.windows_commands import run_process

    result = await run_process(Path(sys.executable), ["-c", "raise SystemExit(259)"], tmp_path, timeout=5, output_limit=2048)
    assert result["status"] == "failed"
    assert result["exit_code"] == 259


@pytest.mark.anyio
@pytest.mark.parametrize("limit", [1.5, True])
async def test_invalid_output_limit_is_rejected_before_launch(tmp_path, limit):
    from services.agent_api.app.windows_commands import run_process

    with pytest.raises(ValueError, match="limits"):
        await run_process(Path(sys.executable), ["-c", "from pathlib import Path; Path('forbidden').touch()"], tmp_path, timeout=5, output_limit=limit)
    assert not (tmp_path / "forbidden").exists()


@pytest.mark.anyio
async def test_cleanup_closes_all_handles_even_if_termination_reports_error(tmp_path, monkeypatch):
    import _winapi

    import services.agent_api.app.windows_commands as commands

    real_kernel = commands._kernel()
    closed = []

    class TrackingKernel:
        def __getattr__(self, name):
            return getattr(real_kernel, name)

        def CloseHandle(self, handle):
            closed.append(handle)
            return real_kernel.CloseHandle(handle)

    terminate = _winapi.TerminateProcess

    def terminate_then_report_error(process, code):
        terminate(process, code)  # Keep the disposable child safe.
        raise OSError("fixture termination reporting failure")

    monkeypatch.setattr(commands, "_kernel", lambda: TrackingKernel())
    monkeypatch.setattr(_winapi, "TerminateProcess", terminate_then_report_error)
    monkeypatch.setattr(commands, "assign_job", lambda *_: (_ for _ in ()).throw(ValueError("fixture assignment failure")))
    with pytest.raises(RuntimeError, match="cleanup requires inspection"):
        await commands.run_process(Path(sys.executable), ["-c", "from pathlib import Path; Path('forbidden').touch()"], tmp_path, timeout=5, output_limit=2048)
    assert len(closed) == 3 and len(set(closed)) == 3
    assert not (tmp_path / "forbidden").exists()


@pytest.mark.anyio
async def test_late_output_drain_error_cannot_report_success(tmp_path, monkeypatch):
    import threading
    import time

    import services.agent_api.app.windows_commands as commands

    read = commands.os.read
    late_failure = []

    def fail_on_reader_eof(fd, size):
        data = read(fd, size)
        if not data and threading.current_thread() is not threading.main_thread():
            time.sleep(0.12)  # Parent exited before the final drain completes.
            late_failure.append(True)
            raise OSError("fixture late output failure")
        return data

    monkeypatch.setattr(commands.os, "read", fail_on_reader_eof)
    with pytest.raises(RuntimeError, match="output could not be captured"):
        await commands.run_process(Path(sys.executable), ["-c", "print('fixture')"], tmp_path, timeout=5, output_limit=2048)
    assert late_failure == [True]


@pytest.mark.anyio
async def test_revocation_while_reader_starts_prevents_resume(tmp_path, monkeypatch):
    import services.agent_api.app.windows_commands as commands

    allowed = [True]
    start = commands.threading.Thread.start
    real_kernel = commands._kernel()
    resumed = []

    class TrackingKernel:
        def __getattr__(self, name):
            return getattr(real_kernel, name)

        def ResumeThread(self, handle):
            resumed.append(True)
            return real_kernel.ResumeThread(handle)

    def revoke_during_start(thread):
        start(thread)
        allowed[0] = False

    def authorize():
        if not allowed[0]:
            raise ValueError("fixture reader-start revocation")

    monkeypatch.setattr(commands, "_kernel", lambda: TrackingKernel())
    monkeypatch.setattr(commands.threading.Thread, "start", revoke_during_start)
    with pytest.raises(ValueError, match="fixture reader-start revocation"):
        await commands.run_process(Path(sys.executable), ["-c", "from pathlib import Path; Path('forbidden').touch()"], tmp_path, timeout=5, output_limit=2048, authorize=authorize)
    assert resumed == []
    assert not (tmp_path / "forbidden").exists()


@pytest.mark.anyio
async def test_normal_parent_exit_also_stops_surviving_owned_children(tmp_path):
    from services.agent_api.app.windows_commands import run_process

    child = "from pathlib import Path; import time; time.sleep(2); Path('escaped').touch()"
    parent = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}]); print('parent completed')"
    result = await run_process(Path(sys.executable), ["-c", parent], tmp_path, timeout=5, output_limit=2048)
    assert result["status"] == "succeeded"
    assert "parent completed" in result["output"]
    await asyncio.sleep(2.1)
    assert not (tmp_path / "escaped").exists()


@pytest.mark.anyio
async def test_unrelated_inheritable_handle_does_not_enter_child(tmp_path):
    import msvcrt

    from services.agent_api.app.windows_commands import run_process

    private = tmp_path / "private-fixture.txt"
    private.write_text("never inherited")
    descriptor = os.open(private, os.O_RDONLY)
    handle = msvcrt.get_osfhandle(descriptor)
    os.set_handle_inheritable(handle, True)
    script = f"import ctypes; k=ctypes.WinDLL('kernel32'); k.GetFinalPathNameByHandleW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32]; b=ctypes.create_unicode_buffer(32768); n=k.GetFinalPathNameByHandleW({handle},b,len(b),0); print(b.value if n else 'excluded')"
    try:
        result = await run_process(Path(sys.executable), ["-c", script], tmp_path, timeout=5, output_limit=2048)
        assert result["status"] == "succeeded"
        assert private.name not in result["output"]
    finally:
        os.close(descriptor)


@pytest.mark.anyio
async def test_cancelled_queued_command_never_launches(tmp_path):
    from services.agent_api.app.windows_commands import run_process

    first = asyncio.create_task(run_process(Path(sys.executable), ["-c", "from pathlib import Path; import time; Path('started').touch(); time.sleep(0.5)"], tmp_path, timeout=5, output_limit=2048))
    for _ in range(500):
        if (tmp_path / "started").exists():
            break
        await asyncio.sleep(0.02)
    assert (tmp_path / "started").exists()
    queued = asyncio.create_task(run_process(Path(sys.executable), ["-c", "from pathlib import Path; Path('forbidden').touch()"], tmp_path, timeout=5, output_limit=2048))
    await asyncio.sleep(0.05)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert (await first)["status"] == "succeeded"
    assert not (tmp_path / "forbidden").exists()
