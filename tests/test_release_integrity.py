"""Inert byte fixtures only. Never launches installers or application code."""

import hashlib
import json
import zipfile

import pytest

from scripts.release_integrity import inspect_portable, inspect_release, member_name


def portable(tmp_path, *, corrupt=False, extra=False):
    # Minimal inert PE-shaped bytes, not executable code or a real build artifact.
    pe = bytearray(256)
    pe[:2] = b'MZ'
    pe[60:64] = (128).to_bytes(4, 'little')
    pe[128:134] = b'PE\0\0\x64\x86'
    files = {'USMAN-JARVIS.exe': bytes(pe), 'jarvis-backend.exe': bytes(pe),
             'Start-Portable.cmd': b'fixture-only', 'Reset-Settings.cmd': b'fixture-only', 'PORTABLE.txt': b'fixture-only'}
    hashes = {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}
    if corrupt:
        hashes['PORTABLE.txt'] = '0' * 64
    archive = tmp_path / 'USMAN-JARVIS-portable.zip'
    with zipfile.ZipFile(archive, 'x') as bundle:
        for name, value in files.items():
            bundle.writestr(name, value)
        if extra:
            bundle.writestr('data/private.sqlite', b'forbidden-user-persistence-fixture')
        bundle.writestr('package-manifest.json', json.dumps({'format': 'usman-jarvis-portable-v1', 'files': hashes}))
    return archive


def test_inert_package_integrity_is_distinct_from_execution_acceptance(tmp_path):
    result = inspect_portable(portable(tmp_path))
    assert result['status'] == 'integrity_inspected'
    assert result['members'] == 6
    assert 'do not prove' in result['limitations']


@pytest.mark.parametrize('corrupt,extra', [(True, False), (False, True)])
def test_mismatch_or_user_persistence_is_rejected(tmp_path, corrupt, extra):
    with pytest.raises(ValueError):
        inspect_portable(portable(tmp_path, corrupt=corrupt, extra=extra))


@pytest.mark.parametrize('path', ['../outside', '/root', 'C:/drive', 'a\\b', 'a/../b', 'CON.txt', 'folder/name.', 'folder/name '])
def test_unsafe_windows_names_are_rejected(path):
    with pytest.raises(ValueError):
        member_name(path)


def test_release_exact_artifact_inventory_and_outer_hashes(tmp_path):
    archive = portable(tmp_path)
    installer = tmp_path / 'Fixture-installer.exe'
    installer.write_bytes(b'inert installer fixture, never launched')
    manifest = {'format_version': 1, 'files': {item.name: hashlib.sha256(item.read_bytes()).hexdigest() for item in (archive, installer)}}
    (tmp_path / 'release-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    assert inspect_release(tmp_path)['status'] == 'integrity_inspected'
    installer.write_bytes(b'changed installer fixture')
    with pytest.raises(ValueError, match='checksum mismatch'):
        inspect_release(tmp_path)


def test_duplicate_windows_archive_aliases_rejected(tmp_path):
    archive = portable(tmp_path)
    with zipfile.ZipFile(archive, 'a') as bundle:
        bundle.writestr('portable.TXT', b'alias')
    with pytest.raises(ValueError, match='duplicate Windows aliases'):
        inspect_portable(archive)


def test_oversized_directory_is_rejected_before_zip_inventory_allocation(tmp_path):
    archive = portable(tmp_path)
    contents = bytearray(archive.read_bytes())
    footer = contents.rfind(b'PK\x05\x06')
    contents[footer + 10:footer + 12] = (65000).to_bytes(2, 'little')
    archive.write_bytes(contents)
    with pytest.raises(ValueError, match='directory is oversized'):
        inspect_portable(archive)
