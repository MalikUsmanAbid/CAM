"""Bounded read-only release/archive inspection; never extracts or launches code."""

import hashlib
import json
import re
import stat
import struct
import zipfile
from pathlib import Path, PurePosixPath

MAX_ARCHIVE_BYTES = 3 * 1024**3
MAX_TOTAL_BYTES = 2300 * 1024**2
MAX_ENTRY_BYTES = 512 * 1024**2
MAX_ENTRIES = 8100
SHA = re.compile(r'^[a-f0-9]{64}$')


def checksum(path):
    path = Path(path)
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise ValueError('Release artifact is linked or unavailable.')
    with path.open('rb') as source:
        if source.seek(0, 2) > MAX_ARCHIVE_BYTES:
            raise ValueError('Release artifact exceeds its size limit.')
        source.seek(0)
        result = hashlib.sha256()
        consumed = 0
        for block in iter(lambda: source.read(1024 * 1024), b''):
            consumed += len(block)
            if consumed > MAX_ARCHIVE_BYTES:
                raise ValueError('Growing release artifact exceeds its size limit.')
            result.update(block)
        return result.hexdigest()


def member_name(value):
    parts = PurePosixPath(value).parts
    if (not parts or value != '/'.join(parts) or '\\' in value or value.startswith('/')
            or any(part in {'.', '..'} or any(ord(char) < 32 or char in ':<>"|?*' for char in part) or part.endswith(('.', ' ')) for part in parts)):
        raise ValueError('Archive contains an ambiguous or unsafe member path.')
    # Windows device aliases are unsafe even when ZIP extraction libraries allow them.
    if any(re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part) for part in parts):
        raise ValueError('Archive contains a reserved Windows path.')
    return value


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Manifest has duplicate object keys.')
        result[key] = value
    return result


def parse_manifest(raw):
    if len(raw) > 2 * 1024**2:
        raise ValueError('Manifest exceeds its bound.')
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid JSON constant.')))


def bounded_zip(source):
    # Bound central-directory allocation before ZipFile parses it. Generated
    # packages fit ordinary ZIP limits; multi-disk/ZIP64 input is unsupported.
    size = source.seek(0, 2)
    if size > MAX_ARCHIVE_BYTES:
        raise ValueError('Portable archive exceeds its size limit.')
    source.seek(max(0, size - 65557))
    tail = source.read(65557)
    offset = tail.rfind(b'PK\x05\x06')
    if offset < 0 or len(tail) - offset < 22:
        raise ValueError('Archive directory footer is unavailable.')
    _, disk, central_disk, disk_count, count, central_size, central_offset, comment_size = struct.unpack_from('<4s4H2IH', tail, offset)
    footer_position = size - len(tail) + offset
    if (disk or central_disk or disk_count != count or count == 65535 or count > MAX_ENTRIES
            or central_size > 8 * 1024**2 or central_offset == 0xffffffff
            or central_offset + central_size > footer_position or offset + 22 + comment_size != len(tail)):
        raise ValueError('Archive directory is oversized, multi-disk, ZIP64 or inconsistent.')
    source.seek(0)
    return source


