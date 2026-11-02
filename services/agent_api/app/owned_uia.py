"""Attended UIA through one owned, bounded worker per operation.

Production enablement remains closed pending the separate Windows security
acceptance gate. No environment variable can silently bypass that gate.
"""

import asyncio
import hashlib
import json
import sqlite3
import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import Field, field_validator

from services.agent_api.app.action_policy import ActionCategory
from services.agent_api.app.memory import is_sensitive_content
from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.session_security import (
    execution_authorized,
    execution_dispatch_authorized,
    execution_session,
)
from services.agent_api.app.tools import (
    EmptyInput,
    Tool,
    ToolExecutionOutcome,
    ToolInput,
    WorkspaceGuard,
)
from services.agent_api.app.windows_commands import run_process
from services.agent_api.app.workspace_records import WorkspaceRecords, now


class OpenOwnedUIA(ToolInput):
    titles: list[str] = Field(min_length=1, max_length=3)
    attended: bool

    @field_validator('titles')
    @classmethod
    def exact_titles(cls, value):
        if len(set(value)) != len(value) or any(not title.strip() or len(title) > 200 or '::' in title or is_sensitive_content(title) for title in value):
            raise ValueError('Select distinct exact visible window titles without potential credentials.')
        return value


class OwnedSessionInput(ToolInput):
    session_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class OwnedActionInput(OwnedSessionInput):
    operation_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    target: str = Field(min_length=1, max_length=266)
    observation_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    value: str = Field(default='', max_length=2000)

    @field_validator('value')
    @classmethod
    def bounded_text(cls, value):
        if len(value.encode()) > 2000 or is_sensitive_content(value):
            raise ValueError('UIA value exceeds its limit or contains potential credentials.')
        return value


