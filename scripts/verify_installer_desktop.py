"""Actual per-user installer acceptance with disposable app data and uninstall.

Refuses existing JARVIS registrations. Temporarily creates only the new product's
HKCU registration, then uninstalls and removes its verified fixture location key.
Never requests application-data deletion or touches a real database.
"""
import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

from scripts.release_integrity import checksum, inspect_release
from scripts.verify_portable_desktop import exercise, locked_file
from services.agent_api.app.windows_commands import (
    _kernel,
    assign_job,
    create_job,
    terminate_owned_job,
)

PRODUCT = r'Software\usman\USMAN JARVIS'
UNINSTALL = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\USMAN JARVIS'


def expected_desktop_hashes(source):
    # Tauri patches this one bundle marker while assembling NSIS, then restores
    # the source binary. All other bytes must remain identical to the portable.
    unknown = b'__TAURI_BUNDLE_TYPE_VAR_UNK'
    if source.count(unknown) > 1:
        raise ValueError('Ambiguous Tauri bundle marker.')
    result = {hashlib.sha256(source).hexdigest(): 'unchanged'}
    if source.count(unknown) == 1:
        patched = source.replace(unknown, b'__TAURI_BUNDLE_TYPE_VAR_NSS', 1)
        result[hashlib.sha256(patched).hexdigest()] = 'exact Tauri NSIS bundle marker patch'
    return result


def preflight():
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            for path in (PRODUCT, UNINSTALL):
                try:
                    with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view):
                        raise ValueError('Existing JARVIS registration; installer acceptance refused.')
                except FileNotFoundError:
                    pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
            winreg.QueryValueEx(key, 'USMAN JARVIS')
    except FileNotFoundError:
        return
    raise ValueError('Existing JARVIS autostart; installer acceptance refused.')


def contained(program, command, cwd):
    import _winapi
    kernel = _kernel()
    job = create_job(kernel)
    process = thread = None
    environment = {key: os.environ[key] for key in (
        'SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'PATH'
    ) if key in os.environ}
    try:
        process, thread, _, _ = _winapi.CreateProcess(str(program), command, None, None, False,
            4 | 0x08000000, environment, str(cwd), subprocess.STARTUPINFO())
        assign_job(kernel, job, process)
        if kernel.ResumeThread(thread) == 0xffffffff:
            raise RuntimeError('Cannot resume installer fixture.')
        if _winapi.WaitForSingleObject(process, 120000) != 0:
            raise RuntimeError('Installer fixture exceeded its bounded deadline.')
        if _winapi.GetExitCodeProcess(process) != 0:
            raise RuntimeError('Actual installer/uninstaller returned failure.')
    finally:
        terminate_owned_job(kernel, job)
        for handle in (thread, process, job):
            if handle is not None:
                kernel.CloseHandle(handle)


def remove_location(install):
    import winreg
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PRODUCT, 0, winreg.KEY_READ | view) as key:
                value, _ = winreg.QueryValueEx(key, '')
                children, values, _ = winreg.QueryInfoKey(key)
                names = {winreg.EnumValue(key, i)[0] for i in range(values)}
                if children or names - {'', 'Installer Language'} or Path(value).resolve() != install.resolve():
                    raise RuntimeError('Registration changed ownership; cleanup refused.')
            winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, PRODUCT, view)
        except FileNotFoundError:
            pass


def _verify_locked(directory, screenshot):
    preflight()  # No mutation before existing installations are excluded.
    directory = Path(directory).resolve(strict=True)
    inspect_release(directory)
    manifest = json.loads((directory / 'release-manifest.json').read_text())
    installer = directory / next(name for name in manifest['files'] if name.endswith('.exe'))
    digest = checksum(installer)
    with zipfile.ZipFile(directory / 'USMAN-JARVIS-portable.zip') as archive:
        payload = json.loads(archive.read('package-manifest.json'))['files']
        expected_desktop = expected_desktop_hashes(archive.read('USMAN-JARVIS.exe'))
    import winreg
    parent_present = {}
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\usman', 0, winreg.KEY_READ | view):
                parent_present[view] = True
        except FileNotFoundError:
            parent_present[view] = False
    checks = []
    with tempfile.TemporaryDirectory(prefix='jarvis-installer-fixture-') as temporary:
        root = Path(temporary).resolve()
        if not root.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise ValueError('Fixture is outside the temporary directory.')
        install = root / 'application'
        uninstaller = install / 'uninstall.exe'
        try:
            command = subprocess.list2cmdline([str(installer), '/S', '/NS']) + ' /D=' + str(install)
            contained(installer, command, root)
            installed_digest = checksum(install / 'usman-jarvis-desktop.exe')
            if installed_digest not in expected_desktop:
                raise RuntimeError('Installed desktop differs from the reviewed portable payload.')
            if checksum(install / 'jarvis-backend.exe') != payload['jarvis-backend.exe']:
                raise RuntimeError('Installed backend differs from the reviewed payload.')
            checks.append('actual silent per-user NSIS install; payload verified against ' + expected_desktop[installed_digest])
            checks.extend(exercise(install / 'usman-jarvis-desktop.exe', root / 'local-data' / 'USMANJARVIS', screenshot, portable=False))
        finally:
            if uninstaller.exists():
                # _?= avoids an asynchronous self-copy; no delete-app-data option.
                command = subprocess.list2cmdline([str(uninstaller), '/S']) + ' _?=' + str(install)
                contained(uninstaller, command, root)
            remove_location(install)
            for view, existed in parent_present.items():
                if existed:
                    continue
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\usman', 0, winreg.KEY_READ | view) as key:
                        children, values, _ = winreg.QueryInfoKey(key)
                    if children == 0 and values == 0:
                        winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, r'Software\usman', view)
                except FileNotFoundError:
                    pass
        if (install / 'usman-jarvis-desktop.exe').exists() or (install / 'jarvis-backend.exe').exists():
            raise RuntimeError('Uninstaller left application payloads behind.')
        preflight()
        checks.append('actual silent uninstall; fixture product registrations and payloads removed')
    if checksum(installer) != digest:
        raise RuntimeError('Installer changed during acceptance.')
    return {'format': 'jarvis-installer-desktop-acceptance-v1', 'installer_sha256': digest,
            'installed_desktop_sha256': installed_digest, 'desktop_transform': expected_desktop[installed_digest],
            'checks': checks, 'persistence': 'temporary, removed; original registrations absent',
            'limitations': ['this device with installed WebView2; not a clean machine',
                            'mock UI provider; separate real local-AI evidence',
                            'not tray exit, OS browser isolation or UIA acceptance']}


def verify(directory, screenshot):
    preflight()
    directory = Path(directory).resolve(strict=True)
    with locked_file(directory / 'release-manifest.json'):
        inspect_release(directory)
        manifest = json.loads((directory / 'release-manifest.json').read_text())
        installer = directory / next(name for name in manifest['files'] if name.endswith('.exe'))
        with locked_file(installer), locked_file(directory / 'USMAN-JARVIS-portable.zip'):
            return _verify_locked(directory, screenshot)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--screenshot', type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.evidence.exists() or arguments.screenshot.exists():
        raise SystemExit('Select absent evidence/screenshot paths.')
    result = verify(arguments.directory, arguments.screenshot)
    with arguments.evidence.open('x', encoding='utf-8') as output:
        json.dump(result, output, indent=2)
    print('Actual installer, isolated installed desktop and uninstall acceptance passed.')


if __name__ == '__main__':
    main()
