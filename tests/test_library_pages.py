"""Deferred source: temporary library metadata, no user documents or startup."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.knowledge_library import KnowledgeLibrary
from services.agent_api.app.main import app
from services.agent_api.app.migrations import initialize_database


def fixture(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    database = tmp_path / "library-pages.sqlite"
    initialize_database(database)
    library = KnowledgeLibrary(root, database, "A")
    stamp = "2030-01-01T00:00:00+00:00"
    with library.records.connect() as db:
        for index in range(121):
            content = "Needle%_ source" if index in {0, 60} else "Stored source body"
            db.execute(
                "INSERT INTO knowledge_documents VALUES (?,?,?,?,?,?,?,?)",
                (
                    f"{index:032x}",
                    "A",
                    library.root,
                    f"Source {index}",
                    f"source-{index}.txt",
                    hashlib.sha256(content.encode()).hexdigest(),
                    content,
                    stamp,
                ),
            )
            db.execute(
                "INSERT INTO creation_artifacts VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    f"{index + 2000:032x}",
                    "A",
                    library.root,
                    f"Artifact {index}",
                    "document",
                    "Stored artifact body",
                    "[]",
                    1,
                    None,
                    stamp,
                ),
            )
    return library, root, database


@pytest.mark.parametrize(
    "collection,identifier", [("documents", "document_id"), ("artifacts", "artifact_id")]
)
def test_all_entries_beyond_old_hundred_limit_are_reachable_with_tied_timestamps(
    tmp_path, collection, identifier
):
    library, _, _ = fixture(tmp_path)
    cursor, identifiers, sizes = None, [], []
    while True:
        page = library.page(collection, cursor)
        sizes.append(len(page[collection]))
        identifiers.extend(item[identifier] for item in page[collection])
        assert all(
            "content" not in item and "sources_json" not in item for item in page[collection]
        )
        cursor = page["next_cursor"]
        if cursor is None:
            break
        assert len(sizes) < 4
    assert sizes == [50, 50, 21]
    assert len(set(identifiers)) == 121
    assert identifiers == sorted(identifiers, reverse=True)


def test_cursor_binds_project_root_collection_and_query(tmp_path):
    library, root, database = fixture(tmp_path)
    cursor = library.page("documents")["next_cursor"]
    other_root = tmp_path / "other-root"
    other_root.mkdir()
    for changed, collection, query in [
        (KnowledgeLibrary(root, database, "B"), "documents", ""),
        (KnowledgeLibrary(other_root, database, "A"), "documents", ""),
        (library, "artifacts", ""),
        (library, "documents", "Stored"),
    ]:
        with pytest.raises(ValueError, match="changed"):
            changed.page(collection, cursor, query)
    result = library.page("documents", query="%_")
    assert {item["document_id"] for item in result["documents"]} == {f"{0:032x}", f"{60:032x}"}
    assert result["next_cursor"] is None


@pytest.mark.parametrize("cursor", ["not valid base64!", "a" * 1001, {"unexpected": "object"}])
def test_invalid_cursor_refuses_without_mutating_library(tmp_path, cursor):
    library, _, _ = fixture(tmp_path)
    with pytest.raises(ValueError, match="cursor"):
        library.page("documents", cursor)
    with library.records.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM knowledge_documents").fetchone()[0] == 121


def test_api_page_read_honors_current_read_approval_policy(tmp_path):
    root = tmp_path / "api-project"
    root.mkdir()
    with TestClient(app) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "Fixture", "root": str(root)}
        ).json()["project_id"]
        assert (
            client.put(
                "/api/v1/policy", json={"blocked_categories": [], "require_read_approval": True}
            ).status_code
            == 200
        )
        response = client.post(
            f"/api/v1/workspace/knowledge/execute?project_id={project}",
            json={"tool": "knowledge_document_page", "arguments": {}},
        )
        assert response.status_code == 200 and response.json()["status"] == "approval_required"
        grant = response.json()["approval"]["request_id"]
        assert (
            client.post(f"/api/v1/approvals/{grant}/approve?project_id={project}").status_code
            == 200
        )
        result = client.post(f"/api/v1/tools/execute/{grant}/continue?project_id={project}").json()
        assert result["status"] == "success"
        assert result["result"]["documents"] == [] and result["result"]["limit"] == 50