class OwnedUIAAdapter:
    def __init__(self, owner, *, runner=run_process):
        self.owner = owner
        self.runner = runner
        self.windows = []
        self.closed = False
        self.active = None
        self.lock = asyncio.Lock()
        self.operations = asyncio.Lock()
        self.observation_sha256 = None
        self.controls = {}

    def authorize(self):
        if self.closed or execution_session.get() != self.owner or not execution_authorized.get()():
            raise ValueError('UIA session is closed or belongs to a different/expired actor.')
        execution_dispatch_authorized.get()()

    async def request(self, action, *, titles=None, target='', value=''):
        async with self.lock:
            self.authorize()
            request = {'version': 1, 'action': action, 'titles': titles} if action == 'attach' else {
                'version': 1, 'action': action, 'windows': self.windows, 'target': target, 'value': value}
            payload = json.dumps(request, ensure_ascii=False).encode() + b'\n'
            if len(payload) > 8192:
                raise ValueError('UIA request exceeds its private channel limit.')
            executable = Path(sys.executable).resolve()
            arguments = ['--uia-worker'] if getattr(sys, 'frozen', False) else ['-I', str(Path(__file__).with_name('uia_worker.py').resolve())]
            try:
                with tempfile.TemporaryDirectory(prefix='jarvis-uia-worker-') as temporary:
                    self.active = asyncio.create_task(self.runner(executable, arguments, Path(temporary), timeout=8,
                        output_limit=64000, input_data=payload, authorize=self.authorize))
                    output = await self.active
                self.authorize()
                if output['status'] != 'succeeded' or output['truncated']:
                    raise ValueError('UIA worker did not return a bounded verified protocol result.')
                response = json.loads(output['output'])
                if not isinstance(response, dict) or response.get('version') != 1 or response.get('status') != 'completed' or not isinstance(response.get('result'), dict):
                    raise ValueError('UIA outcome is unverified. Observe/reconcile before creating another session.')
                result = response['result']
                if action in {'invoke', 'set_value'} and (result.get('action') != action or result.get('target') != target or result.get('outcome') != 'provider_call_returned'):
                    raise ValueError('UIA worker did not identify the exact executed action/control.')
                if action == 'locate' and (result.get('target') != target or result.get('effect') != 'none'):
                    raise ValueError('UIA worker did not identify the inspected control.')
                if is_sensitive_content(json.dumps(result, ensure_ascii=False)):
                    raise ValueError('Potential credential content is excluded from UIA output.')
                return result
            except BaseException:
                # A provider could have accepted an action before timeout or
                # cancellation. Do not let an old session blindly retry it.
                self.closed = True
                self.controls.clear()
                self.observation_sha256 = None
                raise
            finally:
                self.active = None

    @classmethod
    async def create(cls, titles, *, runner=run_process):
        adapter = cls(execution_session.get(), runner=runner)
        result = await adapter.request('attach', titles=titles)
        windows = result.get('windows')
        if not isinstance(windows, list) or len(windows) != len(titles) or [item.get('title') for item in windows] != titles:
            await adapter.close()
            raise ValueError('UIA worker returned mismatched window scope.')
        adapter.windows = windows
        return adapter

    async def observe(self):
        async with self.operations:
            return await self._observe()

    async def _observe(self):
        self.observation_sha256 = None
        self.controls.clear()
        result = await self.request('observe')
        text = result.get('text')
        if not isinstance(text, str) or len(text.encode()) > 10000:
            await self.close()
            raise ValueError('UIA observation exceeded its evidence bound.')
        try:
            controls = json.loads(text)
        except (ValueError, RecursionError):
            await self.close()
            raise ValueError('UIA observation protocol is invalid.') from None
        if not isinstance(controls, list) or any(not isinstance(item, dict) or not isinstance(item.get('target'), str) for item in controls):
            await self.close()
            raise ValueError('UIA observation protocol is invalid.')
        self.observation_sha256 = hashlib.sha256(text.encode()).hexdigest()
        self.controls = {item['target']: item for item in controls}
        return {'text': text, 'observation_sha256': self.observation_sha256, 'trust': 'untrusted_data'}

    def preview(self, value):
        self.authorize()
        if value.observation_sha256 != self.observation_sha256 or value.target not in self.controls:
            raise ValueError('Observe the exact current control before requesting an action.')
        return {'observed_control': self.controls[value.target], 'selected_windows': self.windows,
                'effect_boundary': 'Only this fingerprinted control. Provider acceptance may trigger irreversible external effects; timeout/cancellation cannot undo them.'}

    async def act(self, action, value):
        async with self.operations:
            return await self._act(action, value)

    async def _act(self, action, value):
        self.preview(value)
        # Invalidate the grant's observation before an effect can be accepted.
        # A separately queued action with this old observation will fail.
        if action != 'locate':
            self.observation_sha256 = None
            self.controls.clear()
        return await self.request(action, target=value.target, value=value.value)

    async def close(self):
        self.closed = True
        self.controls.clear()
        self.observation_sha256 = None
        active = self.active
        if active is not None and active is not asyncio.current_task():
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)


