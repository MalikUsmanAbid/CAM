"""Approved encrypted database exports with ephemeral passphrases/downloads.

No user database migration, restore, cloud transmission or automatic download.
Only opaque capture IDs enter approvals; secrets never enter SQLite/audit.
"""

import asyncio
import hashlib
import importlib.util
import json
import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import Field

from services.agent_api.app.action_policy import ActionCategory
from services.agent_api.app.backup_bundle import create_bundle
from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.portable_backup import HEADER, bounded_bundle_bytes, decrypt_payload
from services.agent_api.app.project_runtime import project_scope
from services.agent_api.app.session_security import (
    execution_authorized,
    execution_dispatch_authorized,
    execution_session,
)
from services.agent_api.app.tools import Tool, ToolInput

TTL = 300


class BackupCapture(ToolInput):
    capture_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class BackupService:
    def __init__(self, database, *, creator=create_bundle, available=None):
        self.database = Path(database).resolve()
        self.creator = creator
        self.available = importlib.util.find_spec('cryptography') is not None if available is None else available
        self.pending, self.ready = {}, {}
        self.lock = threading.Lock()
        self.gate = asyncio.Semaphore(1)
        self.inflight = 0

    def purge(self):
        with self.lock:
            for collection in (self.pending, self.ready):
                for identifier in [key for key, row in collection.items() if time.monotonic() - row['created'] >= TTL]:
                    collection.pop(identifier, None)

    def clear(self):
        with self.lock:
            self.pending.clear()
            self.ready.clear()

    def stage(self, password):
        if not self.available or not isinstance(password, str) or not 16 <= len(password.encode()) <= 1024:
            raise ValueError('Portable backup is unavailable or passphrase length is invalid.')
        self.purge()
        identifier = uuid.uuid4().hex
        with self.lock:
            if len(self.pending) + len(self.ready) + self.inflight >= 2:
                raise ValueError('Finish or discard existing backup exports first.')
            self.pending[identifier] = {'created': time.monotonic(), 'owner': execution_session.get(), 'password': password}
        return {'capture_id': identifier}

    def discard(self, identifier):
        with self.lock:
            for collection in (self.pending, self.ready):
                row = collection.get(identifier)
                if row and row['owner'] == execution_session.get():
                    collection.pop(identifier, None)

    def list_ready(self):
        self.purge()
        with self.lock:
            return [{'download_id': key, 'sha256': row['sha256'], 'bytes': len(row['payload']),
                'expires_in_seconds': max(0, int(TTL - (time.monotonic() - row['created'])))}
                for key, row in self.ready.items() if row['owner'] == execution_session.get()]

    def list_pending(self):
        self.purge()
        with self.lock:
            return [{'capture_id': key, 'expires_in_seconds': max(0, int(TTL - (time.monotonic() - row['created'])))}
                for key, row in self.pending.items() if row['owner'] == execution_session.get()]

    def download(self, identifier):
        self.purge()
        with self.lock:
            row = self.ready.get(identifier)
            if row is None or row['owner'] != execution_session.get():
                raise ValueError('Encrypted export expired or belongs to another actor.')
            return row['payload']

    async def export(self, value):
        authorize = execution_dispatch_authorized.get()
        async with self.gate:
            authorize()
            self.purge()
            with self.lock:
                row = self.pending.get(value.capture_id)
                if row is None or row['owner'] != execution_session.get():
                    raise ValueError('Backup secret expired or belongs to another actor; prepare again.')
                row = self.pending.pop(value.capture_id)
                self.inflight += 1
            cancelled = threading.Event()
            deadline = time.monotonic() + 20
            def check():
                if cancelled.is_set() or time.monotonic() >= deadline:
                    raise ValueError('Backup export was cancelled or exceeded its deadline.')
                authorize()
            def generate():
                # Both plaintext temporary snapshots and ciphertext output are
                # scoped to newly allocated private temporary directories.
                with tempfile.TemporaryDirectory(prefix='jarvis-encrypted-export-') as temporary:
                    destination = Path(temporary) / 'backup.encrypted'
                    self.creator(self.database, destination, passphrase=row['password'], authorize=check)
                    check()
                    payload = bounded_bundle_bytes(destination)
                    if not payload.startswith(HEADER):
                        raise ValueError('Backup creator did not produce a portable encrypted envelope.')
                    plaintext = decrypt_payload(payload, row['password'])
                    del plaintext
                    check()
                    return payload
            worker = asyncio.create_task(asyncio.to_thread(generate))
            try:
                payload = await asyncio.shield(worker)
                check()
            except BaseException:
                cancelled.set()
                # Wait for cooperative SQLite cancellation before releasing
                # temporary-directory ownership or the one-export resource slot.
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                await asyncio.gather(worker, return_exceptions=True)
                raise
            finally:
                row.pop('password', None)  # Best effort; Python does not guarantee zeroization.
                with self.lock:
                    self.inflight -= 1
            identifier = uuid.uuid4().hex
            digest = hashlib.sha256(payload).hexdigest()
            with self.lock:
                if len(self.pending) + len(self.ready) >= 2:
                    raise ValueError('Encrypted export capacity changed; no download was retained.')
                self.ready[identifier] = {'created': time.monotonic(), 'owner': execution_session.get(), 'payload': payload, 'sha256': digest}
            return {'download_id': identifier, 'sha256': digest, 'bytes': len(payload), 'encryption': 'portable_scrypt_aes256gcm',
                'expires_in_seconds': TTL, 'credentials_included': False, 'working_files_included': False,
                'restore': 'Separate explicit maintenance into an absent local data directory; no active database is replaced.'}


