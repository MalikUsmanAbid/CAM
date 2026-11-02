from pathlib import Path

import pytest

from services.agent_api.app.persistence import Phase5Store


def test_workflow_persists_and_transitions_with_event(tmp_path: Path):
    store = Phase5Store(tmp_path / "db.sqlite")
    store.initialize()
    workflow = store.create_workflow("client_research", "idempotent-1")
    assert store.create_workflow("client_research", "idempotent-1")["id"] == workflow["id"]
    assert store.transition(workflow["id"], "running")["status"] == "running"
    assert (
        store.transition(workflow["id"], "waiting_for_approval")["status"] == "waiting_for_approval"
    )
    with pytest.raises(ValueError, match="transition"):
        store.transition(workflow["id"], "completed")


def test_schedule_lead_document_and_event_are_bounded(tmp_path: Path):
    store = Phase5Store(tmp_path / "db.sqlite")
    store.initialize()
    schedule = store.create_schedule("task-1", "weekly", "2026-01-01T00:00:00+00:00", 2)
    assert schedule
    assert store.list_schedules()[0]["id"] == schedule
    store.cancel_schedule(schedule)
    assert store.list_schedules()[0]["enabled"] == 0
    with pytest.raises(ValueError):
        store.create_schedule("task-1", "weekly", "not-a-time", 2)
    assert store.create_lead("Example Business", "https://example.com", "public")
    assert store.create_document("proposal", "Draft")
    with pytest.raises(ValueError):
        store.create_event("task_failed", "password=secret")
