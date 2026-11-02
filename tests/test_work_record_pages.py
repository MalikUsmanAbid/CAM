"""Deferred work-record regressions; temporary SQLite and fixture records only."""
import pytest
from fastapi.testclient import TestClient

from services.agent_api.app.main import app
from services.agent_api.app.migrations import initialize_database
from services.agent_api.app.workspace_records import RecordInput, WorkspaceRecords


def fixture(tmp_path):
    database = tmp_path / 'work.sqlite'
    initialize_database(database)
    return WorkspaceRecords(database)


def test_keyset_pages_include_tied_timestamps_without_duplicates(tmp_path):
    records = fixture(tmp_path)
    identifiers = [records.save_record('A', RecordInput(title=f'Lead {i}', kind='lead'))['record_id'] for i in range(65)]
    with records.connect() as db:
        db.execute('UPDATE work_records SET updated_at=?', ('2030-01-01T00:00:00+00:00',))
    first = records.record_page('A')
    second = records.record_page('A', cursor=first['next_cursor'])
    assert len(first['items']) == 50 and len(second['items']) == 15
    assert [item['record_id'] for item in first['items'] + second['items']] == sorted(identifiers, reverse=True)
    assert second['next_cursor'] is None
    for changed in ({'kind': 'proposal'}, {'status': 'active'}, {'query': 'Lead'}):
        with pytest.raises(ValueError, match='filters changed'):
            records.record_page('A', cursor=first['next_cursor'], **changed)
    with pytest.raises(ValueError, match='scope or filters changed'):
        records.record_page('B', cursor=first['next_cursor'])


def test_filters_are_literal_and_scope_bound(tmp_path):
    records = fixture(tmp_path)
    expected = records.save_record('A', RecordInput(title='100%_ fixture', content='Bounded note', kind='proposal', status='active'))
    records.save_record('A', RecordInput(title='Unrelated', kind='lead'))
    records.save_record('B', RecordInput(title='100%_ other project', kind='proposal', status='active'))
    result = records.record_page('A', kind='proposal', status='active', query='%_')
    assert [item['record_id'] for item in result['items']] == [expected['record_id']]
    assert records.record_page('A', query="' OR 1=1 --")['items'] == []
    for token in ('not base64!', 'a' * 1001):
        with pytest.raises(ValueError, match='cursor'):
            records.record_page('A', cursor=token)


def test_archiving_and_editing_preserve_newer_revision(tmp_path):
    records = fixture(tmp_path)
    created = records.save_record('A', RecordInput(title='Proposal', kind='proposal'))
    archived = records.save_record('A', RecordInput(title='Reviewed proposal', content='Newer content', kind='proposal', status='archived'), created['record_id'], 1)
    with pytest.raises(ValueError, match='changed'):
        records.save_record('A', RecordInput(title='Old tab', kind='proposal'), created['record_id'], 1)
    assert records.record_page('A', status='archived')['items'] == [archived]
    with pytest.raises(ValueError, match='outside this scope'):
        records.save_record('B', RecordInput(title='Wrong project', kind='proposal'), created['record_id'], 2)


def test_api_filtered_page_and_revision_bound_edit():
    with TestClient(app) as client:
        endpoint = '/api/v1/workspace/records'
        created = client.post(endpoint, json={'title': 'Fixture proposal', 'kind': 'proposal'}).json()
        result = client.get(endpoint + '/page', params={'kind': 'proposal', 'query': 'Fixture'}).json()
        assert [item['record_id'] for item in result['items']] == [created['record_id']]
        updated = client.put(endpoint + '/' + created['record_id'], json={
            'title': 'Edited fixture', 'kind': 'proposal', 'content': 'Reviewed', 'status': 'archived', 'expected_revision': 1})
        assert updated.status_code == 200 and updated.json()['revision'] == 2
        archived = client.get(endpoint + '/page', params={'status': 'archived'}).json()
        assert archived['items'][0]['title'] == 'Edited fixture'
        assert client.get(endpoint + '/page', params={'cursor': 'invalid token!'}).status_code >= 400
