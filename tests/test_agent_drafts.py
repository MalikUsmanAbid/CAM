"""Virtual workspace regression source; all files/database are temporary."""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from services.agent_api.app.agent_drafts import (
    AgentDrafts,
    DraftEdit,
    DraftId,
    DraftModel,
    DraftVersion,
    SnapshotInput,
    register_agent_drafts,
    sha,
)
from services.agent_api.app.agent_workspace import role_tool
from services.agent_api.app.migrations import initialize_database
from services.agent_api.app.session_security import execution_dispatch_authorized
from services.agent_api.app.tools import ToolRegistry


def fixture_drafts(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'math.py').write_bytes(b'def add(a, b):\n    return a - b\n')
    (root / 'user.txt').write_bytes(b'Unrelated dirty user content\n')
    database = tmp_path / 'temporary.sqlite'
    initialize_database(database)
    drafts = AgentDrafts(root, database, 'A')
    value = SnapshotInput(files=[{'path': 'math.py', 'source_sha256': sha((root / 'math.py').read_text())}])
    return root, database, drafts, drafts.create(value)


def edit_value(snapshot, content):
    return DraftEdit(workspace_id=snapshot['workspace_id'], expected_revision=snapshot['revision'],
                     path='math.py', before_sha256=snapshot['files'][0]['sha256'], content=content)


def test_independent_drafts_preserve_working_tree_and_prepare_exact_proposals(tmp_path):
    root, database, drafts, first = fixture_drafts(tmp_path)
    before = (root / 'math.py').read_bytes()
    second = drafts.create(SnapshotInput(files=[{'path': 'math.py', 'source_sha256': sha(before.decode())}]))
    changed = drafts.edit(edit_value(first, 'def add(a, b):\n    return a + b\n'))
    assert changed['revision'] == 2
    assert drafts.inspect(DraftId(workspace_id=second['workspace_id']))['files'][0]['content'] == before.decode()
    merge = drafts.merge(DraftVersion(workspace_id=first['workspace_id'], expected_revision=2))
    assert len(merge['proposals']) == 1 and merge['source_files_modified'] is False
    proposal = merge['proposals'][0]
    assert proposal['before_sha256'] == sha(before.decode())
    assert proposal['after_sha256'] == sha(changed['files'][0]['content'])
    assert '-    return a - b' in proposal['diff'] and '+    return a + b' in proposal['diff']
    assert (root / 'math.py').read_bytes() == before
    assert (root / 'user.txt').read_bytes() == b'Unrelated dirty user content\n'
    with pytest.raises(ValueError, match='unavailable'):
        AgentDrafts(root, database, 'B').inspect(DraftId(workspace_id=first['workspace_id']))
    other = tmp_path / 'other-root'
    other.mkdir()
    with pytest.raises(ValueError, match='unavailable'):
        AgentDrafts(other, database, 'A').inspect(DraftId(workspace_id=first['workspace_id']))


def test_stale_user_edit_blocks_merge_and_retains_user_data(tmp_path):
    root, _, drafts, snapshot = fixture_drafts(tmp_path)
    changed = drafts.edit(edit_value(snapshot, 'Corrected draft\n'))
    (root / 'math.py').write_bytes(b'Later user edit\n')
    with pytest.raises(ValueError, match='User source changed'):
        drafts.merge(DraftVersion(workspace_id=snapshot['workspace_id'], expected_revision=changed['revision']))
    assert drafts.changes.list()['changes'] == []
    assert (root / 'math.py').read_bytes() == b'Later user edit\n'


def test_synchronized_competing_draft_edits_only_one_revision_wins(tmp_path):
    _, _, drafts, snapshot = fixture_drafts(tmp_path)
    barrier = threading.Barrier(2)
    def compete(content):
        barrier.wait(timeout=5)
        try:
            drafts.edit(edit_value(snapshot, content))
            return 'accepted'
        except ValueError:
            return 'stale'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(compete, value) for value in ('First draft\n', 'Second draft\n')]
        assert sorted(future.result(timeout=10) for future in futures) == ['accepted', 'stale']
    current = drafts.inspect(DraftId(workspace_id=snapshot['workspace_id']))
    assert current['revision'] == 2 and current['files'][0]['content'] in ('First draft\n', 'Second draft\n')
    drafts.close(DraftVersion(workspace_id=snapshot['workspace_id'], expected_revision=2))
    with pytest.raises(ValueError, match='closed'):
        drafts.edit(edit_value(current, 'After close\n'))


