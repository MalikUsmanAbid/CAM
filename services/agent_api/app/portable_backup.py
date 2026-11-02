"""Portable password-encrypted bundles and explicit encrypted-only HTTPS upload."""

import hashlib
import os
import re
import stat
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from services.agent_api.app.backup_bundle import BUNDLE_LIMIT
from services.agent_api.app.network_security import NetworkDestinationPolicy, PinnedHTTPProxy

HEADER = b'USMAN-JARVIS-SCRYPT-AES256GCM-1\n'
MAX_ENCRYPTED = BUNDLE_LIMIT + len(HEADER) + 16 + 12 + 16


def _aes():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise ValueError('Portable encryption requires the optional recovery dependency; no plaintext fallback is allowed.') from None
    return AESGCM


def _key(passphrase, salt):
    if not isinstance(passphrase, str) or not 16 <= len(passphrase.encode()) <= 1024:
        raise ValueError('Use a recovery passphrase between 16 and 1024 UTF-8 bytes; keep it outside the backup.')
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=32768, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)


def encrypt_payload(payload, passphrase):
    if not isinstance(payload, bytes) or len(payload) > BUNDLE_LIMIT:
        raise ValueError('Backup payload exceeds the portable encryption bound.')
    algorithm = _aes()
    salt, nonce = os.urandom(16), os.urandom(12)
    prefix = HEADER + salt + nonce
    return prefix + algorithm(_key(passphrase, salt)).encrypt(nonce, payload, prefix)


def decrypt_payload(payload, passphrase):
    if not isinstance(payload, bytes) or not payload.startswith(HEADER) or not len(HEADER) + 44 <= len(payload) <= MAX_ENCRYPTED:
        raise ValueError('Unsupported or oversized portable encrypted bundle.')
    algorithm = _aes()
    from cryptography.exceptions import InvalidTag
    salt = payload[len(HEADER):len(HEADER) + 16]
    nonce = payload[len(HEADER) + 16:len(HEADER) + 28]
    prefix = payload[:len(HEADER) + 28]
    try:
        return algorithm(_key(passphrase, salt)).decrypt(nonce, payload[len(prefix):], prefix)
    except InvalidTag:
        raise ValueError('Recovery passphrase is wrong or the encrypted backup was modified.') from None


async def upload_encrypted_bundle(path, endpoint, *, passphrase, expected_sha256, credential, allow_storage_charges, authorize,
                                  client_factory=httpx.AsyncClient, proxy_factory=PinnedHTTPProxy):
    """One explicit conditional PUT; no automatic retry or provider enrollment.

    The caller supplies authorization checks. A conditional PUT must be honored
    by the configured storage server; this adapter cannot prove server retention,
    billing, durability or remote deletion semantics.
    """
    authorize()
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None, 443):
        raise ValueError('Storage requires an exact HTTPS object URL on port 443 without credentials, query or fragment.')
    if not allow_storage_charges:
        raise ValueError('Explicit consent to provider storage charges is required; unknown pricing is not treated as free.')
    path = Path(path)
    payload = bounded_bundle_bytes(path)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected_sha256:
        raise ValueError('Backup changed since inspection; approve its new hash before uploading.')
    # Authenticate the envelope before releasing it; a forged magic prefix is
    # never accepted as proof that arbitrary plaintext is encrypted.
    plaintext = decrypt_payload(payload, passphrase)
    del plaintext
    if not isinstance(credential, str) or not credential or len(credential) > 16000 or any(character.isspace() for character in credential):
        raise ValueError('A bounded protected bearer credential is required.')
    policy = NetworkDestinationPolicy((parsed.hostname,), exact_hosts=True, allowed_ports={443})
    proxy = proxy_factory(policy)
    proxy_url = await proxy.start()
    try:
        async with client_factory(proxy=proxy_url, trust_env=False, follow_redirects=False, timeout=30) as client:
            authorize()
            async with client.stream('PUT', endpoint, content=payload, headers={
                'Authorization': 'Bearer ' + credential, 'Content-Type': 'application/octet-stream', 'If-None-Match': '*',
            }) as response:
                if response.status_code == 412:
                    return {'status': 'object_exists', 'sha256': digest, 'retry': 'Choose a new explicitly approved object URL.'}
                if response.status_code in (200, 201, 204):
                    return {'status': 'accepted', 'sha256': digest, 'durability': 'Provider acceptance is not a restore verification.'}
                return {'status': 'outcome_unknown', 'http_status': response.status_code, 'retry': 'Reconcile remote state before retrying.'}
    except httpx.HTTPError:
        return {'status': 'outcome_unknown', 'retry': 'Reconcile remote state before retrying.'}
    finally:
        await proxy.close()


