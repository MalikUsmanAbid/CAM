"""Disposable release-verifier guard checks; no packaged app or user data."""

import os
import socket
import zipfile

import pytest

from scripts import verify_portable_desktop as portable
from services.agent_api.app.windows_commands import _kernel, create_job

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows ownership and file sharing')


def test_debugger_listener_outside_owned_job_is_rejected():
    kernel = _kernel()
    job = create_job(kernel)
    try:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            assert not portable.owned_listener(kernel, job, listener.getsockname()[1])
    finally:
        kernel.CloseHandle(job)


def test_archive_cannot_change_between_inspection_and_extraction(tmp_path, monkeypatch):
    archive = tmp_path / 'inert.zip'
    archive.write_bytes(b'original')

    def inspect(path, screenshot):
        with pytest.raises(PermissionError):
            path.write_bytes(b'replacement')
        return {'fixture': True}

    monkeypatch.setattr(portable, '_verify_locked', inspect)
    assert portable.verify(archive, tmp_path / 'unused.png') == {'fixture': True}
    archive.write_bytes(b'unlocked after verification')


def test_unsafe_archive_is_rejected_before_app_launch_or_extraction(tmp_path, monkeypatch):
    archive = tmp_path / 'unsafe.zip'
    with zipfile.ZipFile(archive, 'w') as source:
        source.writestr('../escape.txt', 'forbidden')

    def forbidden(*args):
        pytest.fail('An unsafe archive must not launch a desktop process.')

    monkeypatch.setattr(portable, 'desktop', forbidden)
    with pytest.raises(ValueError):
        portable.verify(archive, tmp_path / 'unused.png')
    assert not (tmp_path / 'escape.txt').exists()


def test_installer_refuses_existing_registration_before_opening_payload(tmp_path, monkeypatch):
    from scripts import verify_installer_desktop as installer

    def existing():
        raise ValueError('Existing JARVIS registration')

    def forbidden(*args, **kwargs):
        pytest.fail('Existing installations must never reach installation or cleanup.')

    monkeypatch.setattr(installer, 'preflight', existing)
    monkeypatch.setattr(installer, 'contained', forbidden)
    monkeypatch.setattr(installer, 'remove_location', forbidden)
    with pytest.raises(ValueError, match='Existing JARVIS'):
        installer.verify(tmp_path / 'not-opened', tmp_path / 'unused.png')


def test_installer_payload_allows_only_the_exact_tauri_bundle_marker_patch():
    import hashlib

    from scripts.verify_installer_desktop import expected_desktop_hashes

    original = b'unchanged PE fixture prefix__TAURI_BUNDLE_TYPE_VAR_UNKunchanged suffix'
    patched = original.replace(b'_UNK', b'_NSS')
    allowed = expected_desktop_hashes(original)
    assert hashlib.sha256(original).hexdigest() in allowed
    assert hashlib.sha256(patched).hexdigest() in allowed
    assert hashlib.sha256(b'changed' + patched).hexdigest() not in allowed
    with pytest.raises(ValueError, match='Ambiguous'):
        expected_desktop_hashes(original + original)