def register_owned_uia_tools(registry, manager, *, enabled=False, runner=run_process, memory=None):
    guard = WorkspaceGuard(registry.workspace_root)
    records = WorkspaceRecords(memory.database_path) if memory is not None else None
    if records is not None:
        with records.connect() as db:
            db.execute("UPDATE owned_uia_operations SET status='outcome_unknown',updated_at=? WHERE status='dispatching'", (now(),))

    def selected(identifier):
        session = manager._sessions.get(identifier)
        if session is None or not isinstance(session.adapter, OwnedUIAAdapter) or datetime.now(UTC) - session.last_activity > timedelta(minutes=10):
            raise ValueError('Owned UIA session expired or is unavailable.')
        session.adapter.authorize()
        return session

    async def opening(value):
        if not value.attended:
            raise ValueError('Explicit attended-session consent is required.')
        for identifier, session in list(manager._sessions.items()):
            if datetime.now(UTC) - session.last_activity > timedelta(minutes=10) or getattr(session.adapter, 'closed', False):
                await manager.close(identifier)
        # Reserve a slot before awaiting attachment; concurrent opens cannot
        # exceed the manager's bound or leak an unregistered owned worker.
        identifier = uuid.uuid4().hex
        adapter = OwnedUIAAdapter(execution_session.get(), runner=runner)
        manager.create(identifier, adapter, value.titles)
        try:
            response = await adapter.request('attach', titles=value.titles)
            windows = response.get('windows')
            if not isinstance(windows, list) or len(windows) != len(value.titles) or [item.get('title') for item in windows] != value.titles:
                raise ValueError('UIA attachment scope did not match the selected titles.')
            adapter.windows = windows
        except BaseException:
            await manager.close(identifier)
            raise
        return {'session_id': identifier, 'windows': adapter.windows, 'status': 'attended_owned_session', 'unsupported': 'Global keys, pointer/coordinate clicks, elevated windows and cross-process controls.'}

    async def observing(value):
        session = selected(value.session_id)
        manager._tick(session)
        return await session.adapter.observe()

    async def listing(_):
        owner = execution_session.get()
        return {'sessions': [{'session_id': identifier, 'windows': session.adapter.windows,
            'last_activity': session.last_activity.isoformat()} for identifier, session in manager._sessions.items()
            if isinstance(session.adapter, OwnedUIAAdapter) and session.adapter.owner == owner and not session.adapter.closed
            and datetime.now(UTC) - session.last_activity <= timedelta(minutes=10)]}

    async def operations(_):
        if records is None:
            return {'operations': []}
        with records.connect() as db:
            return {'operations': [dict(row) for row in db.execute('SELECT operation_id,action,status,created_at,updated_at FROM owned_uia_operations ORDER BY created_at DESC LIMIT 50')],
                    'boundary': 'Local-user audit only; no window titles, typed text or session credentials are retained.'}

    async def closing(value):
        selected(value.session_id)
        await manager.close(value.session_id)
        return {'status': 'closed', 'external_effects_undone': False}

    async def action(name, value):
        session = selected(value.session_id)
        manager._tick(session)
        session.adapter.preview(value)
        if name == 'locate':
            return await session.adapter.act(name, value)
        if records is None:
            raise ValueError('Consequential UIA requires a durable operation ledger.')
        contract = hashlib.sha256(json.dumps(value.model_dump(), sort_keys=True).encode()).hexdigest()
        with records.connect() as db:
            try:
                db.execute("INSERT INTO owned_uia_operations VALUES (?,?,?,?,'dispatching',?,?)", (value.operation_id, value.session_id, name, contract, now(), now()))
            except sqlite3.IntegrityError:
                raise ValueError('UIA operation already exists; reconcile its outcome instead of retrying.') from None
        status = 'outcome_unknown'
        try:
            result = await session.adapter.act(name, value)
            status = 'provider_call_returned'
            return {**result, 'operation_id': value.operation_id}
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ToolExecutionOutcome({'status': 'outcome_unknown', 'operation_id': value.operation_id,
                'retry': 'Observe/reconcile the target application manually before any new operation.'}) from None
        finally:
            with records.connect() as db:
                db.execute('UPDATE owned_uia_operations SET status=?,updated_at=? WHERE operation_id=?', (status, now(), value.operation_id))
            memory.record_event('computer_owned_' + name, status, 'Owned UIA operation ' + value.operation_id + ': ' + status + '. External effects are not assumed undone.')

    for name, schema, risk, handler, category in [
        ('computer_owned_open', OpenOwnedUIA, RiskLevel.HIGH, opening, ActionCategory.READ),
        ('computer_owned_observe', OwnedSessionInput, RiskLevel.LOW, observing, ActionCategory.READ),
        ('computer_owned_sessions', EmptyInput, RiskLevel.LOW, listing, ActionCategory.READ),
        ('computer_owned_operations', EmptyInput, RiskLevel.LOW, operations, ActionCategory.READ),
        ('computer_owned_close', OwnedSessionInput, RiskLevel.LOW, closing, ActionCategory.READ),
    ]:
        registry.register(Tool(name, 'Attended bounded UIA via an owned worker; acceptance gate remains closed.', schema, risk, 12, handler,
            enabled=enabled, resolve_destination=lambda _: guard.resolve('.'), category=category))
    for name in ('locate', 'invoke', 'set_value'):
        registry.register(Tool('computer_owned_' + name, 'Exact observed UIA control action; observe again after effects and reconcile uncertain outcomes.',
            OwnedActionInput, RiskLevel.HIGH if name != 'locate' else RiskLevel.LOW, 12,
            lambda value, action_name=name: action(action_name, value), enabled=enabled,
            resolve_destination=lambda _: guard.resolve('.'),
            category=ActionCategory.DESTRUCTIVE if name != 'locate' else ActionCategory.READ,
            preview_details=lambda args: selected(args['session_id']).adapter.preview(OwnedActionInput(**args))))
