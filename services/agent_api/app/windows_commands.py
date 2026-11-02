"""Bounded Windows process ownership. Job objects are not a filesystem sandbox.

The child is suspended until assignment and final authorization succeed. No
breakaway flags are enabled. Indirect OS services/external effects are outside
this process-tree guarantee. This primitive is not yet a registered tool.
"""

import asyncio
import ctypes
import os
import subprocess
import threading
import time
from weakref import WeakKeyDictionary

_queues = WeakKeyDictionary()


def _kernel():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel.CreateJobObjectW.restype = ctypes.c_void_p
    kernel.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    kernel.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.ResumeThread.argtypes = [ctypes.c_void_p]
    kernel.ResumeThread.restype = ctypes.c_uint32
    kernel.QueryInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
    return kernel


def wait_job_empty(kernel, job, timeout=5):
    """Termination is asynchronous; waiting for the launcher alone misses children."""
    class Accounting(ctypes.Structure):
        _fields_ = [(name, ctypes.c_int64) for name in (
            'user_time', 'kernel_time', 'period_user_time', 'period_kernel_time'
        )] + [(name, ctypes.c_uint32) for name in (
            'page_faults', 'total_processes', 'active_processes', 'terminated_processes'
        )]

    deadline = time.monotonic() + timeout
    while True:
        accounting = Accounting()
        if not kernel.QueryInformationJobObject(job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
            raise RuntimeError('Cannot confirm owned process group termination.')
        if accounting.active_processes == 0:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('Owned process group termination is unconfirmed.')
        time.sleep(0.01)


def terminate_owned_job(kernel, job):
    """Pin current member handles before termination, including launcher children."""
    import _winapi

    class Members(ctypes.Structure):
        _fields_ = [('assigned', ctypes.c_uint32), ('listed', ctypes.c_uint32),
                    ('pids', ctypes.c_size_t * 32)]

    members = Members()
    handles = []
    try:
        if not kernel.QueryInformationJobObject(job, 3, ctypes.byref(members), ctypes.sizeof(members), None):
            raise RuntimeError('Cannot inspect owned process group termination.')
        for pid in members.pids[:members.listed]:
            try:
                handles.append(_winapi.OpenProcess(0x100000, False, pid))
            except OSError as error:
                if error.winerror != 87:  # An already retired member needs no wait.
                    raise
        if not kernel.TerminateJobObject(job, 1):
            raise RuntimeError('Cannot terminate owned process group.')
        wait_job_empty(kernel, job)
        deadline = time.monotonic() + 5
        for handle in handles:
            if _winapi.WaitForSingleObject(handle, max(0, int((deadline - time.monotonic()) * 1000))) != 0:
                raise RuntimeError('Owned process termination signal is unconfirmed.')
    finally:
        # Even if inspection failed, request termination before releasing ownership.
        kernel.TerminateJobObject(job, 1)
        for handle in handles:
            _winapi.CloseHandle(handle)


def create_job(kernel, *, private_worker=False):
    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", ctypes.c_uint32), ("min_working", ctypes.c_size_t), ("max_working", ctypes.c_size_t), ("active_limit", ctypes.c_uint32), ("affinity", ctypes.c_size_t), ("priority", ctypes.c_uint32), ("scheduling", ctypes.c_uint32)]

    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ValueError("Cannot create owned process job.")
    limits = Extended()
    limits.basic.flags = 0x2000 | 8  # KILL_ON_JOB_CLOSE and bounded active processes
    limits.basic.active_limit = 32
    if private_worker:
        # A frozen one-file backend uses a bootloader plus its interpreter.
        # Both remain owned; no breakaway is enabled.
        limits.basic.active_limit = 2
        limits.basic.flags |= 0x100 | 0x200  # PROCESS_MEMORY and JOB_MEMORY
        limits.process_memory = limits.job_memory = 384 * 1024 * 1024
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        kernel.CloseHandle(job)
        raise ValueError("Cannot enforce owned process limits.")
    return job


def assign_job(kernel, job, process):
    if not kernel.AssignProcessToJobObject(job, process):
        raise ValueError("Cannot assign suspended command to owned job.")


def command_environment(executable, profile=None):
    # Environment values are selected explicitly; provider/session credentials
    # and arbitrary backend variables never enter the child environment.
    environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP", "COMSPEC") if key in os.environ}
    system = os.environ.get("SystemRoot", r"C:\Windows")
    environment["PATH"] = str(executable.parent) + os.pathsep + os.path.join(system, "System32")
    environment["PYTHONIOENCODING"] = "utf-8"
    if profile == "git_inspection":
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="NUL", GIT_ATTR_NOSYSTEM="1", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    elif profile is not None:
        raise ValueError("Unsupported command environment profile.")
    return environment


async def run_process(executable, arguments, cwd, *, timeout, output_limit, authorize=lambda: None, environment_profile=None, input_data=None):
    # Supported backend runs one event loop/process. The queue is shared by
    # its project runtimes and does not grant authorization to queued work.
    loop = asyncio.get_running_loop()
    lock = _queues.setdefault(loop, asyncio.Lock())
    async with lock:
        authorize()
        return await _run_owned_process(executable, arguments, cwd, timeout=timeout, output_limit=output_limit, authorize=authorize, environment_profile=environment_profile, input_data=input_data)


async def _run_owned_process(executable, arguments, cwd, *, timeout, output_limit, authorize, environment_profile=None, input_data=None):
    if os.name != "nt":
        raise ValueError("Verified command supervision requires Windows.")
    if not executable.is_absolute() or not executable.is_file() or executable.suffix.lower() != ".exe":
        raise ValueError("Command requires an existing absolute executable path.")
    if type(timeout) not in (int, float) or not 0 < timeout <= 120 or type(output_limit) is not int or not 1 <= output_limit <= 64000:
        raise ValueError("Command limits are outside the supported contract.")
    if not isinstance(arguments, list) or len(arguments) > 100 or any(not isinstance(arg, str) or "\x00" in arg or len(arg) > 4000 for arg in arguments):
        raise ValueError("Command arguments are invalid.")
    if input_data is not None and (not isinstance(input_data, bytes) or not 1 <= len(input_data) <= 8192):
        raise ValueError('Private worker input must be bounded bytes.')
    import _winapi
    import msvcrt

    kernel = _kernel()
    job = create_job(kernel, private_worker=True) if input_data is not None else create_job(kernel)
    process = thread = None
    stdout_read = stdout_write = stdin = input_write = None
    reader = writer = None
    captured = bytearray()
    overflow = threading.Event()
    read_failed = threading.Event()
    write_failed = threading.Event()

    def read_output():
        try:
            while chunk := os.read(stdout_read, 4096):
                available = output_limit - len(captured)
                captured.extend(chunk[:max(0, available)])
                if len(chunk) > available:
                    overflow.set()
        except OSError:
            read_failed.set()

    try:
        authorize()
        stdout_read, stdout_write = os.pipe()
        if input_data is None:
            stdin = os.open(os.devnull, os.O_RDONLY)
        else:
            stdin, input_write = os.pipe()
        handles = [msvcrt.get_osfhandle(stdout_write), msvcrt.get_osfhandle(stdin)]
        for handle in handles:
            os.set_handle_inheritable(handle, True)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESTDHANDLES
        startup.hStdOutput = startup.hStdError = handles[0]
        startup.hStdInput = handles[1]
        startup.lpAttributeList = {"handle_list": handles}
        command = subprocess.list2cmdline([str(executable), *arguments])
        flags = 4 | 0x08000000 | 0x00080000  # suspended, no window, explicit handles
        process, thread, _, _ = _winapi.CreateProcess(str(executable), command, None, None, True, flags, command_environment(executable, environment_profile), str(cwd), startup)
        os.close(stdout_write)
        stdout_write = None
        os.close(stdin)
        stdin = None
        assign_job(kernel, job, process)
        authorize()  # exact dispatch boundary, before the first instruction
        reader = threading.Thread(target=read_output, daemon=True, name="jarvis-command-output")
        reader.start()
        authorize()  # Starting the reader can release the GIL or block.
        if kernel.ResumeThread(thread) == 0xffffffff:
            raise ValueError("Cannot resume owned command.")
        if input_write is not None:
            # Deliver only over the explicitly inherited pipe, never argv or
            # a temp file. A writer prevents pipe back-pressure blocking the
            # event loop that owns cancellation and the job handle.
            def write_input(fd=input_write, payload=input_data):
                try:
                    offset = 0
                    while offset < len(payload):
                        count = os.write(fd, payload[offset:])
                        if count <= 0:
                            raise OSError('Worker input pipe closed.')
                        offset += count
                except OSError:
                    write_failed.set()
                finally:
                    os.close(fd)
            authorize()
            writer = threading.Thread(target=write_input, daemon=True, name='jarvis-private-worker-input')
            writer.start()
            input_write = None  # The writer owns this descriptor after start.
        started = time.monotonic()
        status = "succeeded"
        exit_code = None
        while True:
            authorize()
            if overflow.is_set():
                status = "output_limit"
                break
            if read_failed.is_set():
                raise RuntimeError("Command output could not be captured.")
            if write_failed.is_set():
                raise RuntimeError('Private worker input could not be delivered.')
            # An exited process may itself return 259 (STILL_ACTIVE). Its
            # signalled process handle, rather than that value, proves exit.
            if _winapi.WaitForSingleObject(process, 0) == 0:
                code = _winapi.GetExitCodeProcess(process)
                exit_code = code
                status = "succeeded" if code == 0 else "failed"
                break
            if time.monotonic() - started >= timeout:
                status = "timed_out"
                break
            await asyncio.sleep(0.02)
        kernel.TerminateJobObject(job, 1)
        _winapi.WaitForSingleObject(process, 2000)
        await asyncio.to_thread(reader.join, 2)
        if reader.is_alive():
            raise RuntimeError("Owned command output did not close.")
        if read_failed.is_set():
            raise RuntimeError("Command output could not be captured.")
        if writer is not None:
            await asyncio.to_thread(writer.join, 2)
            if writer.is_alive() or write_failed.is_set():
                raise RuntimeError('Private worker input did not close cleanly.')
        if overflow.is_set():
            status = "output_limit"
        output = captured.decode("utf-8", errors="replace").encode("utf-8")
        text = output[:output_limit].decode("utf-8", errors="ignore")
        return {"status": status, "exit_code": exit_code, "output": text, "truncated": overflow.is_set() or len(output) > output_limit}
    finally:
        cleanup_errors = []
        try:
            terminate_owned_job(kernel, job)
        except (OSError, RuntimeError) as error:
            cleanup_errors.append(error)
        try:
            # Assignment can fail: the suspended child is not in the job.
            if process is not None:
                if _winapi.WaitForSingleObject(process, 0) != 0:
                    _winapi.TerminateProcess(process, 1)
                _winapi.WaitForSingleObject(process, 2000)
            wait_job_empty(kernel, job)
        except (OSError, RuntimeError) as error:
            cleanup_errors.append(error)
        if reader is not None:
            try:
                reader.join(2)
            except RuntimeError as error:
                cleanup_errors.append(error)
        if writer is not None:
            try:
                writer.join(2)
                if writer.is_alive():
                    cleanup_errors.append(RuntimeError('Private worker input thread did not stop.'))
            except RuntimeError as error:
                cleanup_errors.append(error)
        for fd in (stdout_read, stdout_write, stdin, input_write):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError as error:
                    cleanup_errors.append(error)
        for handle in (thread, process, job):
            if handle is not None:
                if not kernel.CloseHandle(handle):
                    cleanup_errors.append(RuntimeError("Owned handle close failed."))
        if cleanup_errors:
            raise RuntimeError("Owned command cleanup requires inspection.") from cleanup_errors[0]