@pytest.mark.anyio
async def test_cancelled_model_draft_cannot_write_after_await(tmp_path):
    root, database, drafts, snapshot = fixture_drafts(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    class Provider:
        name = 'fixture'
        model = 'controlled-response'
        async def respond(self, prompt, *, json_output=False):
            assert json_output is True
            assert 'return a - b' in prompt
            entered.set()
            await release.wait()
            return json.dumps({'content': 'Unexpected write\n'})
    registry = ToolRegistry(root)
    register_agent_drafts(registry, database, 'A', Provider(), 5)
    cancelled = False
    def authorize():
        if cancelled:
            raise ValueError('Job cancelled')
    context = execution_dispatch_authorized.set(authorize)
    task = None
    try:
        arguments = DraftModel(workspace_id=snapshot['workspace_id'], expected_revision=1, path='math.py', before_sha256=snapshot['files'][0]['sha256'], request='Fix addition')
        assert role_tool('coder', arguments.model_dump()) == 'draft_ai_edit'
        task = asyncio.create_task(registry.get('draft_ai_edit').handler(arguments))
        await asyncio.wait_for(entered.wait(), timeout=2)
        cancelled = True
        release.set()
        with pytest.raises(ValueError, match='cancelled'):
            await task
    finally:
        release.set()
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        execution_dispatch_authorized.reset(context)
    assert drafts.inspect(DraftId(workspace_id=snapshot['workspace_id']))['revision'] == 1
    assert (root / 'math.py').read_bytes() == b'def add(a, b):\n    return a - b\n'


@pytest.mark.anyio
async def test_coder_job_invokes_real_draft_handler_only_after_its_approval(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from services.agent_api.app import agent_workspace
    from services.agent_api.app.agent_workspace import AgentJobInput, AgentWorkspace
    from services.agent_api.app.memory import MemoryRepository
    from services.agent_api.app.tools import ToolExecutor

    root, database, drafts, snapshot = fixture_drafts(tmp_path)
    calls = []
    class Provider:
        name = 'fixture'
        model = 'isolated-coder-fixture'
        async def respond(self, prompt, *, json_output=False):
            assert json_output is True
            calls.append(prompt)
            return json.dumps({'content': 'def add(a, b):\n    return a + b\n'})
    registry = ToolRegistry(root)
    register_agent_drafts(registry, database, 'A', Provider(), 5)
    registry.get('draft_ai_edit').enabled = True
    memory = MemoryRepository(database)
    runtime = SimpleNamespace(root=root, memory=memory, executor=ToolExecutor(registry, memory))
    app = SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(database_path=database)))
    monkeypatch.setattr(agent_workspace, 'runtime_for', lambda app_, project_id: runtime)
    jobs = AgentWorkspace(app)
    job = jobs.create('A', AgentJobInput(role='coder', arguments={
        'workspace_id': snapshot['workspace_id'], 'expected_revision': 1, 'path': 'math.py',
        'before_sha256': snapshot['files'][0]['sha256'], 'request': 'Fix addition',
    }))
    assert job['paths'] == ['draft:' + snapshot['workspace_id'] + ':math.py']
    pending = await jobs.run('A', job['job_id'])
    assert pending['status'] == 'waiting_for_approval' and not calls
    memory.decide_approval(pending['approval_id'], True)
    completed = await jobs.run('A', job['job_id'], continuing=True)
    assert completed['status'] == 'completed'
    assert completed['result']['tool_name'] == 'draft_ai_edit'
    assert len(calls) == 1 and 'return a - b' in calls[0]
    inspected = drafts.inspect(DraftId(workspace_id=snapshot['workspace_id']))
    assert inspected['revision'] == 2 and 'return a + b' in inspected['files'][0]['content']
    assert (root / 'math.py').read_bytes() == b'def add(a, b):\n    return a - b\n'


@pytest.mark.anyio
async def test_snapshot_approval_binds_each_read_destination_and_retains_after_approval(tmp_path):
    from services.agent_api.app.memory import MemoryRepository
    from services.agent_api.app.project_runtime import project_scope
    from services.agent_api.app.tools import ToolExecutor

    root, database, _, _ = fixture_drafts(tmp_path)
    registry = ToolRegistry(root)
    register_agent_drafts(registry, database, 'A', None, 5)
    memory = MemoryRepository(database)
    executor = ToolExecutor(registry, memory)
    value = {'files': [{'path': 'math.py', 'source_sha256': sha((root / 'math.py').read_text())},
                       {'path': 'user.txt', 'source_sha256': sha((root / 'user.txt').read_text())}]}
    with project_scope('A'):
        proposal = await executor.execute('draft_create', value)
        assert proposal['status'] == 'approval_required'
        assert proposal['approval']['preview']['execution_scope']['destinations'] == sorted([str((root / 'math.py').resolve()), str((root / 'user.txt').resolve())])
        grant = proposal['approval']['request_id']
        memory.decide_approval(grant, True)
        result = await executor.continue_approved(grant)
    assert result['status'] == 'success' and len(result['result']['files']) == 2
