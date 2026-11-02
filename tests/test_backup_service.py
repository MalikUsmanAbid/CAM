"""Temporary persistence/ephemeral-secret regressions; verification deferred."""

import asyncio
import json
import sqlite3
import threading
from contextlib import closing

import pytest

from services.agent_api.app.backup_service import (
    TTL,
    BackupCapture,
    BackupService,
    register_backup_tool,
)
from services.agent_api.app.session_security import execution_session


def test_pending_backup_secret_is_owned_expires_and_has_no_persistence(tmp_path):
    database = tmp_path / 'absent.sqlite'
    service = BackupService(database, available=True)
    staged = service.stage('fixture-exclusive-recovery-passphrase')
    assert not database.exists()
    token = execution_session.set('other-actor')
    try:
        assert service.list_pending() == []
        service.discard(staged['capture_id'])
        assert staged['capture_id'] in service.pending
    finally:
        execution_session.reset(token)
    with service.lock:
        service.pending[staged['capture_id']]['created'] -= TTL + 1
    service.purge()
    assert service.pending == {} and not database.exists()


@pytest.mark.anyio
async def test_backup_export_requires_approval_excludes_secret_and_restores_only_a_new_fixture(tmp_path):
    pytest.importorskip('cryptography', reason='Optional recovery encryption is required for real round-trip verification.')
    from services.agent_api.app.backup_bundle import restore_bundle
    from services.agent_api.app.memory import MemoryRepository
    from services.agent_api.app.migrations import initialize_database
    from services.agent_api.app.tools import ToolExecutor, ToolRegistry
    from services.agent_api.app.workspace_records import RecordInput, WorkspaceRecords

    database = tmp_path / 'temporary.sqlite'
    initialize_database(database)
    WorkspaceRecords(database).save_record('A', RecordInput(title='Preserved fixture record', kind='lead'))
    root = tmp_path / 'workspace'
    root.mkdir()
    service = BackupService(database, available=True)
    password = 'fixture-exclusive-recovery-passphrase'
    capture = service.stage(password)
    registry = ToolRegistry(root)
    register_backup_tool(registry, service)
    memory = MemoryRepository(database)
    executor = ToolExecutor(registry, memory)
    proposal = await executor.execute('backup_export', capture)
    assert proposal['status'] == 'approval_required' and service.list_ready() == []
    assert password not in json.dumps(proposal) and password.encode() not in database.read_bytes()
    grant = proposal['approval']['request_id']
    memory.decide_approval(grant, True)
    result = await executor.continue_approved(grant)
    assert result['status'] == 'success'
    assert password not in json.dumps(result) and password.encode() not in database.read_bytes()
    identifier = result['result']['download_id']
    payload = service.download(identifier)
    copied = tmp_path / 'encrypted-backup.jarvis'
    copied.write_bytes(payload)
    token = execution_session.set('other-actor')
    try:
        with pytest.raises(ValueError, match='actor'):
            service.download(identifier)
    finally:
        execution_session.reset(token)
    restored = tmp_path / 'new-restored-data'
    restore_bundle(copied, restored, passphrase=password)
    with closing(sqlite3.connect(restored / 'jarvis.db')) as db:
        assert db.execute('SELECT title FROM work_records').fetchone()[0] == 'Preserved fixture record'
    service.discard(identifier)
    assert service.list_ready() == [] and service.pending == {}


@pytest.mark.anyio
async def test_cancellation_joins_backup_worker_before_releasing_temporary_ownership(tmp_path):
    entered = threading.Event()
    finished = threading.Event()
    def creator(database, destination, *, passphrase, authorize):
        entered.set()
        try:
            # Keep cooperating until cancellation, never write a plaintext result.
            while True:
                authorize()
                threading.Event().wait(0.01)
        finally:
            finished.set()
    service = BackupService(tmp_path / 'absent.sqlite', creator=creator, available=True)
    value = BackupCapture(**service.stage('fixture-exclusive-recovery-passphrase'))
    task = asyncio.create_task(service.export(value))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.inflight == 0 and service.ready == {} and service.pending == {}
        assert finished.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_plaintext_creator_output_is_never_retained_as_an_encrypted_download(tmp_path):
    def creator(database, destination, **kwargs):
        destination.write_bytes(b'Unencrypted fixture content')
    service = BackupService(tmp_path / 'absent.sqlite', creator=creator, available=True)
    value = BackupCapture(**service.stage('fixture-exclusive-recovery-passphrase'))
    with pytest.raises(ValueError, match='encrypted envelope'):
        await service.export(value)
    assert service.ready == {} and service.pending == {} and service.inflight == 0
