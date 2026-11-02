"""Deferred question regressions; temporary SQLite fixtures, no task dispatch."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.main import app
from services.agent_api.app.migrations import initialize_database
from services.agent_api.app.workspace_records import WorkspaceRecords


def fixture(tmp_path):
    database = tmp_path / 'questions.sqlite'
    initialize_database(database)
    records = WorkspaceRecords(database)
    identifier = records.ask('A', 'Which draft should be used?')
    return records, identifier, records.decisions('A')[0]['question_sha256']


def test_dismiss_preserves_history_and_refuses_stale_scope_or_late_answers(tmp_path):
    records, identifier, digest = fixture(tmp_path)
    for scope, observed in [('B', digest), ('A', '0' * 64)]:
        with pytest.raises(ValueError, match='Question changed'):
            records.dismiss_decision(scope, identifier, observed)
    assert records.decisions('A')[0]['status'] == 'pending'
    result = records.dismiss_decision('A', identifier, digest)
    assert result['status'] == 'dismissed'
    row = records.decisions('A')[0]
    assert row['question'] == 'Which draft should be used?' and row['answer'] is None
    assert row['answered_at'] is not None
    with pytest.raises(ValueError, match='closed'):
        records.answer('A', identifier, 'A late answer')
    with pytest.raises(ValueError, match='Question changed'):
        records.dismiss_decision('A', identifier, digest)


def test_synchronized_answer_and_dismiss_cannot_overwrite_each_other(tmp_path):
    records, identifier, digest = fixture(tmp_path)
    barrier = Barrier(2)
    def complete(answer):
        barrier.wait(timeout=5)
        try:
            if answer:
                records.answer('A', identifier, 'Reviewed answer')
            else:
                records.dismiss_decision('A', identifier, digest)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as workers:
        answer, dismiss = workers.submit(complete, True), workers.submit(complete, False)
        won = [answer.result(timeout=10), dismiss.result(timeout=10)]
    assert sum(won) == 1
    final = records.decisions('A')[0]
    assert final['status'] == ('answered' if won[0] else 'dismissed')
    assert final['answer'] == ('Reviewed answer' if won[0] else None)


def test_api_dismisses_observed_question_without_creating_a_task():
    with TestClient(app) as client:
        with app.state.workspace_records.connect() as db:
            before = db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        path = '/api/v1/workspace/decisions'
        created = client.post(path, json={'question': 'Keep this draft?'}).json()
        item = next(row for row in client.get(path).json() if row['decision_id'] == created['decision_id'])
        dismissed = client.post(f"{path}/{item['decision_id']}/dismiss", json={'expected_question_sha256': item['question_sha256']})
        assert dismissed.status_code == 200
        assert dismissed.json()['execution'].startswith('No task resumed or cancelled')
        assert next(row for row in client.get(path).json() if row['decision_id'] == item['decision_id'])['status'] == 'dismissed'
        with app.state.workspace_records.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == before
