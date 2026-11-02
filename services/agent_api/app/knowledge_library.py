"""Bounded project knowledge and versioned text artifacts, with explicit sources."""

import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator

from services.agent_api.app.action_policy import ActionCategory
from services.agent_api.app.document_formats import artifact_bytes, docx_text, read_project_binary
from services.agent_api.app.memory import is_sensitive_content
from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.project_files import ProjectFiles
from services.agent_api.app.session_security import execution_dispatch_authorized
from services.agent_api.app.tools import EmptyInput, Tool, ToolInput
from services.agent_api.app.workspace_records import WorkspaceRecords, now

Identifier = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]


class ImportText(ToolInput):
    path: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=200)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("title")
    @classmethod
    def safe_title(cls, value):
        if not value.strip() or is_sensitive_content(value):
            raise ValueError("Title is blank or contains potential credentials.")
        return value.strip()


class KnowledgeQuery(ToolInput):
    query: str = Field(min_length=1, max_length=200)

    @field_validator("query")
    @classmethod
    def safe_query(cls, value):
        if not value.strip() or is_sensitive_content(value):
            raise ValueError("Query is blank or contains potential credentials.")
        return value.strip()


class DocumentId(ToolInput):
    document_id: Identifier


class ForgetDocument(DocumentId):
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_forget: Literal[True]


class ArtifactId(ToolInput):
    artifact_id: Identifier


class LibraryPage(ToolInput):
    cursor: str | None = Field(default=None, min_length=1, max_length=1000)
    query: str = Field(default='', max_length=200)

    @field_validator('query')
    @classmethod
    def safe_query(cls, value):
        if is_sensitive_content(value):
            raise ValueError('Potential credentials are excluded from library queries.')
        return value.strip()


class SnapshotCursor(ToolInput):
    scope_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    identifier: Identifier
    created_at: datetime

    @field_validator('created_at')
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError('Snapshot cursor needs an explicit timezone.')
        return value.astimezone(UTC)


class ExportArtifact(ArtifactId):
    format: Literal["txt", "md", "docx", "json"] = "txt"
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ArtifactInput(ToolInput):
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["note", "research", "document", "learning", "content"] = "document"
    content: str = Field(min_length=1, max_length=32000)
    sources: list[Identifier] = Field(default_factory=list, max_length=20)
    previous_id: Identifier | None = None

    @field_validator("title", "content")
    @classmethod
    def safe_content(cls, value):
        if not value.strip() or is_sensitive_content(value):
            raise ValueError("Artifact text is blank or contains potential credentials.")
        return value