def inspect_portable(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError('Portable archive is linked or oversized.')
    with path.open('rb') as archive_source, zipfile.ZipFile(bounded_zip(archive_source)) as archive:
        inventory = archive.infolist()
        if len(inventory) > MAX_ENTRIES:
            raise ValueError('Archive member count exceeds its bound.')
        entries = {}
        folded = set()
        total = 0
        for info in inventory:
            name = member_name(info.filename)
            mode = info.external_attr >> 16
            if info.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)) or info.flag_bits & 1:
                raise ValueError('Only unencrypted ordinary archive files are supported.')
            if name.casefold() in folded:
                raise ValueError('Archive contains duplicate Windows aliases.')
            folded.add(name.casefold())
            total += info.file_size
            if info.file_size > MAX_ENTRY_BYTES or total > MAX_TOTAL_BYTES:
                raise ValueError('Archive expansion exceeds its bound.')
            entries[name] = info
        if 'package-manifest.json' not in entries:
            raise ValueError('Portable archive has no package manifest.')
        if entries['package-manifest.json'].file_size > 2 * 1024**2:
            raise ValueError('Package manifest exceeds its bound.')
        manifest = parse_manifest(archive.read('package-manifest.json'))
        if not isinstance(manifest, dict) or manifest.get('format') != 'usman-jarvis-portable-v1':
            raise ValueError('Unsupported package manifest.')
        files = manifest.get('files')
        if not isinstance(files, dict) or set(files) != set(entries) - {'package-manifest.json'}:
            raise ValueError('Package inventory differs from its exact manifest.')
        required = {'USMAN-JARVIS.exe', 'jarvis-backend.exe', 'Start-Portable.cmd', 'Reset-Settings.cmd', 'PORTABLE.txt'}
        if not required.issubset(files):
            raise ValueError('Portable package is missing required launch/runtime files.')
        for name, expected in files.items():
            member_name(name)
            if any(part.lower().startswith('.env') or part.lower() in {'credentials', '.ssh', 'data'}
                   or part.lower().endswith(('.sqlite', '.sqlite3', '.db', '.pem', '.key', '.dpapi', '.p12', '.pfx'))
                   for part in PurePosixPath(name).parts):
                raise ValueError('Potential user persistence or credentials are excluded from release payloads.')
            if not isinstance(expected, str) or not SHA.fullmatch(expected):
                raise ValueError('Invalid package member checksum.')
            if name not in required and not name.startswith('WebView2Runtime/'):
                raise ValueError('Unexpected portable payload. User data/configuration must not be packaged.')
            digest = hashlib.sha256()
            consumed = 0
            prefix = b''
            with archive.open(entries[name]) as source:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    consumed += len(block)
                    if consumed > MAX_ENTRY_BYTES or consumed > entries[name].file_size:
                        raise ValueError('Archive member expansion changed.')
                    if not prefix:
                        prefix = block[:4096]
                    digest.update(block)
            if consumed != entries[name].file_size or digest.hexdigest() != expected:
                raise ValueError('Portable package member checksum mismatch.')
            if name in {'USMAN-JARVIS.exe', 'jarvis-backend.exe'}:
                # Format/architecture inspection only, never authenticity or execution proof.
                if len(prefix) < 64 or prefix[:2] != b'MZ':
                    raise ValueError('Packaged executable has no PE header.')
                offset = int.from_bytes(prefix[60:64], 'little')
                if offset > len(prefix) - 6 or prefix[offset:offset + 4] != b'PE\0\0' or prefix[offset + 4:offset + 6] != b'\x64\x86':
                    raise ValueError('Packaged executable is not a bounded-header x64 PE.')
        return {'status': 'integrity_inspected', 'members': len(entries), 'expanded_bytes': total,
                'limitations': 'Hash/format checks do not prove publisher authenticity, installation, startup, security acceptance or AI behavior.'}


def inspect_release(directory):
    directory = Path(directory)
    raw = directory / 'release-manifest.json'
    if raw.is_symlink() or raw.stat().st_size > 2 * 1024**2:
        raise ValueError('Release manifest is linked or oversized.')
    with raw.open('rb') as source:
        manifest = parse_manifest(source.read(2 * 1024**2 + 1))
    if not isinstance(manifest, dict) or manifest.get('format_version') != 1 or not isinstance(manifest.get('files'), dict):
        raise ValueError('Unsupported release manifest.')
    files = manifest['files']
    if len(files) != 2 or 'USMAN-JARVIS-portable.zip' not in files or sum(name.endswith('.exe') for name in files) != 1:
        raise ValueError('Expected exactly one installer and one portable archive.')
    for name, expected in files.items():
        if member_name(name) != Path(name).name or not isinstance(expected, str) or not SHA.fullmatch(expected):
            raise ValueError('Release artifact name or checksum is invalid.')
        if checksum(directory / name) != expected:
            raise ValueError('Release artifact checksum mismatch.')
    if {item.name for item in directory.iterdir()} != set(files) | {'release-manifest.json'}:
        raise ValueError('Release directory contains undeclared files.')
    return inspect_portable(directory / 'USMAN-JARVIS-portable.zip')
