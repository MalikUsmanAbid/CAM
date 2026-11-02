"""Owned UIA contract source. Uses injected workers, never real windows."""

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from services.agent_api.app import uia_worker
from services.agent_api.app.owned_uia import OwnedActionInput, OwnedUIAAdapter
from services.agent_api.app.session_security import execution_session


def output(result):
    return {'status': 'succeeded', 'truncated': False, 'output': json.dumps({'version': 1, 'status': 'completed', 'result': result})}


def window():
    return {'hwnd': 123, 'title': 'Fixture App', 'process': {'pid': 321, 'created': 42, 'session': 1, 'owner_sha256': 'b' * 64}}


@pytest.mark.anyio
async def test_private_worker_gets_no_target_text_in_argv_and_actions_require_current_observation():
    requests = []
    target = 'Fixture App::' + 'a' * 64
    async def runner(executable, arguments, cwd, **kwargs):
        request = json.loads(kwargs['input_data'])
        requests.append(request)
        assert 'Fixture App' not in ' '.join(arguments)
        assert kwargs['timeout'] == 8 and kwargs['output_limit'] == 64000
        kwargs['authorize']()
        if request['action'] == 'attach':
            return output({'windows': [window()]})
        if request['action'] == 'observe':
            return output({'text': json.dumps([{'target': target, 'type': 'Button', 'name': 'Fixture save'}])})
        assert request['action'] == 'invoke'
        assert request['windows'] == [window()] and request['target'] == target
        return output({'action': 'invoke', 'target': target, 'outcome': 'provider_call_returned'})
    adapter = await OwnedUIAAdapter.create(['Fixture App'], runner=runner)
    observed = await adapter.observe()
    assert observed['observation_sha256'] == hashlib.sha256(observed['text'].encode()).hexdigest()
    value = OwnedActionInput(session_id='a' * 32, operation_id='b' * 32, target=target, observation_sha256=observed['observation_sha256'])
    assert adapter.preview(value)['observed_control']['name'] == 'Fixture save'
    result = await adapter.act('invoke', value)
    assert result['outcome'] == 'provider_call_returned'
    with pytest.raises(ValueError, match='Observe'):
        await adapter.act('invoke', value)
    assert [request['action'] for request in requests] == ['attach', 'observe', 'invoke']
    await adapter.close()


@pytest.mark.anyio
async def test_owner_change_is_denied_before_worker_dispatch():
    calls = []
    async def runner(*args, **kwargs):
        calls.append(kwargs['input_data'])
        return output({'windows': [window()]})
    owner = execution_session.set('owner-A')
    adapter = await OwnedUIAAdapter.create(['Fixture App'], runner=runner)
    try:
        execution_session.set('owner-B')
        with pytest.raises(ValueError, match='actor'):
            await adapter.observe()
        assert len(calls) == 1
    finally:
        execution_session.reset(owner)
        await adapter.close()


@pytest.mark.anyio
async def test_close_cancels_active_owned_worker_before_cooperative_effect():
    entered, release = asyncio.Event(), asyncio.Event()
    effects = []
    async def runner(*args, **kwargs):
        if json.loads(kwargs['input_data'])['action'] == 'attach':
            return output({'windows': [window()]})
        entered.set()
        await release.wait()
        effects.append('must not occur after close')
        return output({'text': '[]'})
    adapter = await OwnedUIAAdapter.create(['Fixture App'], runner=runner)
    observing = asyncio.create_task(adapter.observe())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await adapter.close()
        with pytest.raises(asyncio.CancelledError):
            await observing
        assert effects == [] and adapter.closed
    finally:
        release.set()
        observing.cancel()
        await asyncio.gather(observing, return_exceptions=True)


def test_worker_rejects_global_keys_and_extra_executable_fields_without_os_access():
    with pytest.raises(ValueError, match='Unsupported'):
        uia_worker.execute({'version': 1, 'action': 'key_press', 'value': 'CTRL+R'})
    with pytest.raises(ValueError, match='Unsupported'):
        uia_worker.execute({'version': 1, 'action': 'attach', 'titles': ['Fixture App'], 'script': 'arbitrary code'})


def test_worker_rechecks_control_identity_immediately_before_invoke(monkeypatch):
    invoked = []
    control = SimpleNamespace(iface_invoke=SimpleNamespace(Invoke=lambda: invoked.append(True)))
    selected = window()
    fingerprint = 'a' * 64
    monkeypatch.setattr(uia_worker, 'verify_window', lambda value: value)
    monkeypatch.setattr(uia_worker, 'nodes', lambda value: iter([(control, fingerprint, {'type': 'Button'})]))
    identities = iter([fingerprint, 'c' * 64])
    monkeypatch.setattr(uia_worker, 'control_identity', lambda *args: (next(identities), {'type': 'Button'}))
    with pytest.raises(ValueError, match='changed before Invoke'):
        uia_worker.execute({'version': 1, 'action': 'invoke', 'windows': [selected],
            'target': 'Fixture App::' + fingerprint, 'value': ''})
    assert invoked == []