class KnowledgeLibrary:
    def __init__(self, root, database, project_id):
        self.files = ProjectFiles(root)
        self.records = WorkspaceRecords(database)
        self.project_id = project_id
        self.root = str(self.files.guard.root)

    def import_text(self, value):
        source = self.files.read(value.path, 32000)
        if source["sha256"] != value.source_sha256:
            raise ValueError("Source changed; inspect and approve its new hash.")
        identifier = uuid.uuid4().hex
        with self.records.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT COUNT(*) FROM knowledge_documents WHERE project_id=?", (self.project_id,)).fetchone()[0] >= 1000:
                raise ValueError("Project knowledge limit reached.")
            db.execute("INSERT INTO knowledge_documents VALUES (?,?,?,?,?,?,?,?)",
                       (identifier, self.project_id, self.root, value.title, source["path"], source["sha256"], source["content"], now()))
        return {"document_id": identifier, "title": value.title, "source_path": source["path"], "source_sha256": source["sha256"], "imported_snapshot": True}

    def documents(self):
        with self.records.connect() as db:
            return [dict(row) for row in db.execute("SELECT document_id,title,source_path,source_sha,created_at FROM knowledge_documents WHERE project_id=? AND root=? ORDER BY created_at DESC LIMIT 100", (self.project_id, self.root))]

    def page(self, collection, cursor=None, query=''):
        # Only internal names select SQL identifiers; cursor values remain data.
        contracts = {
            'documents': ('knowledge_documents', 'document_id', 'document_id,title,source_path,source_sha,created_at'),
            'artifacts': ('creation_artifacts', 'artifact_id', 'artifact_id,title,kind,version,previous_id,created_at'),
        }
        if collection not in contracts:
            raise ValueError('Unsupported library collection.')
        if not isinstance(query, str) or len(query) > 200 or is_sensitive_content(query):
            raise ValueError('Library query is oversized or contains potential credentials.')
        query = query.strip()
        table, identifier, columns = contracts[collection]
        scope = hashlib.sha256(json.dumps([self.project_id, self.root, collection, query]).encode()).hexdigest()
        predicate, arguments = 'project_id=? AND root=?', [self.project_id, self.root]
        if query:
            predicate += ' AND (instr(lower(title),lower(?))>0 OR instr(lower(content),lower(?))>0)'
            arguments.extend([query, query])
        if cursor is not None:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 1000:
                raise ValueError('Library cursor is invalid.')
            try:
                selected = SnapshotCursor.model_validate_json(base64.b64decode(cursor, altchars=b'-_', validate=True))
            except (ValueError, TypeError) as error:
                raise ValueError('Library cursor is invalid.') from error
            if selected.scope_sha256 != scope:
                raise ValueError('Library project, root, collection or query changed; start a new page.')
            predicate += f' AND (created_at<? OR (created_at=? AND {identifier}<?))'
            stamp = selected.created_at.isoformat()
            arguments.extend([stamp, stamp, selected.identifier])
        with self.records.connect() as db:
            rows = db.execute(f'SELECT {columns} FROM {table} WHERE {predicate} ORDER BY created_at DESC,{identifier} DESC LIMIT 51', arguments).fetchall()
        items, next_cursor = [dict(row) for row in rows[:50]], None
        if len(rows) > 50:
            last = items[-1]
            token = SnapshotCursor(scope_sha256=scope, identifier=last[identifier], created_at=last['created_at'])
            next_cursor = base64.urlsafe_b64encode(token.model_dump_json().encode()).decode('ascii')
        return {collection: items, 'next_cursor': next_cursor, 'limit': 50,
                'consistency': 'Live metadata pages; refresh to see newly imported or removed items. Content is read separately.'}

    def import_docx(self, value):
        if not value.path.lower().endswith('.docx'):
            raise ValueError('Choose a DOCX source.')
        data = read_project_binary(self.files, value.path)
        digest = hashlib.sha256(data).hexdigest()
        if digest != value.source_sha256:
            raise ValueError('Source changed; inspect and approve its new hash.')
        content = docx_text(data)
        if not content.strip() or is_sensitive_content(content):
            raise ValueError('Document is empty or contains potential credentials.')
        identifier = uuid.uuid4().hex
        with self.records.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM knowledge_documents WHERE project_id=?', (self.project_id,)).fetchone()[0] >= 1000:
                raise ValueError('Project knowledge limit reached.')
            db.execute('INSERT INTO knowledge_documents VALUES (?,?,?,?,?,?,?,?)',
                       (identifier, self.project_id, self.root, value.title, value.path, digest, content, now()))
        return {'document_id': identifier, 'source_path': value.path, 'source_sha256': digest, 'imported_snapshot': True}

    def export_artifact(self, value):
        artifact = self.artifact(value.artifact_id)
        if artifact['sha256'] != value.source_sha256:
            raise ValueError('Artifact hash does not match the inspected version.')
        data, media = artifact_bytes(artifact['content'], value.format)
        return {'filename': f'artifact-{value.artifact_id}.{value.format}', 'media_type': media,
                'content_base64': base64.b64encode(data).decode('ascii'),
                'sha256': hashlib.sha256(data).hexdigest(), 'artifact_id': value.artifact_id}

    def document(self, identifier):
        with self.records.connect() as db:
            row = db.execute("SELECT * FROM knowledge_documents WHERE document_id=? AND project_id=? AND root=?", (identifier, self.project_id, self.root)).fetchone()
        if row is None:
            raise KeyError(identifier)
        return dict(row)

    def forget_document(self, value):
        with self.records.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT source_sha FROM knowledge_documents WHERE document_id=? AND project_id=? AND root=?',
                             (value.document_id, self.project_id, self.root)).fetchone()
            if row is None:
                raise ValueError('Knowledge snapshot is unavailable in this project.')
            if row['source_sha'] != value.source_sha256:
                raise ValueError('Knowledge snapshot changed; inspect and approve its current hash.')
            execution_dispatch_authorized.get()()
            db.execute('DELETE FROM knowledge_documents WHERE document_id=? AND project_id=? AND root=? AND source_sha=?',
                       (value.document_id, self.project_id, self.root, value.source_sha256))
        return {'status': 'forgotten', 'document_id': value.document_id,
                'preserved': 'Original project file, artifact content/source-attribution metadata, audit records and existing backups remain.',
                'erasure': 'Removed from active knowledge retrieval; SQLite free pages and external copies are not securely erased.'}

    def search(self, query):
        # instr keeps percent/underscore literal; no query-language execution.
        with self.records.connect() as db:
            rows = db.execute("SELECT document_id,title,source_path,source_sha,content FROM knowledge_documents WHERE project_id=? AND root=? AND instr(lower(content),lower(?))>0 ORDER BY created_at DESC LIMIT 10", (self.project_id, self.root, query)).fetchall()
        matches = []
        for row in rows:
            item = dict(row)
            content = item.pop("content")
            index = content.lower().find(query.lower())
            start = max(0, index - 160)
            item.update({"excerpt": content[start:start + 800], "line": content[:start].count("\n") + 1, "snapshot": True})
            matches.append(item)
        return {"query": query, "matches": matches, "retrieval": "bounded literal text search; imported snapshots may be stale"}

    def artifacts(self):
        with self.records.connect() as db:
            return [dict(row) for row in db.execute("SELECT artifact_id,title,kind,version,previous_id,created_at FROM creation_artifacts WHERE project_id=? AND root=? ORDER BY created_at DESC LIMIT 100", (self.project_id, self.root))]

    def artifact(self, identifier):
        with self.records.connect() as db:
            row = db.execute("SELECT * FROM creation_artifacts WHERE artifact_id=? AND project_id=? AND root=?", (identifier, self.project_id, self.root)).fetchone()
        if row is None:
            raise KeyError(identifier)
        result = dict(row)
        result["sources"] = json.loads(result.pop("sources_json"))
        result["sha256"] = hashlib.sha256(result["content"].encode()).hexdigest()
        result["verification"] = "stored text; source attribution is recorded, factual correctness is not inferred"
        return result

    def history(self, identifier):
        """One bounded, consistent predecessor page; never follow another scope."""
        versions, visited, expected = [], set(), None
        with self.records.connect() as db:
            db.execute('BEGIN')
            for _ in range(20):
                if identifier is None:
                    break
                if identifier in visited:
                    raise ValueError('Artifact history contains a cycle.')
                visited.add(identifier)
                row = db.execute('SELECT artifact_id,title,kind,version,previous_id,created_at,content FROM creation_artifacts WHERE artifact_id=? AND project_id=? AND root=?',
                                 (identifier, self.project_id, self.root)).fetchone()
                if row is None:
                    raise ValueError('Artifact history is unavailable in this project.')
                item = dict(row)
                if type(item['version']) is not int or item['version'] < 1 or expected is not None and item['version'] != expected:
                    raise ValueError('Artifact history version sequence is inconsistent.')
                item['sha256'] = hashlib.sha256(item.pop('content').encode()).hexdigest()
                versions.append(item)
                expected, identifier = item['version'] - 1, item['previous_id']
                if identifier is None and expected != 0:
                    raise ValueError('Artifact history is incomplete.')
            if identifier in visited:
                raise ValueError('Artifact history contains a cycle.')
        return {'versions': versions, 'next_artifact_id': identifier, 'limit': 20,
                'verification': 'Stored version history and content hashes; no factual or execution verification inferred.'}

    def create_artifact(self, value):
        identifier = uuid.uuid4().hex
        with self.records.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            sources = []
            for source in sorted(set(value.sources)):
                row = db.execute("SELECT document_id,title,source_path,source_sha FROM knowledge_documents WHERE document_id=? AND project_id=? AND root=?", (source, self.project_id, self.root)).fetchone()
                if row is None:
                    raise ValueError("Artifact source is unavailable in this project.")
                sources.append(dict(row))
            version = 1
            if value.previous_id:
                previous = db.execute("SELECT version FROM creation_artifacts WHERE artifact_id=? AND project_id=? AND root=?", (value.previous_id, self.project_id, self.root)).fetchone()
                if previous is None or db.execute("SELECT 1 FROM creation_artifacts WHERE previous_id=?", (value.previous_id,)).fetchone():
                    raise ValueError("Previous artifact is unavailable or already superseded.")
                version = previous[0] + 1
            db.execute("INSERT INTO creation_artifacts VALUES (?,?,?,?,?,?,?,?,?,?)", (identifier, self.project_id, self.root, value.title, value.kind, value.content, json.dumps(sources), version, value.previous_id, now()))
        return self.artifact(identifier)


