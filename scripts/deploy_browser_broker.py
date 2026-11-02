"""Later explicit administrator deployment recipe; do not run during code work.

No elevation, download, service, scheduled task, application activation or policy
grant here. Installs reviewed bytes into a NEW machine-protected directory.
Partial installation remains inert for explicit inspection; never auto-delete.
"""
import argparse
import ctypes
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

from scripts.build_browser_native import ASSETS
from scripts.release_integrity import parse_manifest
from scripts.runtime_assets import snapshot_runtime, require_x64_runtime


def machine_paths():
    if os.name != 'nt' or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise ValueError('Use an explicitly elevated x64 Windows Python deployment session.')
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise ValueError('Deployment requires explicit Windows administrator consent; no automatic elevation.')
    program_files = ctypes.create_unicode_buffer(32768)
    # Fixed OS-known x64 Program Files folder; never trust environment paths.
    if ctypes.windll.shell32.SHGetFolderPathW(None, 0x26, None, 0, program_files) != 0:
        raise ValueError('Protected machine installation root is unavailable.')
    system = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(system, len(system))
    if not 0 < length < len(system):
        raise ValueError('Windows system directory is unavailable.')
    return Path(program_files.value) / 'USMANJARVISBrowserBrokerV1', Path(system.value) / 'icacls.exe'


def protect(directory, icacls):
    # Apply only to the newly created tree. No existing browser/app ACL changed.
    for arguments in (
        [str(icacls), str(directory), '/inheritance:r', '/grant:r', '*S-1-5-18:(OI)(CI)F',
         '*S-1-5-32-544:(OI)(CI)F', '*S-1-5-32-545:(OI)(CI)RX', '*S-1-15-2-1:(OI)(CI)RX'],
        [str(icacls), str(directory), '/setowner', '*S-1-5-32-544', '/T'],
    ):
        # No PATH lookup, shell expansion, wildcard target, or inherited secrets.
        subprocess.run(arguments, check=True, shell=False, timeout=60,
                       env={'SystemRoot': str(icacls.parent.parent)}, cwd=icacls.parent)


def deploy(native_directory, native_manifest, browser_directory, browser_manifest):
    destination, icacls = machine_paths()
    if destination.exists() or destination.is_symlink():
        raise ValueError('A deployment already exists; inspect it separately. No replacement or deletion performed.')
    for parent in (destination.parent,):
        if parent.is_symlink() or parent.is_junction():
            raise ValueError('Linked installation roots are unsupported.')
    with Path(native_manifest).open('rb') as source:
        manifest = parse_manifest(source.read(2 * 1024**2 + 1))
    if (not isinstance(manifest, dict) or manifest.get('format') != 'jarvis-browser-native-source-v1'
            or manifest.get('architecture') != 'x64' or manifest.get('activation') != 'denied'
            or not isinstance(manifest.get('files'), dict) or set(manifest['files']) != set(ASSETS)):
        raise ValueError('Select an exact reviewed native source-build manifest; hash identity is not publisher verification.')
    root = Path(native_directory).absolute()
    if root.is_symlink() or root.is_junction() or not root.is_dir():
        raise ValueError('Native assets require an ordinary source directory.')
    destination.mkdir(exist_ok=False)
    protect(destination, icacls)
    for name in ASSETS:
        path = root / name
        if path.is_symlink() or path.is_junction():
            raise ValueError('Linked native assets are unsupported.')
        expected = manifest['files'][name]
        digest, consumed = hashlib.sha256(), 0
        with path.open('rb') as source, (destination / name).open('xb') as output:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError('Native source must be a regular file without hard links.')
            while chunk := source.read(1024 * 1024):
                consumed += len(chunk)
                if consumed > 32 * 1024**2:
                    raise ValueError('Native asset exceeds deployment bounds.')
                digest.update(chunk); output.write(chunk)
            output.flush(); os.fsync(output.fileno())
        if digest.hexdigest() != expected:
            raise ValueError('Native bytes differ from reviewed inventory. Partial installation remains inactive.')
        require_x64_runtime(destination / name)
    browser = snapshot_runtime(browser_directory, browser_manifest, destination / 'Browser', required_executable='chrome.exe')
    # New files may be owned by the installing user's SID; normalize ownership
    # only within the new deployment, matching the native protected-path checks.
    protect(destination, icacls)
    receipt = {'format': 'jarvis-browser-deployment-v1', 'activation': 'denied', 'acceptance': 'not performed',
               'broker': str(destination / ASSETS[0]), 'browser': str(browser['directory'] / 'chrome.exe'),
               'native_files': manifest['files'], 'browser_files': browser['files'],
               'recovery': 'managed startup; previous boot only; current-boot crash denies retained'}
    with (destination / 'deployment.json').open('x', encoding='utf-8') as output:
        json.dump(receipt, output, indent=2)
    protect(destination, icacls)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-directory', type=Path, required=True)
    parser.add_argument('--native-manifest', type=Path, required=True)
    parser.add_argument('--browser-directory', type=Path, required=True)
    parser.add_argument('--browser-manifest', type=Path, required=True)
    arguments = parser.parse_args()
    receipt = deploy(arguments.native_directory, arguments.native_manifest, arguments.browser_directory, arguments.browser_manifest)
    print('Protected source assets deployed; browser activation is still denied. Receipt:', Path(receipt['broker']).parent / 'deployment.json')


if __name__ == '__main__':
    main()
