"""Bounded DOCX text import and macro-free text/DOCX artifact export."""

import io
import json
import os
import stat
import zipfile
from xml.etree import ElementTree
from xml.sax.saxutils import escape


def read_project_binary(files, relative, limit=4 * 1024 * 1024):
    target = files._safe(relative)
    fd = os.open(target, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        files.verify_opened(fd, target)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('Only regular project documents can be imported.')
        if os.fstat(fd).st_size > limit:
            raise ValueError('Document exceeds its bounded import size.')
        output = bytearray()
        while chunk := os.read(fd, min(65536, limit + 1 - len(output))):
            output.extend(chunk)
            if len(output) > limit:
                raise ValueError('Document grew beyond its import bound.')
        files.verify_opened(fd, target)
        return bytes(output)
    finally:
        os.close(fd)


def docx_text(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 200 or len({entry.filename for entry in entries}) != len(entries):
            raise ValueError('Document archive has excessive or duplicate entries.')
        entry = archive.getinfo('word/document.xml')
        if entry.file_size > 2 * 1024 * 1024 or entry.flag_bits & 1:
            raise ValueError('Encrypted or oversized document XML is unsupported.')
        xml = archive.read(entry)
    # Decode first so alternate byte encodings cannot conceal declarations.
    xml = xml.decode('utf-8-sig', errors='strict')
    if '\x00' in xml or '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():
        raise ValueError('Document type declarations and entities are unsupported.')
    root = ElementTree.fromstring(xml)
    namespace = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    paragraphs = []
    total = 0
    for paragraph in root.iter(namespace + 'p'):
        pieces = []
        for node in paragraph.iter():
            if node.tag == namespace + 't':
                pieces.append(node.text or '')
            elif node.tag == namespace + 'tab':
                pieces.append('\t')
            elif node.tag in (namespace + 'br', namespace + 'cr'):
                pieces.append('\n')
        text = ''.join(pieces)
        total += len(text.encode()) + 1
        if total > 32000:
            raise ValueError('Extracted document text exceeds its bound.')
        paragraphs.append(text)
    return '\n'.join(paragraphs)


def artifact_bytes(content, format):
    if len(content.encode()) > 32000:
        raise ValueError('Artifact exceeds its export bound.')
    if format == 'json':
        def reject_constant(_):
            raise ValueError('JSON cannot contain non-finite constants.')
        try:
            json.loads(content, parse_constant=reject_constant)
        except (ValueError, RecursionError):
            raise ValueError('Choose a valid bounded JSON artifact for JSON export.') from None
        return content.encode(), 'application/json;charset=utf-8'
    if format in ('txt', 'md'):
        return content.encode(), 'text/plain;charset=utf-8' if format == 'txt' else 'text/markdown;charset=utf-8'
    if format != 'docx' or any(ord(value) < 32 and value not in '\t\n\r' for value in content):
        raise ValueError('Unsupported export format or XML control characters.')
    body = ''.join('<w:p><w:r><w:t xml:space="preserve">' + escape(line) + '</w:t></w:r></w:p>' for line in content.split('\n'))
    document = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '<w:sectPr/></w:body></w:document>'
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr('word/document.xml', document)
    return stream.getvalue(), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
