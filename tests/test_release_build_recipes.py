"""Deferred recipe regressions: inert assets/injected tools, no compiler or app."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import build_browser_native, build_desktop


def inert_pe():
    data = bytearray(256)
    data[:2] = b'MZ'
    data[60:64] = (128).to_bytes(4, 'little')
    data[128:134] = b'PE\0\0\x64\x86'
    return bytes(data)


@pytest.mark.parametrize('generator', ['Visual Studio 17 2022', 'Ninja'])
def test_native_build_emits_exact_denied_inventory_without_launch(tmp_path, monkeypatch, generator):
    cmake = tmp_path / 'fixture-cmake.exe'
    cmake.write_bytes(b'not an executable')
    calls = []
    def invoke(arguments, **options):
        calls.append((arguments, options))
        if '--build' in arguments:
            release = Path(arguments[2])
            if generator == 'Visual Studio 17 2022':
                release = release / 'Release'
                release.mkdir()
            for name in build_browser_native.ASSETS:
                (release / name).write_bytes(inert_pe())
    monkeypatch.setattr(build_browser_native, 'os', SimpleNamespace(name='nt', environ={'DATABASE_PATH': 'must not inherit'}))
    monkeypatch.setattr(build_browser_native.subprocess, 'run', invoke)
    destination = tmp_path / 'native-output'
    build_browser_native.build(destination, cmake, generator)
    manifest = json.loads((destination / 'native-manifest.json').read_text())
    assert set(manifest['files']) == set(build_browser_native.ASSETS)
    assert manifest['activation'] == 'denied' and manifest['acceptance'] == 'not performed'
    assert len(calls) == 2
    configure = calls[0][0]
    assert configure[configure.index('-G') + 1] == generator
    assert ('-A' in configure) is (generator == 'Visual Studio 17 2022')
    assert ('-DCMAKE_BUILD_TYPE=Release' in configure) is (generator == 'Ninja')
    assert all('DATABASE_PATH' not in options['env'] and options['shell'] is False for _, options in calls)


@pytest.mark.parametrize('npm_cli', [False, True])
@pytest.mark.parametrize('reuse_sidecar', [False, True])
def test_desktop_recipe_ignores_stale_installer_and_publishes_inspected_directory(tmp_path, monkeypatch, npm_cli, reuse_sidecar):
    root = tmp_path / 'source'
    native = root / 'apps' / 'desktop' / 'src-tauri'
    stale = native / 'target' / 'x86_64-pc-windows-msvc' / 'release' / 'bundle' / 'nsis'
    stale.mkdir(parents=True)
    (stale / 'old.exe').write_bytes(b'must not ship')
    calls = []
    def invoke(arguments, directory, environment):
        calls.append((arguments, dict(environment)))
        if 'PyInstaller' in arguments:
            dist = Path(arguments[arguments.index('--distpath') + 1])
            dist.mkdir()
            (dist / 'jarvis-backend.exe').write_bytes(inert_pe())
        elif 'tauri' in arguments or any(str(value).endswith('tauri.cmd') for value in arguments):
            release = Path(environment['CARGO_TARGET_DIR']) / 'x86_64-pc-windows-msvc' / 'release'
            installers = release / 'bundle' / 'nsis'
            installers.mkdir(parents=True)
            (release / 'usman-jarvis-desktop.exe').write_bytes(inert_pe())
            (installers / 'fresh.exe').write_bytes(inert_pe())
    monkeypatch.setattr(build_desktop, 'ROOT', root)
    monkeypatch.setattr(build_desktop, 'os', SimpleNamespace(name='nt', environ={'CARGO_TARGET_DIR': str(stale), 'DATABASE_PATH': 'private'}))
    monkeypatch.setattr(build_desktop.importlib.util, 'find_spec', lambda _: object())
    monkeypatch.setattr(build_desktop.shutil, 'which', lambda name: 'fixture-' + name)
    monkeypatch.setattr(build_desktop, 'run', invoke)
    destination = tmp_path / 'release'
    arguments = ['build_desktop', '--output-directory', str(destination)]
    if npm_cli:
        launcher = tmp_path / 'tauri.cmd'
        launcher.write_text('inert CLI fixture')
        arguments.extend(['--tauri-cli', str(launcher)])
    if reuse_sidecar:
        sidecar = tmp_path / 'reviewed-sidecar.exe'
        sidecar.write_bytes(inert_pe())
        arguments.extend(['--sidecar', str(sidecar)])
    monkeypatch.setattr(sys, 'argv', arguments)
    build_desktop.main()
    assert {path.name for path in destination.iterdir()} == {'fresh.exe', 'USMAN-JARVIS-portable.zip', 'release-manifest.json'}
    assert (stale / 'old.exe').read_bytes() == b'must not ship'
    assert all('DATABASE_PATH' not in environment and environment['CARGO_TARGET_DIR'] != str(stale) for _, environment in calls)
    assert all(environment['CARGO_BUILD_JOBS'] == '1' for _, environment in calls)
    assert any('PyInstaller' in arguments for arguments, _ in calls) is not reuse_sidecar


def test_native_build_refuses_existing_output_before_any_build(tmp_path, monkeypatch):
    monkeypatch.setattr(build_browser_native, 'os', SimpleNamespace(name='nt', environ={}))
    destination = tmp_path / 'existing'
    destination.mkdir()
    (destination / 'user.txt').write_text('preserve')
    with pytest.raises(ValueError, match='absent'):
        build_browser_native.build(destination, tmp_path / 'missing.exe')
    assert (destination / 'user.txt').read_text() == 'preserve'