def register_backup_tool(registry, service):
    registry.register(Tool('backup_export', 'Create an encrypted consistent database export using an ephemeral passphrase; no cloud transmission or restore.',
        BackupCapture, RiskLevel.HIGH, 25, service.export, enabled=service.available,
        resolve_destination=lambda _: service.database.resolve(), category=ActionCategory.READ,
        preview_details=lambda _: {'includes': 'Database records and scoped knowledge snapshots',
            'excludes': 'Protected credentials, project working files and model weights', 'download': 'Ciphertext only; session-bound and expires in five minutes.'}))


router = APIRouter(prefix='/api/v1/backups', tags=['backups'])


def allowed(request):
    if request.query_params.get('project_id'):
        raise HTTPException(400, 'Database backup exports require Personal scope.')
    if not execution_authorized.get()() or request.app.state.executor.policy.denied(ActionCategory.READ):
        raise HTTPException(403, 'Current session or policy blocks database exports.')


@router.get('/status')
async def status(request: Request):
    allowed(request)
    service = request.app.state.backup_service
    return {'available': service.available, 'ready': service.list_ready(), 'pending': service.list_pending(), 'secrets': 'Ephemeral memory only; no guaranteed memory zeroization.',
            'scope': 'Database records; working files, credentials and models excluded. Cloud/restore use separate maintenance actions.'}


@router.post('/prepare')
async def prepare(request: Request):
    allowed(request)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 4096:
            raise HTTPException(413, 'Backup preparation exceeds its bound.')
        body.extend(chunk)
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate input field.')
                result[key] = value
            return result
        value = json.loads(body, object_pairs_hook=unique)
        if not isinstance(value, dict) or set(value) != {'passphrase'}:
            raise ValueError('Invalid preparation contract.')
        capture = request.app.state.backup_service.stage(value['passphrase'])
    except (ValueError, TypeError, UnicodeError, RecursionError):
        # Never echo a secret through Pydantic's input/error representation.
        raise HTTPException(400, 'Backup preparation failed. Check optional recovery support, passphrase length and pending exports.') from None
    with project_scope(None):
        result = await request.app.state.executor.execute('backup_export', capture)
    if result['status'] not in {'approval_required', 'success'}:
        request.app.state.backup_service.discard(capture['capture_id'])
    return {**result, 'capture_id': capture['capture_id']}


@router.get('/download/{identifier}')
async def download(identifier: str, request: Request):
    allowed(request)
    try:
        payload = request.app.state.backup_service.download(identifier)
    except ValueError:
        raise HTTPException(404, 'Owned encrypted export is unavailable.') from None
    return Response(payload, media_type='application/octet-stream', headers={'Cache-Control': 'no-store',
        'Content-Disposition': 'attachment; filename="USMAN-JARVIS-backup.encrypted"', 'X-Content-Type-Options': 'nosniff'})


@router.delete('/capture/{identifier}')
async def discard(identifier: str, request: Request):
    # Dropping memory remains available during stop/restricted read policy.
    request.app.state.backup_service.discard(identifier)
    return {'status': 'discarded_if_owned'}
