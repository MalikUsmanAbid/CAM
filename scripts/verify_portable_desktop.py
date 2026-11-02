"""Actual packaged WebView2 UI acceptance, using disposable portable data only.

A private, temporary debugging port is enabled only for this fixture process.
Verify its listener belongs to the owned job before attaching. No account login,
real persistence, installed-app settings or privileged browser policy is used.
"""

import argparse
import ctypes
import json
import os
import socket
import subprocess
import tempfile
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx

from scripts.release_integrity import checksum, inspect_portable
from services.agent_api.app.windows_commands import (
    _kernel,
    assign_job,
    create_job,
    terminate_owned_job,
)


def owned_listener(kernel, job, port):
    import _winapi

    size = ctypes.c_uint32()
    api = ctypes.WinDLL('iphlpapi').GetExtendedTcpTable
    api.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    if api(None, ctypes.byref(size), False, 2, 5, 0) not in (0, 122):
        return False
    table = ctypes.create_string_buffer(size.value)
    if api(table, ctypes.byref(size), False, 2, 5, 0):
        return False
    count = ctypes.c_uint32.from_buffer(table).value
    if count > (size.value - 4) // 24:
        return False
    kernel.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    for index in range(count):
        state, address, local_port, _, _, pid = (ctypes.c_uint32 * 6).from_buffer(table, 4 + index * 24)
        if state != 2 or address != int.from_bytes(socket.inet_aton('127.0.0.1'), 'little') or socket.ntohs(local_port & 65535) != port:
            continue
        handle = _winapi.OpenProcess(0x1000, False, pid)
        try:
            member = ctypes.c_int()
            return bool(kernel.IsProcessInJob(handle, job, ctypes.byref(member)) and member.value)
        finally:
            _winapi.CloseHandle(handle)
    return False


@contextmanager
def desktop(executable, port, *, portable=True, local_data=None):
    import _winapi

    kernel = _kernel()
    job = create_job(kernel)
    process = thread = None
    environment = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'LOCALAPPDATA', 'APPDATA', 'USERPROFILE') if key in os.environ}
    environment['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = f'--remote-debugging-port={port} --remote-debugging-address=127.0.0.1'
    if local_data is not None:
        local_data.mkdir(parents=True, exist_ok=True)
        environment['LOCALAPPDATA'] = str(local_data)
    command = subprocess.list2cmdline([str(executable), *(['--portable'] if portable else [])])
    try:
        process, thread, _, _ = _winapi.CreateProcess(str(executable), command, None, None, False,
            4 | 0x08000000, environment, str(executable.parent), subprocess.STARTUPINFO())
        assign_job(kernel, job, process)
        if kernel.ResumeThread(thread) == 0xffffffff:
            raise RuntimeError('Cannot resume the contained desktop fixture.')
        yield kernel, job, process
    finally:
        try:
            terminate_owned_job(kernel, job)
        finally:
            if process is not None:
                if _winapi.WaitForSingleObject(process, 0) != 0:
                    _winapi.TerminateProcess(process, 1)
                _winapi.WaitForSingleObject(process, 5000)
            for handle in (thread, process, job):
                if handle is not None:
                    kernel.CloseHandle(handle)


@contextmanager
def locked_file(archive):
    # Inspection and extraction must see the same bytes, even if another build
    # attempts to replace this archive during a long UI acceptance run.
    archive = Path(archive).resolve(strict=True)
    kernel = _kernel()
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    handle = kernel.CreateFileW(str(archive), 0x80000000, 1, None, 3, 0x80, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ValueError('Portable archive is unavailable or another writer owns it.')
    try:
        yield archive
    finally:
        kernel.CloseHandle(handle)


def verify(archive, screenshot):
    with locked_file(archive) as path:
        return _verify_locked(path, screenshot)


def _verify_locked(archive, screenshot):
    archive = Path(archive).resolve(strict=True)
    inventory = inspect_portable(archive)
    archive_digest = checksum(archive)
    checks = []
    with tempfile.TemporaryDirectory(prefix='jarvis-portable-ui-fixture-') as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(root)  # Names, links, sizes and hashes inspected above.
        checks.extend(exercise(root / 'USMAN-JARVIS.exe', root / 'data', screenshot))
    if checksum(archive) != archive_digest:
        raise RuntimeError('Portable archive changed during acceptance.')
    return {'format': 'jarvis-portable-desktop-acceptance-v1', 'archive_sha256': archive_digest,
            'inventory': inventory, 'checks': checks, 'persistence': 'temporary, removed',
            'limitations': ['mock UI provider; separate live coding evidence', 'installed WebView2 on this device',
                            'not clean-machine installer, tray exit, native browser isolation or UIA acceptance']}



def exercise(executable, data_root, screenshot, *, portable=True):
    import _winapi

    checks = []
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    debugger = f'http://127.0.0.1:{port}'
    with desktop(executable, port, portable=portable, local_data=None if portable else data_root.parent) as (kernel, job, process):
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=2) as client:
            deadline = time.monotonic() + 90
            while not owned_listener(kernel, job, port):
                if _winapi.WaitForSingleObject(process, 0) == 0 or time.monotonic() >= deadline:
                    raise RuntimeError('The actual desktop did not expose its owned fixture WebView2.')
                time.sleep(0.2)
            checks.append('real desktop and owned loopback WebView2')
            from playwright.sync_api import expect, sync_playwright
            with sync_playwright() as driver:
                browser = driver.chromium.connect_over_cdp(debugger)
                page = browser.contexts[0].pages[0]
                page.set_default_timeout(30000)
                endpoints = []
                page.on('request', lambda request: endpoints.append(request.url) if '/api/v1/health' in request.url else None)
                page.reload()
                expect(page.get_by_text('Connected', exact=True)).to_be_visible()
                webview_data = data_root / 'webview'
                if not webview_data.is_dir() or not any(webview_data.iterdir()):
                    raise RuntimeError('WebView profile escaped its disposable data directory.')
                checks.append('real WebView profile created inside disposable app data')
                checks.append('native private session bootstrap reaches authenticated dashboard')
                page.get_by_role('button', name='Chat', exact=True).click()
                page.get_by_label('Message JARVIS').fill('Portable release fixture')
                page.get_by_role('button', name='Send', exact=True).click()
                expect(page.get_by_text('JARVIS development provider is online.', exact=False)).to_be_visible()
                checks.append('actual dashboard request through packaged backend; explicitly mock provider')
                page.screenshot(path=str(screenshot), full_page=True)
                if not endpoints:
                    raise RuntimeError('No packaged API endpoint was observed.')
                parsed = urlparse(endpoints[-1])
                if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port:
                    raise RuntimeError('Unexpected packaged API destination.')
                api = f'http://127.0.0.1:{parsed.port}'
                _winapi.TerminateProcess(process, 1)  # Simulate shell crash, not a graceful tray exit.
                deadline = time.monotonic() + 15
                while True:
                    try:
                        client.get(api + '/api/v1/health')
                    except httpx.TransportError:
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Backend survived termination of its native owner.')
                    time.sleep(0.1)
                checks.append('native shell crash stops its owned backend')
    return checks

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--screenshot', type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.evidence.exists() or arguments.screenshot.exists():
        raise SystemExit('Select absent evidence and screenshot files.')
    result = verify(arguments.archive, arguments.screenshot)
    with arguments.evidence.open('x', encoding='utf-8') as output:
        json.dump(result, output, indent=2)
    print('Actual portable desktop fixture passed; user persistence was not opened.')


if __name__ == '__main__':
    main()
