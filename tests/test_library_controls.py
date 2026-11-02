"""Deferred isolated regression source; no real database/workspace paths."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.knowledge_library import (
    ArtifactInput,
    ForgetDocument,
    ImportText,
    KnowledgeLibrary,
)
from services.agent_api.app.main import app
from services.agent_api.app.migrations import initialize_database
from services.agent_api.app.session_security import execution_dispatch_authorized


def library_fixture(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    database = tmp_path / "library.sqlite"
    initialize_database(database)
    library = KnowledgeLibrary(root, database, "project-A")
    content = "A saved source for retrieval"
    (root / "source.txt").write_text(content, encoding="utf-8")
    digest = hashlib.sha256(content.encode()).hexdigest()
    source = library.import_text(
        ImportText(path="source.txt", title="Source", source_sha256=digest)
    )
    return library, root, database, source, digest


def test_forget_removes_retrieval_preserves_original_and_artifact_attribution(tmp_path):
    library, root, _, source, digest = library_fixture(tmp_path)
    artifact = library.create_artifact(
        ArtifactInput(title="Draft", content="Retained draft", sources=[source["document_id"]])
    )
    result = library.forget_document(
        ForgetDocument(document_id=source["document_id"], source_sha256=digest, confirm_forget=True)
    )
    assert result["status"] == "forgotten"
    assert library.search("saved source")["matches"] == []
    assert (root / "source.txt").read_text() == "A saved source for retrieval"
    assert library.artifact(artifact["artifact_id"])["sources"][0]["source_sha"] == digest
    assert library.artifact(artifact["artifact_id"])["content"] == "Retained draft"
    with pytest.raises(ValueError, match="unavailable"):
        library.forget_document(
            ForgetDocument(
                document_id=source["document_id"], source_sha256=digest, confirm_forget=True
            )
        )


def test_forget_rejects_wrong_hash_scope_and_revoked_dispatch(tmp_path):
    library, root, database, source, digest = library_fixture(tmp_path)
    exact = ForgetDocument(
        document_id=source["document_id"], source_sha256=digest, confirm_forget=True
    )
    with pytest.raises(ValueError, match="changed"):
        library.forget_document(exact.model_copy(update={"source_sha256": "0" * 64}))
    with pytest.raises(ValueError, match="unavailable"):
        KnowledgeLibrary(root, database, "project-B").forget_document(exact)
    other = tmp_path / "other-root"
    other.mkdir()
    with pytest.raises(ValueError, match="unavailable"):
        KnowledgeLibrary(other, database, "project-A").forget_document(exact)

    def revoked():
        raise ValueError("dispatch revoked")

    token = execution_dispatch_authorized.set(revoked)
    try:
        with pytest.raises(ValueError, match="revoked"):
            library.forget_document(exact)
    finally:
        execution_dispatch_authorized.reset(token)
    assert library.document(source["document_id"])["source_sha"] == digest


def test_history_has_bounded_predecessor_pages_and_scope_checks(tmp_path):
    library, root, database, _, _ = library_fixture(tmp_path)
    previous = None
    for version in range(1, 24):
        previous = library.create_artifact(
            ArtifactInput(
                title="Draft",
                content=f"Version {version}",
                previous_id=previous["artifact_id"] if previous else None,
            )
        )
    first = library.history(previous["artifact_id"])
    assert [item["version"] for item in first["versions"]] == list(range(23, 3, -1))
    assert all("content" not in item and len(item["sha256"]) == 64 for item in first["versions"])
    second = library.history(first["next_artifact_id"])
    assert [item["version"] for item in second["versions"]] == [3, 2, 1]
    assert second["next_artifact_id"] is None
    with pytest.raises(ValueError, match="unavailable"):
        KnowledgeLibrary(root, database, "project-B").history(previous["artifact_id"])
    with library.records.connect() as db:
        db.execute(
            "UPDATE creation_artifacts SET previous_id=? WHERE artifact_id=?",
            (previous["artifact_id"], previous["artifact_id"]),
        )
    with pytest.raises(ValueError, match="cycle"):
        library.history(previous["artifact_id"])


def test_forget_api_requires_exact_approval_and_generic_continuation(tmp_path):
    root = tmp_path / "api-project"
    root.mkdir()
    content = "Original project file"
    (root / "source.txt").write_text(content)
    digest = hashlib.sha256(content.encode()).hexdigest()
    with TestClient(app) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "Fixture", "root": str(root)}
        ).json()["project_id"]
        library = KnowledgeLibrary(root, app.state.settings.database_path, project)
        source = library.import_text(
            ImportText(path="source.txt", title="Source", source_sha256=digest)
        )
        response = client.post(
            f"/api/v1/workspace/knowledge/execute?project_id={project}",
            json={
                "tool": "knowledge_forget",
                "arguments": {
                    "document_id": source["document_id"],
                    "source_sha256": digest,
                    "confirm_forget": True,
                },
            },
        )
        assert response.status_code == 200
        pending = response.json()
        assert pending["status"] == "approval_required"
        assert library.document(source["document_id"])["source_sha"] == digest
        identifier = pending["approval"]["request_id"]
        assert (
            client.post(f"/api/v1/approvals/{identifier}/approve?project_id={project}").status_code
            == 200
        )
        completed = client.post(
            f"/api/v1/tools/execute/{identifier}/continue?project_id={project}"
        ).json()
        assert completed["status"] == "success"
        assert library.documents() == []
        assert (root / "source.txt").read_text() == content
