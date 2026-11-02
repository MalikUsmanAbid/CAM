"""Authorized live local-model fixture: propose, approve, test, selectively undo.

All application persistence and coding projects are disposable. The downloaded
model lives only in managed local app data; user configuration is not changed.
Requires the verified standalone runtime, never installs a global service.
"""

import ast
import ctypes
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import tokenize
from contextlib import contextmanager
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from services.agent_api.app.main import app
from services.agent_api.app.windows_commands import _kernel, assign_job, create_job

ASSET_DIGEST = "e5ecff02dfc7cae8ddf6d6b7a7ac5f727ffeedf4491f44ae75ff5b0820dbbbd5"
MODEL = "qwen2.5-coder:1.5b"
ALIAS = "jarvis-fixture-coder-cpu"


def validate_fixture_patch(content):
    """Authorize only the exact code Python imports from the UTF-8 bytes."""
    raw = content.encode("utf-8")
    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    if encoding not in {"utf-8", "utf-8-sig"}:
        raise RuntimeError("Fixture correction requires UTF-8 Python source.")
    expected = ast.parse(b"def add(a, b):\n    return a + b\n")
    if ast.dump(ast.parse(raw), include_attributes=False) != ast.dump(expected, include_attributes=False):
        raise RuntimeError("Model proposed code outside the exact fixture correction; no execution approved.")


@contextmanager
def fixture_environment(values):
    before = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@contextmanager