def test_worker_actual_invoke_pattern_runs_for_exact_control_only(monkeypatch):
    invoked = []
    control = SimpleNamespace(iface_invoke=SimpleNamespace(Invoke=lambda: invoked.append('exact button')))
    fingerprint = 'a' * 64
    monkeypatch.setattr(uia_worker, 'verify_window', lambda value: value)
    monkeypatch.setattr(uia_worker, 'nodes', lambda value: iter([(control, fingerprint, {'type': 'Button'})]))
    monkeypatch.setattr(uia_worker, 'control_identity', lambda *args: (fingerprint, {'type': 'Button'}))
    result = uia_worker.execute({'version': 1, 'action': 'invoke', 'windows': [window()],
        'target': 'Fixture App::' + fingerprint, 'value': ''})
    assert invoked == ['exact button'] and result['outcome'] == 'provider_call_returned'


@pytest.mark.anyio
async def test_action_waiting_for_observation_rejects_replaced_control():
    entered, release = asyncio.Event(), asyncio.Event()
    observed = 0
    effects = []
    async def runner(*args, **kwargs):
        nonlocal observed
        request = json.loads(kwargs['input_data'])
        if request['action'] == 'attach':
            return output({'windows': [window()]})
        if request['action'] == 'observe':
            observed += 1
            if observed == 2:
                entered.set()
                await release.wait()
            fingerprint = 'a' * 64 if observed == 1 else 'c' * 64
            return output({'text': json.dumps([{'target': 'Fixture App::' + fingerprint, 'type': 'Button', 'name': 'Observed button'}])})
        effects.append(request['action'])
        return output({'outcome': 'provider_call_returned'})
    adapter = await OwnedUIAAdapter.create(['Fixture App'], runner=runner)
    first = await adapter.observe()
    refresh = asyncio.create_task(adapter.observe())
    action = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        value = OwnedActionInput(session_id='a' * 32, operation_id='b' * 32,
            target='Fixture App::' + 'a' * 64, observation_sha256=first['observation_sha256'])
        action = asyncio.create_task(adapter.act('invoke', value))
        await asyncio.sleep(0)
        release.set()
        await refresh
        with pytest.raises(ValueError, match='Observe'):
            await action
        assert effects == []
    finally:
        release.set()
        await adapter.close()
        for task in (refresh, action):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_uncertain_uia_outcome_is_durable_and_cannot_reuse_operation_id(tmp_path):
    from services.agent_api.app.computer import ComputerManager
    from services.agent_api.app.memory import MemoryRepository
    from services.agent_api.app.migrations import initialize_database
    from services.agent_api.app.owned_uia import (
        OpenOwnedUIA,
        OwnedSessionInput,
        register_owned_uia_tools,
    )
    from services.agent_api.app.tools import ToolExecutionOutcome, ToolRegistry
    from services.agent_api.app.workspace_records import WorkspaceRecords

    database = tmp_path / 'temporary.sqlite'
    initialize_database(database)
    root = tmp_path / 'workspace'
    root.mkdir()
    effects = []
    target = 'Fixture App::' + 'a' * 64
    async def runner(*args, **kwargs):
        request = json.loads(kwargs['input_data'])
        if request['action'] == 'attach':
            return output({'windows': [window()]})
        if request['action'] == 'observe':
            return output({'text': json.dumps([{'target': target, 'name': 'Exact button', 'type': 'Button'}])})
        effects.append(request['action'])
        raise RuntimeError('Provider outcome cannot be established')
    registry, manager = ToolRegistry(root), ComputerManager()
    register_owned_uia_tools(registry, manager, enabled=True, runner=runner, memory=MemoryRepository(database))
    opened = await registry.get('computer_owned_open').handler(OpenOwnedUIA(titles=['Fixture App'], attended=True))
    observed = await registry.get('computer_owned_observe').handler(OwnedSessionInput(session_id=opened['session_id']))
    value = OwnedActionInput(operation_id='b' * 32, session_id=opened['session_id'], target=target,
        observation_sha256=observed['observation_sha256'])
    with pytest.raises(ToolExecutionOutcome) as failure:
        await registry.get('computer_owned_invoke').handler(value)
    assert failure.value.result['status'] == 'outcome_unknown'
    with WorkspaceRecords(database).connect() as db:
        row = dict(db.execute('SELECT * FROM owned_uia_operations').fetchone())
    assert row['status'] == 'outcome_unknown' and row['operation_id'] == value.operation_id
    assert 'Exact button' not in json.dumps(row)
    replacement = await registry.get('computer_owned_open').handler(OpenOwnedUIA(titles=['Fixture App'], attended=True))
    fresh = await registry.get('computer_owned_observe').handler(OwnedSessionInput(session_id=replacement['session_id']))
    duplicate = value.model_copy(update={'session_id': replacement['session_id'], 'observation_sha256': fresh['observation_sha256']})
    with pytest.raises(ValueError, match='already exists'):
        await registry.get('computer_owned_invoke').handler(duplicate)
    assert effects == ['invoke']
    await manager.close_all()
