"""Explicit later native build recipe. No installation, policy changes or launch.

Outputs experimental broker, network probe and read-only recovery inventory.
Compiling these files does not admit them to the runtime accepted-build gate.
"""
import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from scripts.release_integrity import checksum
from scripts.runtime_assets import require_x64_runtime

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ('jarvis-isolation-probe-host.exe', 'jarvis-network-probe.exe', 'jarvis-browser-recovery-inventory.exe')


def build(output, cmake, generator='Visual Studio 17 2022'):
    if generator not in {'Visual Studio 17 2022', 'Ninja'}:
        raise ValueError('Unsupported native build generator.')
    output = Path(output).absolute()
    if os.name != 'nt':
        raise ValueError('Native browser assembly requires Windows and an existing MSVC/SDK toolchain.')
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError('Select an absent output directory inside an existing parent.')
    executable = Path(cmake).resolve(strict=True)
    if not executable.is_file():
        raise ValueError('An existing CMake executable is required; nothing was installed.')
    keep = {'systemroot', 'windir', 'path', 'temp', 'tmp', 'userprofile', 'localappdata', 'appdata', 'programfiles',
            'programfiles(x86)', 'programw6432', 'include', 'lib', 'libpath', 'vsinstalldir', 'vcinstalldir',
            'windowssdkdir', 'vctoolsinstalldir', 'visualstudioversion', 'comspec', 'pathext'}
    environment = {key: value for key, value in os.environ.items() if key.lower() in keep}
    with tempfile.TemporaryDirectory(prefix='jarvis-native-build-') as temporary:
        directory = Path(temporary)
        configure_options = (['-A', 'x64'] if generator == 'Visual Studio 17 2022'
                             else ['-DCMAKE_BUILD_TYPE=Release'])
        release = directory / 'Release' if generator == 'Visual Studio 17 2022' else directory
        for arguments in (
            [str(executable), '-S', str(ROOT / 'native' / 'browser_isolation'), '-B', str(directory),
             '-G', generator, *configure_options],
            [str(executable), '--build', str(directory), '--config', 'Release', '--parallel', '1'],
        ):
            subprocess.run(arguments, env=environment, check=True, shell=False, timeout=1800)
        files = {}
        # Validate the whole source set before creating a destination.
        for name in ASSETS:
            source = release / name
            require_x64_runtime(source)
            files[name] = checksum(source)
        output.mkdir(exist_ok=False)
        for name in ASSETS:
            with (release / name).open('rb') as source, (output / name).open('xb') as destination:
                shutil.copyfileobj(source, destination)
            if checksum(output / name) != files[name]:
                raise ValueError('Native asset changed during assembly; output is incomplete.')
        (output / 'native-manifest.json').write_text(json.dumps({
            'format': 'jarvis-browser-native-source-v1', 'architecture': 'x64',
            'acceptance': 'not performed', 'activation': 'denied', 'files': files,
        }, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-directory', type=Path, required=True)
    parser.add_argument('--cmake', type=Path)
    parser.add_argument('--generator', choices=['Visual Studio 17 2022', 'Ninja'], default='Visual Studio 17 2022')
    arguments = parser.parse_args()
    cmake = arguments.cmake or shutil.which('cmake')
    if not cmake:
        raise SystemExit('CMake is missing; nothing was installed.')
    build(arguments.output_directory, cmake, arguments.generator)


if __name__ == '__main__':
    main()