def bounded_bundle_bytes(path):
    """Read at most the encrypted envelope limit, including a growth race."""
    with Path(path).open('rb') as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_ENCRYPTED:
            raise ValueError('Choose a bounded regular encrypted bundle.')
        payload = stream.read(MAX_ENCRYPTED + 1)
    if len(payload) > MAX_ENCRYPTED:
        raise ValueError('Encrypted bundle grew beyond its limit.')
    return payload


async def download_encrypted_bundle(endpoint, new_directory, *, passphrase, expected_sha256, credential,
                                    allow_storage_charges, authorize, client_factory=httpx.AsyncClient,
                                    proxy_factory=PinnedHTTPProxy):
    """Explicit GET to a new directory; no restore, migration or auto retry.

    The expected digest must come from the user's separately retained manifest.
    Authentication of ciphertext is not proof of a valid/restorable database.
    Restore performs the independent archive/schema/integrity checks.
    """
    authorize()
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None, 443):
        raise ValueError('Download requires an exact public HTTPS object URL without credentials, query or fragment.')
    if not isinstance(expected_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
        raise ValueError('Supply the exact SHA-256 from your separately retained backup manifest.')
    if not allow_storage_charges:
        raise ValueError('Explicit consent to provider transfer charges is required.')
    if not isinstance(credential, str) or not credential or len(credential) > 16000 or any(value.isspace() for value in credential):
        raise ValueError('A bounded protected bearer credential is required.')
    target = Path(new_directory).absolute()
    parent = target.parent.resolve(strict=True)
    target = parent / target.name
    if not parent.is_dir() or target.exists() or target.is_symlink() or target == parent:
        raise ValueError('Choose an absent download directory in an existing parent.')
    policy = NetworkDestinationPolicy((parsed.hostname,), exact_hosts=True, allowed_ports={443})
    proxy = proxy_factory(policy)
    proxy_url = await proxy.start()
    try:
        async with client_factory(proxy=proxy_url, trust_env=False, follow_redirects=False, timeout=30) as client:
            authorize()
            async with client.stream('GET', endpoint, headers={'Authorization': 'Bearer ' + credential,
                'Accept': 'application/octet-stream', 'Accept-Encoding': 'identity'}) as response:
                if response.status_code != 200:
                    raise ValueError('Download did not return the exact object directly; no redirect or retry was attempted.')
                if response.headers.get('content-encoding', 'identity').strip().lower() != 'identity':
                    raise ValueError('Compressed transfer encoding is unsupported for bounded recovery.')
                payload = bytearray()
                async for chunk in response.aiter_raw():
                    authorize()
                    if len(payload) + len(chunk) > MAX_ENCRYPTED:
                        raise ValueError('Downloaded backup exceeds its encrypted size bound.')
                    payload.extend(chunk)
        payload = bytes(payload)
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise ValueError('Downloaded object differs from the retained manifest; no local output was created.')
        plaintext = decrypt_payload(payload, passphrase)
        del plaintext
        authorize()
        from services.agent_api.app.backup_bundle import _new_directory
        with _new_directory(target):
            destination = target / 'backup.jarvis.encrypted'
            with destination.open('xb') as output:
                authorize()
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
        return {'status': 'downloaded_authenticated_ciphertext', 'path': str(destination), 'sha256': expected_sha256,
                'restored': False, 'next_step': 'Explicit restore into a separate absent local data directory; database compatibility and integrity are checked there.'}
    except httpx.HTTPError:
        raise ValueError('Encrypted download failed; no automatic retry was attempted.') from None
    finally:
        await proxy.close()