def owned_model_server(runtime, fixture, models):
    import _winapi
    import msvcrt

    with socket.socket() as socket_probe:
        socket_probe.bind(("127.0.0.1", 0))
        port = socket_probe.getsockname()[1]
    environment = {name: os.environ[name] for name in ("SystemRoot", "WINDIR", "TEMP", "TMP") if name in os.environ}
    isolated_profile = fixture / "runtime-profile"
    isolated_profile.mkdir()
    environment.update(PATH=str(runtime.parent) + os.pathsep + os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"),
                       USERPROFILE=str(isolated_profile), APPDATA=str(isolated_profile / "AppData/Roaming"), LOCALAPPDATA=str(isolated_profile / "AppData/Local"),
                       OLLAMA_HOST=f"127.0.0.1:{port}", OLLAMA_MODELS=str(models), OLLAMA_NO_CLOUD="1", OLLAMA_NUM_PARALLEL="1",
                       OLLAMA_MAX_LOADED_MODELS="1", OLLAMA_CONTEXT_LENGTH="2048", OLLAMA_KEEP_ALIVE="5m")
    environment.update(OLLAMA_VULKAN="0", CUDA_VISIBLE_DEVICES="-1", HIP_VISIBLE_DEVICES="-1")
    kernel = _kernel()
    job = create_job(kernel)
    process = thread = None
    with (fixture / "model-server.log").open("wb") as log, open(os.devnull, "rb") as stdin:
        handles = [msvcrt.get_osfhandle(log.fileno()), msvcrt.get_osfhandle(stdin.fileno())]
        for handle in handles:
            os.set_handle_inheritable(handle, True)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESTDHANDLES
        startup.hStdOutput = startup.hStdError = handles[0]
        startup.hStdInput = handles[1]
        startup.lpAttributeList = {"handle_list": handles}
        try:
            process, thread, _, _ = _winapi.CreateProcess(str(runtime), subprocess.list2cmdline([str(runtime), "serve"]), None, None, True,
                                                        4 | 0x08000000 | 0x00080000, environment, str(runtime.parent), startup)
            assign_job(kernel, job, process)
            if kernel.ResumeThread(thread) == 0xffffffff:
                raise RuntimeError("Cannot resume the owned model server.")
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60, trust_env=False) as client:
                deadline = time.monotonic() + 60
                while True:
                    try:
                        response = client.get("/api/version")
                        response.raise_for_status()
                        break
                    except httpx.TransportError:
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Owned model server did not become ready.") from None
                        time.sleep(.1)
                yield client, f"http://127.0.0.1:{port}"
        finally:
            # Only the newly created owned job is terminated; never an existing
            # system/user Ollama process. Job control is not a filesystem sandbox.
            kernel.TerminateJobObject(job, 1)
            if process is not None:
                # Assignment can fail while the exact new process is suspended.
                # Terminate its own handle too; no unrelated PID is targeted.
                try:
                    _winapi.TerminateProcess(process, 1)
                except OSError:
                    pass
                _winapi.WaitForSingleObject(process, 10_000)
                kernel.CloseHandle(process)
            if thread is not None:
                kernel.CloseHandle(thread)
            class Accounting(ctypes.Structure):
                _fields_ = [(name, ctypes.c_int64) for name in ("user", "kernel", "period_user", "period_kernel")] + [(name, ctypes.c_uint32) for name in ("faults", "total", "active", "terminated")]
            kernel.QueryInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
            stopped = False
            deadline = time.monotonic() + 10
            try:
                while time.monotonic() < deadline:
                    accounting = Accounting()
                    if not kernel.QueryInformationJobObject(job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                        break
                    if not accounting.active:
                        stopped = True
                        break
                    time.sleep(.05)
            finally:
                kernel.CloseHandle(job)
            for handle in handles:
                os.set_handle_inheritable(handle, False)
            if not stopped:
                raise RuntimeError("Owned model process cleanup could not be verified.")


def pull(client):
    reported = -1
    # Download finalization may hash a gigabyte on a slow disk without emitting
    # another progress line. This setup timeout is separate from inference.
    with client.stream("POST", "/api/pull", json={"model": MODEL, "stream": True}, timeout=httpx.Timeout(300, connect=5)) as response:
        response.raise_for_status()
        last = None
        for line in response.iter_lines():
            if not line:
                continue
            if len(line) > 64_000:
                raise RuntimeError("Model download response exceeded limits.")
            last = json.loads(line)
            if last.get("error"):
                raise RuntimeError("Local runtime could not download the configured model.")
            completed = last.get("completed", 0)
            if completed // (32 * 1024 * 1024) > reported:
                reported = completed // (32 * 1024 * 1024)
                print(f"Model download progress: {completed} bytes", flush=True)
        if not last or last.get("status") != "success":
            raise RuntimeError("Model download did not verify successfully.")
    response = client.post("/api/create", json={"model": ALIAS, "from": MODEL, "parameters": {"num_gpu": 0, "num_ctx": 2048, "num_thread": 4, "temperature": 0}, "stream": False})
    response.raise_for_status()
    if response.json().get("status") != "success":
        raise RuntimeError("CPU-only fixture model was not created.")


def run_fixture(fixture, endpoint):
    project_root = fixture / "coding-project"
    project_root.mkdir()
    original = b"def add(a, b):\n    return a - b\n"
    (project_root / "arithmetic.py").write_bytes(original)
    (project_root / "unrelated.txt").write_text("existing fixture user edit")
    (project_root / "test_arithmetic.py").write_text("import unittest\nfrom arithmetic import add\nclass Addition(unittest.TestCase):\n    def test_addition(self):\n        for a,b in [(2,3),(-2,4),(0,8),(11,-3)]:\n            with self.subTest(a=a,b=b): self.assertEqual(add(a,b),a+b)\n")
    environment = {"DATABASE_PATH": str(fixture / "application.sqlite"), "SAFE_WORKSPACE": str(fixture / "personal"), "AI_PROVIDER": "ollama",
                   "AI_MODEL": ALIAS, "AI_ENDPOINT": endpoint, "AI_API_KEY_REF": "",
                   "AI_TIMEOUT_SECONDS": "60", "AI_MAX_OUTPUT_TOKENS": "384", "BRAIN_PROVIDER": "mock", "JARVIS_ALLOW_DATABASE_MIGRATION": "0"}
    with fixture_environment(environment), TestClient(app) as client:
        paired = client.post("/api/v1/session/pair", json={"code": app.state.sessions.pairing_code})
        paired.raise_for_status()
        client.headers["Authorization"] = "Bearer " + paired.json()["token"]
        registration = client.post("/api/v1/projects", json={"name": "Live local coding fixture", "root": str(project_root)})
        registration.raise_for_status()
        project = registration.json()["project_id"]

        def tool(name, arguments, approve=False):
            response = client.post("/api/v1/tools/execute", json={"name": name, "project_id": project, "arguments": arguments})
            response.raise_for_status()
            value = response.json()
            if value["status"] == "approval_required":
                if not approve:
                    raise RuntimeError("Fixture action needs its explicitly authorized approval.")
                grant = value["approval"]["request_id"]
                client.post(f"/api/v1/approvals/{grant}/approve?project_id={project}").raise_for_status()
                continuation = client.post(f"/api/v1/tools/execute/{grant}/continue?project_id={project}")
                continuation.raise_for_status()
                value = continuation.json()
            return value

        def tests():
            prepared = tool("studio_prepare_command", {"executable": sys.executable, "arguments": ["-B", "-m", "unittest", "discover", "-s", ".", "-p", "test_arithmetic.py"], "cwd": ".", "timeout_seconds": 30, "output_limit": 64000})
            if prepared["status"] != "success":
                raise RuntimeError("Fixture test command could not prepare.")
            data = prepared["result"]
            return tool("studio_run_command", {key: data[key] for key in ("run_id", "contract_sha256")}, approve=True)

        baseline = tests()
        if baseline["status"] != "failed" or baseline["result"].get("exit_code") != 1:
            raise RuntimeError("Original fixture defect was not reproduced by its actual test command.")
        started = time.monotonic()
        original_response = app.state.provider.respond
        fixture_response = []

        async def capture_response(message, **options):
            response = await original_response(message, **options)
            fixture_response.append(response)
            return response

        app.state.provider.respond = capture_response
        proposed = tool("studio_ai_propose", {"path": "arithmetic.py", "before_sha256": hashlib.sha256(original).hexdigest(), "request": "Fix the addition bug. Preserve the function interface. Return the complete corrected source as the required JSON content string."})
        latency = time.monotonic() - started
        if proposed["status"] != "success" or (project_root / "arithmetic.py").read_bytes() != original:
            detail = {key: proposed.get(key) for key in ("status", "detail")}
            detail["fixture_model_response"] = fixture_response[:1]
            raise RuntimeError("Live model did not produce a valid proposal without altering the source: " + json.dumps(detail))
        change = proposed["result"]
        # This benchmark may automatically approve only its known arithmetic
        # fixture. Never execute arbitrary generated code with user privileges.
        validate_fixture_patch(change["content"])
        arguments = {key: change[key] for key in ("change_id", "path", "proposal_sha256")}
        applied = tool("studio_apply", arguments, approve=True)
        if applied["status"] != "success":
            raise RuntimeError("Exact model proposal could not apply with approval.")
        verified = tests()
        if verified["status"] != "success" or verified["result"].get("exit_code") != 0:
            raise RuntimeError("Real local-model patch failed the actual fixture tests.")
        rollback = tool("studio_rollback", arguments, approve=True)
        if rollback["status"] != "success" or (project_root / "arithmetic.py").read_bytes() != original or (project_root / "unrelated.txt").read_text() != "existing fixture user edit":
            raise RuntimeError("Selective rollback or unrelated-edit preservation failed.")
        restored_test = tests()
        if restored_test["status"] != "failed" or restored_test["result"].get("exit_code") != 1:
            raise RuntimeError("Rollback did not restore the reproduced original test behavior.")
        return {"provider": proposed["result"]["provider"], "model": ALIAS, "model_proposal_seconds": latency, "diff": change["diff"],
                "baseline_exit": baseline["result"]["exit_code"], "verified_exit": verified["result"]["exit_code"], "test_output": verified["result"].get("output"),
                "rollback": "verified original bytes and failing original test", "rollback_test_exit": restored_test["result"]["exit_code"], "unrelated_edit": "preserved", "mock_or_echo_used": False, "persistence": "temporary fixture removed"}


def main():
    if os.name != "nt":
        raise RuntimeError("Live acceptance requires native Windows.")
    directory = Path(os.environ["LOCALAPPDATA"]) / "USMANJARVIS/runtimes/ollama-v0.40.3"
    runtime = directory / "ollama.exe"
    manifest = json.loads((directory / "verified-runtime.json").read_text())
    with runtime.open("rb") as file:
        executable_digest = hashlib.file_digest(file, "sha256").hexdigest()
    if manifest.get("asset_sha256") != ASSET_DIGEST or manifest.get("executable_sha256") != executable_digest:
        raise RuntimeError("Standalone runtime verification failed; do not execute it.")
    models = directory.parent / "models"
    models.mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="jarvis-local-coding-evidence-"))
    diagnostic = []
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-local-coding-fixture-") as temporary:
            fixture = Path(temporary)
            try:
                with owned_model_server(runtime, fixture, models) as (client, endpoint):
                    pull(client)
                    tags = client.get("/api/tags").json()
                    print("Official small coding model downloaded and CPU fixture configured.", flush=True)
                    if "--pull-only" in sys.argv:
                        return
                    evidence = run_fixture(fixture, endpoint)
                    loaded = client.get("/api/ps").json()
                    if any(model.get("size_vram", 0) for model in loaded.get("models", [])):
                        raise RuntimeError("CPU-only model acceptance unexpectedly used GPU memory.")
                    evidence.update(runtime_asset_sha256=ASSET_DIGEST, models=tags, loaded_model_memory=loaded, platform="Windows", processor="CPU, one inference", scope="one arithmetic fixture; no general quality claim")
            finally:
                log = fixture / "model-server.log"
                if log.exists():
                    diagnostic = [line for line in log.read_text(encoding="utf-8", errors="replace").splitlines() if any(word in line.lower() for word in ("error", "failed", "listening", "inference compute", "downloading"))][-20:]
    except Exception as exc:
        (output / "failure.json").write_text(json.dumps({"status": "failed", "type": type(exc).__name__, "detail": str(exc), "runtime_diagnostic": diagnostic, "live_acceptance": False}, indent=2), encoding="utf-8")
        print("Live acceptance did not pass. Diagnostics: " + str(output), flush=True)
        raise
    (output / "acceptance.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print("Live local coding acceptance passed. Evidence: " + str(output), flush=True)


if __name__ == "__main__":
    main()
