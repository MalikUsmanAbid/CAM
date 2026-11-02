"""Explicit Windows build/assembly recipe; never downloads or installs prerequisites.

Run only in the later verification stage. This runs repository build scripts,
creates native artifacts and requires PyInstaller, MSVC and cargo-tauri.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(arguments, directory, environment):
    subprocess.run([str(argument) for argument in arguments], cwd=directory, env=environment, check=True, shell=False, timeout=3600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-directory', type=Path, required=True)
    parser.add_argument('--cargo', type=Path)
    parser.add_argument('--tauri-cli', type=Path, help='Installed official Tauri CLI launcher; otherwise use cargo tauri.')
    parser.add_argument('--sidecar', type=Path, help='Reuse an explicitly selected already-built x64 backend.')
    parser.add_argument('--webview2-runtime-directory', type=Path, help='Explicit extracted official fixed runtime; never downloaded or installed here.')
    parser.add_argument('--webview2-runtime-manifest', type=Path, help='Separately reviewed jarvis-runtime-assets-v1 path/SHA-256 manifest.')
    arguments = parser.parse_args()
    if arguments.tauri_cli and not arguments.tauri_cli.is_file():
        raise SystemExit('The selected Tauri CLI launcher does not exist.')
    if bool(arguments.webview2_runtime_directory) != bool(arguments.webview2_runtime_manifest):
        raise SystemExit('Fixed WebView2 runtime assembly requires both the extracted directory and reviewed manifest.')
    if os.name != 'nt':
        raise SystemExit('The desktop release recipe requires Windows.')
    if not arguments.sidecar and importlib.util.find_spec('PyInstaller') is None:
        raise SystemExit('PyInstaller is missing. No package was installed.')
    cargo = arguments.cargo or shutil.which('cargo')
    npm = shutil.which('npm.cmd')
    if not cargo or not npm:
        raise SystemExit('Cargo and npm are required. No toolchain was installed or PATH changed.')
    output = arguments.output_directory.absolute()
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise SystemExit('Choose an absent output directory inside an existing parent.')
    # The caller-selected source tree is preserved. Existing generated backend
    # binaries are never silently replaced by a different build.
    native = ROOT / 'apps' / 'desktop' / 'src-tauri'
    target = native / 'binaries' / 'jarvis-backend-x86_64-pc-windows-msvc.exe'
    if target.exists() or target.is_symlink():
        raise SystemExit('A sidecar build already exists; inspect/archive it explicitly before rebuilding.')
    allowed_environment = {'systemroot', 'windir', 'path', 'temp', 'tmp', 'localappdata', 'appdata', 'userprofile',
                           'homedrive', 'homepath', 'cargo_home', 'rustup_home', 'programfiles', 'programfiles(x86)',
                           'programw6432', 'vsinstalldir', 'vcinstalldir', 'windowssdkdir', 'include', 'lib', 'libpath',
                           'vctoolsinstalldir', 'visualstudioversion', 'comspec', 'pathext'}
    environment = {name: value for name, value in os.environ.items() if name.lower() in allowed_environment}
    # Never propagate real persistence/migration consent to build children.
    for name in ('DATABASE_PATH', 'JARVIS_DATA_DIR', 'JARVIS_ALLOW_DATABASE_MIGRATION', 'SAFE_WORKSPACE'):
        environment.pop(name, None)
    with tempfile.TemporaryDirectory(prefix='jarvis-release-build-', dir=output.parent) as temporary:
        temporary = Path(temporary)
        # A fresh Cargo target tree prevents a stale installer from being selected
        # after a partial/current build. Never inherit CARGO_TARGET_DIR from callers.
        environment['CARGO_TARGET_DIR'] = str(temporary / 'cargo-target')
        environment['CARGO_BUILD_JOBS'] = '1'
        artifacts = temporary / 'release-artifacts'
        runtime = None
        configuration = None
        if arguments.webview2_runtime_directory:
            from scripts.runtime_assets import snapshot_runtime
            runtime = snapshot_runtime(arguments.webview2_runtime_directory, arguments.webview2_runtime_manifest, temporary / 'WebView2Runtime')
            configuration = temporary / 'runtime-tauri-config.json'
            configuration.write_text(json.dumps({'bundle': {'windows': {'webviewInstallMode': {
                'type': 'fixedRuntime', 'path': str(runtime['directory'])}}}}), encoding='utf-8')
        import sys
        if arguments.sidecar:
            from scripts.runtime_assets import require_x64_runtime
            built = arguments.sidecar.resolve(strict=True)
            require_x64_runtime(built)
        else:
            run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', temporary / 'dist',
                 '--workpath', temporary / 'work', ROOT / 'scripts' / 'desktop_sidecar.spec'], ROOT, environment)
            built = temporary / 'dist' / 'jarvis-backend.exe'
        if not built.is_file():
            raise SystemExit('The backend executable was not produced.')
        from scripts.release_integrity import checksum
        sidecar_digest = checksum(built)
        target.parent.mkdir(parents=True, exist_ok=True)
        with built.open('rb') as source, target.open('xb') as destination:
            shutil.copyfileobj(source, destination)
        if checksum(target) != sidecar_digest or checksum(built) != sidecar_digest:
            raise SystemExit('Backend bytes changed during assembly; do not publish this build.')
        run([npm, 'run', 'build'], ROOT / 'apps' / 'desktop', environment)
        build = ([str(arguments.tauri_cli), 'build'] if arguments.tauri_cli else [cargo, 'tauri', 'build'])
        build.extend(['--target', 'x86_64-pc-windows-msvc'])
        if configuration is not None:
            build.extend(['--config', configuration])
        run(build, ROOT / 'apps' / 'desktop', environment)
        release = temporary / 'cargo-target' / 'x86_64-pc-windows-msvc' / 'release'
        shell = release / 'usman-jarvis-desktop.exe'
        installers = list((release / 'bundle' / 'nsis').glob('*.exe'))
        if not shell.is_file() or len(installers) != 1:
            raise SystemExit('Expected desktop executable and one NSIS installer were not produced.')
        artifacts.mkdir(exist_ok=False)
        manifest = {'format_version': 1, 'signed': False, 'acceptance': 'not performed',
                    'portable_requires': 'Model runtime/weights configured separately' if runtime else 'Installed Microsoft WebView2 runtime; model runtime/weights configured separately',
                    'fixed_runtime_files': runtime['files'] if runtime else None, 'files': {}}
        installer = artifacts / installers[0].name
        with installers[0].open('rb') as source, installer.open('xb') as destination:
            shutil.copyfileobj(source, destination)
        archive = artifacts / 'USMAN-JARVIS-portable.zip'
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
            from scripts.release_integrity import checksum, inspect_release
            package_files = {}
            def add_file(path, name):
                package_files[name] = checksum(path)
                bundle.write(path, name)
            def add_text(name, value):
                encoded = value.encode('utf-8')
                package_files[name] = hashlib.sha256(encoded).hexdigest()
                bundle.writestr(name, encoded)
            add_file(shell, 'USMAN-JARVIS.exe')
            add_file(built, 'jarvis-backend.exe')
            launch = '@echo off\r\nsetlocal DisableDelayedExpansion\r\n'
            if runtime:
                for relative in sorted(runtime['files']):
                    add_file(runtime['directory'] / relative, 'WebView2Runtime/' + relative)
                launch += 'set "WEBVIEW2_BROWSER_EXECUTABLE_FOLDER=%~dp0WebView2Runtime"\r\n'
            add_text('Start-Portable.cmd', launch + '"%~dp0USMAN-JARVIS.exe" --portable\r\n')
            add_text('Reset-Settings.cmd', launch + 'echo This starts JARVIS with new default model settings. Previous settings generations and database records are retained.\r\npause\r\n"%~dp0USMAN-JARVIS.exe" --portable --reset-settings\r\n')
            requirement = 'A reviewed fixed WebView2 runtime is included; updates must be managed explicitly.' if runtime else 'WebView2 must already be installed.'
            add_text('PORTABLE.txt', 'Use Start-Portable.cmd. Data stays in the data directory. ' + requirement + ' Model runtime/weights are not included. This unsigned package still requires acceptance verification.\n')
            bundle.writestr('package-manifest.json', json.dumps({'format': 'usman-jarvis-portable-v1', 'architecture': 'x64', 'files': package_files}, sort_keys=True))
        for path in (installer, archive):
            digest = hashlib.sha256()
            with path.open('rb') as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
            manifest['files'][path.name] = digest.hexdigest()
        (artifacts / 'release-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        inspect_release(artifacts)
        # Publish an intact directory only after bounded integrity inspection.
        # Windows rename refuses an existing destination; no user output replaced.
        artifacts.rename(output)
        print('Artifacts assembled. They are not acceptance-verified or signed.')


if __name__ == '__main__':
    main()
