"""Hash-pinned runtime asset snapshots for later offline release assembly.

Reads only an explicit extracted runtime tree plus a separately reviewed
manifest. Hash equality does not establish publisher authenticity or licensing.
No download, installation, executable invocation or in-place replacement.
"""

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath

MAX_FILES = 8000
MAX_TOTAL = 2 * 1024 ** 3
MAX_FILE = 512 * 1024 ** 2
RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *('COM' + str(i) for i in range(1, 10)), *('LPT' + str(i) for i in range(1, 10))}


def canonical_asset_path(value):
    if not isinstance(value, str) or not value or len(value) > 500 or '\\' in value or '\x00' in value:
        raise ValueError('Runtime manifest needs bounded canonical relative paths.')
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or str(path) != value or any(part in {'.', '..'} or part.endswith(('.', ' ')) or any(ord(character) < 32 for character in part)
        or any(character in part for character in ':<>"|?*') or part.split('.')[0].upper() in RESERVED for part in path.parts):
        raise ValueError('Runtime manifest path is unsafe or ambiguous on Windows.')
    return path


def read_manifest(path, required_executable='msedgewebview2.exe'):
    with Path(path).open('rb') as source:
        data = source.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError('Runtime manifest exceeds its bound.')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Runtime manifest contains duplicate fields.')
            result[key] = value
        return result
    document = json.loads(data, object_pairs_hook=unique)
    if not isinstance(document, dict) or set(document) != {'format', 'files'} or document['format'] != 'jarvis-runtime-assets-v1':
        raise ValueError('Unsupported runtime manifest format.')
    entries = document['files']
    if not isinstance(entries, dict) or not 1 <= len(entries) <= MAX_FILES:
        raise ValueError('Runtime manifest file count exceeds its bound.')
    identities = set()
    for name, digest in entries.items():
        canonical_asset_path(name)
        if name.casefold() in identities or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('Runtime manifest has colliding paths or invalid SHA-256 values.')
        identities.add(name.casefold())
    canonical_asset_path(required_executable)
    if '/' in required_executable or required_executable not in entries:
        raise ValueError('Runtime manifest must include its selected root executable.')
    return entries


def require_x64_runtime(path):
    with Path(path).open('rb') as binary:
        header = binary.read(64)
        if len(header) != 64 or header[:2] != b'MZ':
            raise ValueError('Runtime executable has no valid DOS/PE header.')
        offset = int.from_bytes(header[60:64], 'little')
        if not 64 <= offset <= 1024 * 1024:
            raise ValueError('Runtime PE header offset exceeds its inspection bound.')
        binary.seek(offset)
        signature = binary.read(6)
        if signature != b'PE\x00\x00\x64\x86':
            raise ValueError('Desktop assembly requires the reviewed x64 WebView2 runtime.')


def snapshot_runtime(directory, manifest, destination, *, required_executable='msedgewebview2.exe'):
    selected = Path(directory).absolute()
    if selected.is_symlink() or selected.is_junction():
        raise ValueError('Linked runtime roots are unsupported.')
    root = selected.resolve(strict=True)
    target = Path(destination).absolute()
    if not root.is_dir() or target.exists() or target.is_symlink() or not target.parent.is_dir() or target.resolve().is_relative_to(root):
        raise ValueError('Runtime snapshot requires an absent directory outside the source tree.')
    entries = read_manifest(manifest, required_executable)
    scanned, total, actual = 0, 0, set()
    for folder, directories, files in os.walk(root, followlinks=False):
        scanned += len(directories) + len(files)
        if scanned > MAX_FILES * 2 or len(Path(folder).relative_to(root).parts) > 30:
            raise ValueError('Runtime tree exceeds its traversal bound.')
        for name in [*directories, *files]:
            child = Path(folder) / name
            if child.is_symlink() or child.is_junction():
                raise ValueError('Linked runtime assets are unsupported.')
        for name in files:
            path = Path(folder) / name
            relative = path.relative_to(root).as_posix()
            if relative not in entries or not path.is_file():
                raise ValueError('Runtime tree includes unreviewed assets.')
            total += path.stat().st_size
            if path.stat().st_size > MAX_FILE or total > MAX_TOTAL:
                raise ValueError('Runtime assets exceed release bounds.')
            actual.add(relative)
    if actual != set(entries):
        raise ValueError('Runtime manifest is missing source assets or references absent files.')
    target.mkdir(exist_ok=False)
    copied_total = 0
    for relative, expected in sorted(entries.items()):
        source = root.joinpath(*canonical_asset_path(relative).parts)
        if source.is_symlink() or source.is_junction() or not source.resolve().is_relative_to(root):
            raise ValueError('Runtime source changed outside its approved tree.')
        output = target.joinpath(*PurePosixPath(relative).parts)
        output.parent.mkdir(parents=True, exist_ok=True)
        digest, copied = hashlib.sha256(), 0
        with source.open('rb') as reader, output.open('xb') as writer:
            metadata = os.fstat(reader.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError('Runtime source must be a regular unlinked file.')
            while chunk := reader.read(1024 * 1024):
                copied += len(chunk)
                copied_total += len(chunk)
                if copied > MAX_FILE or copied_total > MAX_TOTAL:
                    raise ValueError('Runtime asset grew beyond its release bound.')
                digest.update(chunk)
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        if digest.hexdigest() != expected:
            raise ValueError('Runtime asset differs from the reviewed manifest. Partial new snapshot retained; source was not modified.')
    require_x64_runtime(target / required_executable)
    return {'directory': target, 'files': entries, 'bytes': copied_total}
