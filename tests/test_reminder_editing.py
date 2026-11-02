"""Deferred reminder lifecycle source; temporary SQLite only, no execution jobs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.main import app
from services.agent_api.app.migrations import initialize_database
from services.agent_api.app.workspace_records import (
    RescheduleInput,
    ScheduleInput,
    WorkspaceRecords,
)


def fixture(tmp_path, interval=None):
    database = tmp_path / 'reminders.sqlite'
    initialize_database(database)
    records = WorkspaceRecords(database)
    due = datetime(2030, 1, 1, tzinfo=UTC)
    identifier = records.schedule('project-A', ScheduleInput(title='Review progress', due_at=due, interval_seconds=interval))
    return records, identifier, due


def test_reschedule_changes_only_timing_and_emits_one_new_occurrence(tmp_path):
    records, identifier, due = fixture(tmp_path)
    original = records.schedules('project-A')[0]
    replacement = due + timedelta(days=1)
    updated = records.reschedule('project-A', identifier, RescheduleInput(expected_contract_sha256=original['contract_sha256'], due_at=replacement))
    for key in ('schedule_id', 'project_id', 'title', 'kind', 'payload_json', 'created_at'):
        assert updated[key] == original[key]
    assert updated['contract_sha256'] != original['contract_sha256']
    assert records.materialize_due(due + timedelta(seconds=1)) == []
    assert len(records.materialize_due(replacement + timedelta(seconds=1))) == 1
    assert records.materialize_due(replacement + timedelta(seconds=2)) == []
    assert records.occurrences('project-A')[0]['due_at'] == replacement.isoformat()


def test_scheduler_tick_invalidates_stale_edit_and_cancellation(tmp_path):
    records, identifier, due = fixture(tmp_path, interval=60)
    old = records.schedules('project-A')[0]['contract_sha256']
    records.materialize_due(due + timedelta(seconds=1))
    with pytest.raises(ValueError, match='changed'):
        records.reschedule('project-A', identifier, RescheduleInput(expected_contract_sha256=old, due_at=due + timedelta(days=1)))
    with pytest.raises(ValueError, match='changed'):
        records.cancel_schedule('project-A', identifier, old)
    current = records.schedules('project-A')[0]
    assert records.occurrences('project-A')[0]['status'] == 'pending'
    records.cancel_schedule('project-A', identifier, current['contract_sha256'])
    assert records.occurrences('project-A')[0]['status'] == 'cancelled'
    assert records.materialize_due(due + timedelta(days=1)) == []


def test_reschedule_cannot_replay_history_or_cross_scope(tmp_path):
    records, identifier, due = fixture(tmp_path, interval=60)
    records.materialize_due(due + timedelta(seconds=1))
    current = records.schedules('project-A')[0]['contract_sha256']
    value = RescheduleInput(expected_contract_sha256=current, due_at=due)
    with pytest.raises(ValueError, match='history'):
        records.reschedule('project-A', identifier, value)
    with pytest.raises(KeyError):
        records.reschedule('project-B', identifier, value)
    with pytest.raises(ValueError, match='timezone'):
        RescheduleInput(expected_contract_sha256=current, due_at=datetime(2030, 1, 2))
    assert len(records.occurrences('project-A')) == 1


def test_synchronized_competing_reschedules_have_exactly_one_winner(tmp_path):
    records, identifier, due = fixture(tmp_path)
    contract = records.schedules('project-A')[0]['contract_sha256']
    ready = Barrier(2)
    def update(days):
        ready.wait(timeout=5)
        try:
            return records.reschedule('project-A', identifier, RescheduleInput(expected_contract_sha256=contract, due_at=due + timedelta(days=days)))
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as workers:
        first, second = workers.submit(update, 1), workers.submit(update, 2)
        results = [first.result(timeout=10), second.result(timeout=10)]
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert records.schedules('project-A')[0]['contract_sha256'] == winners[0]['contract_sha256']
    assert records.occurrences('project-A') == []


def test_api_edit_and_exact_cancel_use_personal_scope_without_executing_tasks():
    with TestClient(app) as client:
        endpoint = '/api/v1/workspace/reminders'
        created = client.post(endpoint, json={'title': 'Fixture reminder', 'due_at': '2030-01-01T00:00:00Z'}).json()
        identifier = created['schedule_id']
        before = next(row for row in client.get(endpoint).json() if row['schedule_id'] == identifier)
        changed = client.put(f'{endpoint}/{identifier}', json={'expected_contract_sha256': before['contract_sha256'],
            'due_at': '2030-01-02T10:00:00+05:00', 'interval_seconds': 60})
        assert changed.status_code == 200
        after = changed.json()
        assert after['next_due'] == '2030-01-02T05:00:00+00:00'
        stale = client.post(f'{endpoint}/{identifier}/cancel', json={'expected_contract_sha256': before['contract_sha256']})
        assert stale.status_code >= 400
        cancelled = client.post(f'{endpoint}/{identifier}/cancel', json={'expected_contract_sha256': after['contract_sha256']})
        assert cancelled.status_code == 200
        assert next(row for row in client.get(endpoint).json() if row['schedule_id'] == identifier)['status'] == 'cancelled'
        assert app.state.workspace_records.occurrences('') == []
