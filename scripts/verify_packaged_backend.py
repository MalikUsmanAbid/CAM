"""Exercise the actual sidecar executable using only disposable persistence.

No desktop settings, real databases, model prompts or credentials are opened.
The executable is suspended until contained in an owned Windows job. Session
tokens stay in memory and are never included in evidence or diagnostic logs.
"""

import argparse
import hashlib
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx

from services.agent_api.app.desktop_bootstrap import read_frame
from services.agent_api.app.windows_commands import (
    _kernel,
    assign_job,
    create_job,
    terminate_owned_job,
)


def verify(executable):
    import _winapi
    import msvcrt

    executable = Path(executable).resolve(strict=True)
    if os.name != 'nt' or executable.suffix.lower() != '.exe':
        raise ValueError('An existing Windows sidecar executable is required.')
    with executable.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    checks = []
    with tempfile.TemporaryDirectory(prefix='jarvis-packaged-fixture-') as temporary:
        fixture = Path(temporary)
        kernel = _kernel()
        job = create_job(kernel)
        process = thread = None
        input_read, input_write = os.pipe()
        output_read, output_write = os.pipe()
        reader = None
        with (fixture / 'stderr.log').open('wb') as log:
            try:
                handles = [msvcrt.get_osfhandle(input_read), msvcrt.get_osfhandle(output_write),
                           msvcrt.get_osfhandle(log.fileno())]
                for handle in handles:
                    os.set_handle_inheritable(handle, True)
                startup = subprocess.STARTUPINFO()
                startup.dwFlags = subprocess.STARTF_USESTDHANDLES
                startup.hStdInput, startup.hStdOutput, startup.hStdError = handles
                startup.lpAttributeList = {'handle_list': handles}
                environment = {key: os.environ[key] for key in (
                    'SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'LOCALAPPDATA', 'APPDATA', 'USERPROFILE'
                ) if key in os.environ}
                process, thread, _, _ = _winapi.CreateProcess(
                    str(executable), subprocess.list2cmdline([str(executable)]), None, None, True,
                    4 | 0x08000000 | 0x00080000, environment, str(fixture), startup)
                assign_job(kernel, job, process)
                os.close(input_read)
                input_read = None
                os.close(output_write)
                output_write = None
                if kernel.ResumeThread(thread) == 0xffffffff:
                    raise RuntimeError('Cannot resume fixture sidecar.')
                responses = queue.Queue(maxsize=4)

                def consume():
                    try:
                        with os.fdopen(output_read, 'rb', closefd=False) as channel:
                            for _ in range(3):
                                responses.put(read_frame(channel), timeout=2)
                    except (ValueError, OSError, queue.Full):
                        pass

                reader = threading.Thread(target=consume, daemon=True)
                reader.start()

                def send(value):
                    payload = json.dumps(value).encode() + b'\n'
                    while payload:
                        payload = payload[os.write(input_write, payload):]

                def receive():
                    try:
                        return responses.get(timeout=90)
                    except queue.Empty:
                        raise RuntimeError('Packaged private protocol did not become ready; no token logged.') from None

                send({'version': 1, 'action': 'configure', 'data_directory': str(fixture / 'data'),
                      'workspace_directory': str(fixture / 'workspace'), 'provider': 'mock', 'model': 'jarvis-dev'})
                ready = receive()
                endpoint = ready.get('endpoint', '')
                url = httpx.URL(endpoint)
                if ready.get('version') != 1 or ready.get('event') != 'ready' or url.scheme != 'http' or url.host != '127.0.0.1' or not url.port:
                    raise RuntimeError('Packaged private startup contract is invalid.')
                checks.append('private loopback startup with disposable persistence')
                with httpx.Client(base_url=endpoint, trust_env=False, follow_redirects=False, timeout=5) as client:
                    if client.get('/api/v1/tools').status_code != 401:
                        raise RuntimeError('Packaged API did not enforce authentication.')
                    authorization = {'Authorization': 'Bearer ' + ready['token']}
                    tools = client.get('/api/v1/tools', headers=authorization)
                    tools.raise_for_status()
                    if not any(tool['name'] == 'read_file' for tool in tools.json()):
                        raise RuntimeError('Packaged tool registry is incomplete.')
                    checks.append('unauthorized denial and authenticated real tool registry')
                    send({'version': 1, 'action': 'renew_session', 'request_id': 'a' * 32})
                    renewed = receive()
                    if renewed.get('event') != 'session_renewed' or renewed.get('request_id') != 'a' * 32 or renewed.get('endpoint') != endpoint:
                        raise RuntimeError('Packaged session renewal contract failed.')
                    if client.get('/api/v1/tools', headers=authorization).status_code != 401:
                        raise RuntimeError('Packaged renewal failed to invalidate the old session.')
                    client.get('/api/v1/tools', headers={'Authorization': 'Bearer ' + renewed['token']}).raise_for_status()
                    checks.append('private renewal invalidates the previous session')
                send({'version': 1, 'action': 'shutdown'})
                deadline = time.monotonic() + 30
                while _winapi.WaitForSingleObject(process, 0) != 0 and time.monotonic() < deadline:
                    time.sleep(0.05)
                if _winapi.WaitForSingleObject(process, 0) != 0 or _winapi.GetExitCodeProcess(process) != 0:
                    raise RuntimeError('Packaged sidecar did not shut down cleanly.')
                checks.append('private shutdown exits successfully')
            finally:
                try:
                    terminate_owned_job(kernel, job)
                finally:
                    if process is not None:
                        if _winapi.WaitForSingleObject(process, 0) != 0:
                            _winapi.TerminateProcess(process, 1)
                        _winapi.WaitForSingleObject(process, 5000)
                    if reader:
                        reader.join(5)
                    for descriptor in (input_read, input_write, output_read, output_write):
                        if descriptor is not None:
                            os.close(descriptor)
                    for handle in (thread, process, job):
                        if handle is not None:
                            kernel.CloseHandle(handle)
    with executable.open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != digest:
            raise RuntimeError('Packaged executable changed during verification.')
    return {'format': 'jarvis-packaged-backend-acceptance-v1', 'checks': checks,
            'executable_sha256': digest,
            'persistence': 'temporary, removed', 'real_model_used': False,
            'scope': 'actual packaged backend; not desktop UI or installer acceptance'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.evidence.exists():
        raise SystemExit('Select an absent evidence file.')
    result = verify(arguments.executable)
    with arguments.evidence.open('x', encoding='utf-8') as output:
        json.dump(result, output, indent=2)
    print('Actual packaged backend acceptance passed; evidence saved without session credentials.')


if __name__ == '__main__':
    main()
