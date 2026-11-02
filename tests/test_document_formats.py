"""Isolated document regression source; execution deferred by user request."""

import base64
import gzip
import hashlib
import io
import zipfile

import httpx
import pytest

from services.agent_api.app.document_formats import artifact_bytes, docx_text
from services.agent_api.app.knowledge_library import (
    ArtifactInput,
    ExportArtifact,
    ImportText,
    KnowledgeLibrary,
)
from services.agent_api.app.migrations import initialize_database
from services.agent_api.app.research_sources import FetchSource, TextExtraction


def test_docx_export_round_trip_and_no_external_resources():
    content = 'A < B & C\nUrdu: سلام\nKeep this space '
    data, media = artifact_bytes(content, 'docx')
    assert media.endswith('wordprocessingml.document')
    assert docx_text(data) == content
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert set(archive.namelist()) == {'[Content_Types].xml', '_rels/.rels', 'word/document.xml'}
        assert b'TargetMode="External"' not in archive.read('_rels/.rels')


@pytest.mark.parametrize('xml', [
    b'<!DOCTYPE x [<!ENTITY payload "text">]><x>&payload;</x>',
    '<!DOCTYPE x [<!ENTITY payload "text">]><x>&payload;</x>'.encode('utf-16'),
])
def test_docx_rejects_entity_declarations_in_alternate_encodings(xml):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('word/document.xml', xml)
    with pytest.raises((ValueError, UnicodeError)):
        docx_text(output.getvalue())


def test_import_and_export_bind_binary_hash_and_project_scope(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    database = tmp_path / 'temporary.sqlite'
    initialize_database(database)
    data, _ = artifact_bytes('Inspected document text', 'docx')
    (root / 'source.docx').write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    library = KnowledgeLibrary(root, database, 'project-A')
    imported = library.import_docx(ImportText(path='source.docx', title='Source', source_sha256=digest))
    artifact = library.create_artifact(ArtifactInput(title='Draft', content='An attributed draft', sources=[imported['document_id']]))
    exported = library.export_artifact(ExportArtifact(artifact_id=artifact['artifact_id'], source_sha256=artifact['sha256'], format='docx'))
    binary = base64.b64decode(exported['content_base64'], validate=True)
    assert hashlib.sha256(binary).hexdigest() == exported['sha256']
    assert docx_text(binary) == artifact['content']
    with pytest.raises(ValueError, match='hash'):
        library.export_artifact(ExportArtifact(artifact_id=artifact['artifact_id'], source_sha256='0' * 64))
    with pytest.raises(KeyError):
        KnowledgeLibrary(root, database, 'project-B').export_artifact(ExportArtifact(artifact_id=artifact['artifact_id'], source_sha256=artifact['sha256']))
    (root / 'source.docx').write_bytes(artifact_bytes('Changed text', 'docx')[0])
    with pytest.raises(ValueError, match='changed'):
        library.import_docx(ImportText(path='source.docx', title='Source', source_sha256=digest))
    assert len(library.documents()) == 1


def test_research_contract_rejects_queries_credentials_and_redirect_parameters():
    for url in ['http://example.com/', 'https://name:password@example.com/', 'https://example.com/?redirect=private', 'https://example.com/#token', 'https://example.com:8443/']:
        with pytest.raises(ValueError):
            FetchSource(url=url, title='Source')
    assert FetchSource(url='https://example.com/docs', title='Source').url.endswith('/docs')


def test_html_extraction_does_not_execute_or_include_scripts():
    parser = TextExtraction()
    parser.feed('<p>Visible evidence</p><script>secret script text</script><style>hidden style</style><p>More evidence</p>')
    text = ''.join(parser.output)
    assert 'Visible evidence' in text and 'More evidence' in text
    assert 'secret script' not in text and 'hidden style' not in text
    with pytest.raises(ValueError):
        parser.feed('x' * 32001)


@pytest.mark.anyio
@pytest.mark.parametrize('response_kind', ['success', 'redirect', 'compressed'])
async def test_research_transport_stores_exact_snapshot_without_redirects(tmp_path, monkeypatch, response_kind):
    from services.agent_api.app import research_sources
    from services.agent_api.app.tools import ToolRegistry

    database = tmp_path / 'research.sqlite'
    initialize_database(database)
    root = tmp_path / 'project'
    root.mkdir()
    calls, proxies = [], []
    class Proxy:
        def __init__(self, policy):
            assert policy.exact_hosts and not policy.allow_private_addresses
            assert policy.allowed_domains == ('example.com',)
        async def start(self):
            proxies.append('started')
            return 'http://127.0.0.1:43210'
        async def close(self):
            proxies.append('closed')
    def transport(request):
        calls.append(request)
        assert request.headers['accept-encoding'] == 'identity'
        if response_kind == 'redirect':
            return httpx.Response(302, headers={'Location': 'http://127.0.0.1/private'})
        if response_kind == 'compressed':
            return httpx.Response(200, headers={'Content-Type': 'text/plain', 'Content-Encoding': 'gzip'}, content=gzip.compress(b'Compressed source excluded'))
        return httpx.Response(200, headers={'Content-Type': 'text/html'}, content=b'<p>Exact evidence</p><script>hidden</script>')
    real_client = httpx.AsyncClient
    def client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        assert kwargs.pop('proxy') == 'http://127.0.0.1:43210'
        return real_client(transport=httpx.MockTransport(transport), **kwargs)
    monkeypatch.setattr(research_sources, 'PinnedHTTPProxy', Proxy)
    monkeypatch.setattr(research_sources.httpx, 'AsyncClient', client)
    registry = ToolRegistry(root)
    research_sources.register_research_tool(registry, database, 'A')
    if response_kind != 'success':
        with pytest.raises(ValueError):
            await registry.get('research_fetch').handler(FetchSource(url='https://example.com/docs', title='Evidence'))
        assert len(calls) == 1 and proxies == ['started', 'closed']
        assert KnowledgeLibrary(root, database, 'A').documents() == []
        return
    result = await registry.get('research_fetch').handler(FetchSource(url='https://example.com/docs', title='Evidence'))
    assert len(calls) == 1 and str(calls[0].url) == 'https://example.com/docs'
    assert calls[0].method == 'GET' and 'authorization' not in calls[0].headers
    assert proxies == ['started', 'closed']
    library = KnowledgeLibrary(root, database, 'A')
    stored = library.document(result['document_id'])
    assert stored['content'] == 'Exact evidence'
    assert stored['source_sha'] == hashlib.sha256(b'Exact evidence').hexdigest()
    assert KnowledgeLibrary(root, database, 'B').documents() == []
