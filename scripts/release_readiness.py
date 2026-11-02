"""Read-only prerequisite inventory for the later Windows release stage.

Never runs an executable, installs tools, opens persistence or writes settings.
Discovery is not version compatibility, compilation or acceptance verification.
"""
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path


def inventory(root):
    root = Path(root)
    tools = {name: bool(shutil.which(name)) for name in ('cargo', 'rustc', 'cmake', 'npm.cmd', 'cl.exe')}
    # MSVC may be available through a developer prompt even when absent here.
    native = root / 'apps' / 'desktop' / 'src-tauri'
    checks = {
        'windows_host': os.name == 'nt',
        'python_312_or_newer': sys.version_info >= (3, 12),
        'pyinstaller_discoverable': importlib.util.find_spec('PyInstaller') is not None,
        'frontend_dependencies_present': (root / 'apps' / 'desktop' / 'node_modules').is_dir(),
        'rust_lockfile_present': (native / 'Cargo.lock').is_file(),
        'native_source_present': (root / 'native' / 'browser_isolation' / 'CMakeLists.txt').is_file(),
        'tauri_configuration_present': (native / 'tauri.conf.json').is_file(),
        'sidecar_destination_absent': not (native / 'binaries' / 'jarvis-backend-x86_64-pc-windows-msvc.exe').exists(),
    }
    return {
        'format': 'jarvis-release-readiness-v1', 'checks': checks, 'tools_on_current_path': tools,
        'missing_checks': [name for name, value in checks.items() if not value],
        'missing_tools': [name for name, value in tools.items() if not value],
        'acceptance': 'not performed',
        'not_proven': ['cargo-tauri subcommand/version', 'MSVC and Windows SDK compatibility',
                      'WebView2 runtime availability', 'publisher authenticity/licensing',
                      'installer/portable launch', 'native browser network isolation',
                      'browser loopback provisioning and privileged deployment', 'live AI behavior'],
    }


def main():
    print(json.dumps(inventory(Path(__file__).resolve().parents[1]), indent=2))


if __name__ == '__main__':
    main()
