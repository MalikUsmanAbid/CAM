"""Portable recovery regressions; source only until the deferred verification run."""

import hashlib

import httpx
import pytest

from services.agent_api.app.portable_backup import (
    decrypt_payload,
    download_encrypted_bundle,
    encrypt_payload,
    upload_encrypted_bundle,
)


@pytest.fixture(autouse=True)
def recovery_dependency():
    pytest.importorskip('cryptography', reason='Optional recovery dependency must be installed for encryption verification.')


def test_portable_encryption_authenticates_data_header_and_passphrase():
    original = b'Bounded backup fixture'
    password = 'fixture-long-recovery-passphrase'
    encrypted = encrypt_payload(original, password)
    assert original not in encrypted
    assert decrypt_payload(encrypted, password) == original
    assert encrypted != encrypt_payload(original, password)
    with pytest.raises(ValueError):
        decrypt_payload(encrypted, 'different-long-recovery-passphrase')
    with pytest.raises(ValueError):
        decrypt_payload(encrypted[:-1] + bytes([encrypted[-1] ^ 1]), password)
    with pytest.raises(ValueError):
        encrypt_payload(original, 'short')


@pytest.mark.anyio
async def test_cloud_transport_receives_only_authenticated_ciphertext_and_no_redirects(tmp_path):
    payload = encrypt_payload(b'Private backup fixture', 'fixture-long-recovery-passphrase')
    bundle = tmp_path / 'fixture.encrypted'
    bundle.write_bytes(payload)
    requests, options, authorized = [], [], []
    class Proxy:
        def __init__(self, policy):
            assert policy.exact_hosts and not policy.allow_private_addresses
            assert policy.allowed_ports == {443}
        async def start(self):
            return 'http://127.0.0.1:54321'
        async def close(self):
            pass
    def transport(request):
        requests.append(request)
        return httpx.Response(201)
    def factory(**kwargs):
        options.append(kwargs)
        kwargs.pop('proxy')
        return httpx.AsyncClient(transport=httpx.MockTransport(transport), **kwargs)
    result = await upload_encrypted_bundle(bundle, 'https://storage.example.com/exact-object',
        passphrase='fixture-long-recovery-passphrase', expected_sha256=hashlib.sha256(payload).hexdigest(),
        credential='fixture-access', allow_storage_charges=True, authorize=lambda: authorized.append(True),
        client_factory=factory, proxy_factory=Proxy)
    assert result['status'] == 'accepted'
    assert requests[0].content == payload
    assert b'Private backup fixture' not in requests[0].content
    assert requests[0].headers['If-None-Match'] == '*'
    assert options[0]['trust_env'] is False and options[0]['follow_redirects'] is False
    assert len(authorized) >= 2
    with pytest.raises(ValueError):
        await upload_encrypted_bundle(bundle, 'https://storage.example.com/exact-object',
            passphrase='fixture-long-recovery-passphrase', expected_sha256='a' * 64,
            credential='fixture-access', allow_storage_charges=True, authorize=lambda: None,
            client_factory=factory, proxy_factory=Proxy)
    assert len(requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize('failure', [None, 'digest', 'passphrase', 'redirect'])
async def test_cloud_download_authenticates_before_creating_an_absent_directory(tmp_path, failure):
    password = 'fixture-long-recovery-passphrase'
    payload = encrypt_payload(b'Private backup fixture', password)
    destination = tmp_path / 'download'
    requests, closed = [], []
    class Proxy:
        def __init__(self, policy):
            assert policy.exact_hosts and not policy.allow_private_addresses
        async def start(self):
            return 'http://127.0.0.1:54321'
        async def close(self):
            closed.append(True)
    def transport(request):
        requests.append(request)
        assert request.method == 'GET' and request.headers['Accept-Encoding'] == 'identity'
        if failure == 'redirect':
            return httpx.Response(302, headers={'Location': 'http://127.0.0.1/private'}, stream=httpx.ByteStream(b''))
        return httpx.Response(200, stream=httpx.ByteStream(payload))
    def factory(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        kwargs.pop('proxy')
        return httpx.AsyncClient(transport=httpx.MockTransport(transport), **kwargs)
    arguments = dict(passphrase='different-long-recovery-passphrase' if failure == 'passphrase' else password,
        expected_sha256='0' * 64 if failure == 'digest' else hashlib.sha256(payload).hexdigest(),
        credential='fixture-access', allow_storage_charges=True, authorize=lambda: None,
        client_factory=factory, proxy_factory=Proxy)
    if failure:
        with pytest.raises(ValueError):
            await download_encrypted_bundle('https://storage.example.com/exact-object', destination, **arguments)
        assert not destination.exists()
    else:
        result = await download_encrypted_bundle('https://storage.example.com/exact-object', destination, **arguments)
        assert result['restored'] is False
        assert (destination / 'backup.jarvis.encrypted').read_bytes() == payload
        with pytest.raises(ValueError, match='absent'):
            await download_encrypted_bundle('https://storage.example.com/exact-object', destination, **arguments)
    assert len(requests) == 1 and closed == [True]
