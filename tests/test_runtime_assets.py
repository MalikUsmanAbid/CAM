"""Release asset regressions use inert temporary byte fixtures, never executables."""

import hashlib
import json

import pytest

from scripts.runtime_assets import canonical_asset_path, snapshot_runtime


def runtime_fixture(tmp_path):
    root = tmp_path / 'source-runtime'
    root.mkdir()
    # Minimal inert PE metadata only; this is not a runnable Windows binary.
    header = bytearray(128)
    header[:2] = b'MZ'
    header[60:64] = (128).to_bytes(4, 'little')
    data = bytes(header) + b'PE\x00\x00\x64\x86'
    (root / 'msedgewebview2.exe').write_bytes(data)
    (root / 'locales').mkdir()
    (root / 'locales' / 'fixture.pak').write_bytes(b'Inert fixture resource')
    files = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob('*') if path.is_file()}
    manifest = tmp_path / 'reviewed-manifest.json'
    manifest.write_text(json.dumps({'format': 'jarvis-runtime-assets-v1', 'files': files}))
    return root, manifest, files


def test_release_snapshot_copies_only_exact_reviewed_bytes_without_changing_source(tmp_path):
    root, manifest, files = runtime_fixture(tmp_path)
    output = tmp_path / 'new-runtime-snapshot'
    result = snapshot_runtime(root, manifest, output)
    assert result['files'] == files
    for relative, expected in files.items():
        assert hashlib.sha256((output / relative).read_bytes()).hexdigest() == expected
        assert (root / relative).read_bytes() == (output / relative).read_bytes()
    with pytest.raises(ValueError, match='absent'):
        snapshot_runtime(root, manifest, output)


def test_changed_and_unreviewed_runtime_files_are_rejected(tmp_path):
    root, manifest, _ = runtime_fixture(tmp_path)
    (root / 'unreviewed.dll').write_bytes(b'not approved')
    with pytest.raises(ValueError, match='unreviewed'):
        snapshot_runtime(root, manifest, tmp_path / 'blocked-extra')
    assert not (tmp_path / 'blocked-extra').exists()
    # Preserve that source; use a separate fixture for changed approved content.
    separate = tmp_path / 'separate'
    separate.mkdir()
    second, reviewed, _ = runtime_fixture(separate)
    (second / 'locales' / 'fixture.pak').write_bytes(b'Changed after review')
    with pytest.raises(ValueError, match='differs'):
        snapshot_runtime(second, reviewed, separate / 'blocked-changed')
    assert (second / 'locales' / 'fixture.pak').read_bytes() == b'Changed after review'


@pytest.mark.parametrize('path', ['.', '../outside', '/absolute', 'C:/outside', 'locales/../outside', 'CON.txt', 'a/stream:secret', 'a./file', 'double//slash'])
def test_runtime_paths_reject_windows_aliases_and_traversal(path):
    with pytest.raises(ValueError):
        canonical_asset_path(path)