def register_knowledge_tools(registry, database, project_id):
    library = KnowledgeLibrary(registry.workspace_root, database, project_id)

    def root(_):
        return library.files.guard.resolve(".")

    async def importing(value):
        return library.import_text(value)

    async def importing_docx(value):
        return library.import_docx(value)

    async def exporting(value):
        return library.export_artifact(value)

    async def searching(value):
        return library.search(value.query)

    async def documents(_):
        return {"documents": library.documents()}

    async def document(value):
        return library.document(value.document_id)

    async def artifacts(_):
        return {"artifacts": library.artifacts()}

    async def document_page(value):
        return library.page('documents', value.cursor, value.query)

    async def artifact_page(value):
        return library.page('artifacts', value.cursor, value.query)

    async def artifact(value):
        return library.artifact(value.artifact_id)

    async def create(value):
        return library.create_artifact(value)

    async def forgetting(value):
        return library.forget_document(value)

    async def history(value):
        return library.history(value.artifact_id)

    registry.register(Tool("knowledge_import", "Import an exact inspected text snapshot into project knowledge.", ImportText, RiskLevel.HIGH, 10, importing, resolve_destination=lambda args: library.files.guard.resolve(args["path"]), category=ActionCategory.EDIT))
    registry.register(Tool("knowledge_import_docx", "Import bounded text from an exact approved DOCX snapshot without executing its resources.", ImportText, RiskLevel.HIGH, 10, importing_docx, resolve_destination=lambda args: library.files.guard.resolve(args["path"]), category=ActionCategory.EDIT))
    for name, description, model, handler in [
        ("knowledge_search", "Search bounded project knowledge snapshots with source references.", KnowledgeQuery, searching),
        ("knowledge_documents", "List project knowledge source metadata.", EmptyInput, documents),
        ('knowledge_document_page', 'Browse bounded project source metadata pages with scope-bound cursors.', LibraryPage, document_page),
        ("knowledge_document", "Read one project knowledge snapshot.", DocumentId, document),
        ("library_artifacts", "List project artifact versions.", EmptyInput, artifacts),
        ('library_artifact_page', 'Browse bounded project artifact metadata pages with scope-bound cursors.', LibraryPage, artifact_page),
        ("library_artifact", "Read one project artifact and its source references.", ArtifactId, artifact),
        ("library_history", "Inspect a bounded page of earlier artifact versions and exact content hashes.", ArtifactId, history),
        ("library_export", "Export an inspected artifact version as text, Markdown or macro-free DOCX bytes.", ExportArtifact, exporting),
    ]:
        registry.register(Tool(name, description, model, RiskLevel.LOW, 10, handler, resolve_destination=root, category=ActionCategory.READ))
    registry.register(Tool("library_create", "Store a versioned project text artifact with explicit sources.", ArtifactInput, RiskLevel.HIGH, 10, create, resolve_destination=root, category=ActionCategory.EDIT))
    registry.register(Tool('knowledge_forget', 'Forget one exact approved imported snapshot from active project knowledge; original files and existing copies remain.',
                           ForgetDocument, RiskLevel.HIGH, 10, forgetting, resolve_destination=root, category=ActionCategory.DESTRUCTIVE,
                           preview_details=lambda args: {'document_id': args['document_id'], 'source_sha256': args['source_sha256'],
                               'removal_effect': 'Remove the imported snapshot from active retrieval only.',
                               'preserved': 'Original file, artifact content/attribution, audit records, SQLite free pages and backups.'}))
