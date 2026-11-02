"""Managed-copy regression source. Only tmp_path persistence; not live acceptance."""

import asyncio
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from services.agent_api.app.agent_drafts import (
    AgentDrafts,
    DraftEdit,
    DraftVersion,
    SnapshotInput,
    sha,
)
from services.agent_api.app.draft_materializations import (
    DraftMaterializations,
    Materialization,
    MaterializedCommand,
    MaterializedRun,
)
from services.agent_api.app.migrations import initialize_database
from services.agent_api.app.session_security import execution_dispatch_authorized
from services.agent_api.app.studio_commands import RunCommand, contract_hash
from services.agent_api.app.tools import _approved_revision


def fixture(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'example.py').write_bytes(b'print(1)\n')
    (root / 'user.txt').write_bytes(b'Unrelated user edits\n')
    database = tmp_path / 'private.sqlite'
    initialize_database(database)
    drafts = AgentDrafts(root, database, 'project-A')
    snapshot = drafts.create(SnapshotInput(files=[{'path': 'example.py', 'source_sha256': sha('print(1)\n')}]))
    copies = DraftMaterializations(root, database, 'project-A')
    prepared = copies.prepare(DraftVersion(workspace_id=snapshot['workspace_id'], expected_revision=1))
    value = Materialization(materialization_id=prepared['materialization_id'], contract_sha256=prepared['contract_sha256'])
    return root, copies, value, snapshot


def ready_fixture(root, copies, value):
    # Portable inert directory fixture for boundary tests, not native-create proof.
    _, contract = copies.load(value)
    target = root / contract['directory']
    target.mkdir()
    (target / 'example.py').write_bytes(b'print(1)\n')
    identity = target.stat()
    with copies.drafts.records.connect() as db:
        db.execute("UPDATE draft_materializations SET status='ready',device=?,inode=? WHERE materialization_id=?",
                   (str(identity.st_dev), str(identity.st_ino), value.materialization_id))
    return target


def test_prepare_is_database_only_and_project_hash_bound(tmp_path):
    root, copies, value, _ = fixture(tmp_path)
    assert sorted(path.name for path in root.iterdir()) == ['example.py', 'user.txt']
    assert copies.describe(value)['status'] == 'prepared'
    with pytest.raises(ValueError, match='unavailable'):
        copies.load(Materialization(materialization_id=value.materialization_id, contract_sha256='0' * 64))
    other = DraftMaterializations(root, copies.commands.database, 'project-B')
    with pytest.raises(ValueError, match='unavailable'):
        other.load(value)


def test_changed_virtual_revision_blocks_old_copy_even_if_directory_ready(tmp_path):
    root, copies, value, snapshot = fixture(tmp_path)
    ready_fixture(root, copies, value)
    copies.drafts.edit(DraftEdit(workspace_id=snapshot['workspace_id'], expected_revision=1,
                                path='example.py', before_sha256=sha('print(1)\n'), content='print(2)\n'))
    with pytest.raises(ValueError, match='Draft changed'):
        copies.ready(value)
    assert copies.describe(value)['status'] == 'ready'  # Inspection remains possible for recovery.


def test_changed_input_and_replaced_directory_block_execution(tmp_path):
    root, copies, value, _ = fixture(tmp_path)
    target = ready_fixture(root, copies, value)
    (target / 'example.py').write_bytes(b'print(99)\n')
    with pytest.raises(ValueError, match='input changed'):
        copies.check_inputs(value)
    # Retain the old inode, so replacement cannot incidentally reuse its identity.
    target.rename(root / 'retained-copy')
    target.mkdir()
    with pytest.raises(ValueError, match='identity changed'):
        copies.ready(value)


def test_collection_proposes_exact_selected_text_preserving_user_edits(tmp_path):
    root, copies, value, _ = fixture(tmp_path)
    target = ready_fixture(root, copies, value)
    (target / 'example.py').write_bytes(b'print(2)\n')
    (target / 'unselected.py').write_bytes(b'not collected\n')
    output = copies.collect(value)
    assert len(output['proposals']) == 1
    assert output['source_files_modified'] is False
    assert output['proposals'][0]['after_sha256'] == sha('print(2)\n')
    assert (root / 'example.py').read_bytes() == b'print(1)\n'
    assert (root / 'user.txt').read_bytes() == b'Unrelated user edits\n'
    (root / 'example.py').write_bytes(b'user correction\n')
    with pytest.raises(ValueError, match='User source changed'):
        copies.collect(value)
    assert (root / 'example.py').read_bytes() == b'user correction\n'


def test_hardlinked_output_is_rejected_before_proposals(tmp_path):
    root, copies, value, _ = fixture(tmp_path)
    target = ready_fixture(root, copies, value)
    selected = target / 'example.py'
    selected.unlink()  # Disposable fixture file only.
    os.link(root / 'example.py', selected)
    with pytest.raises(ValueError, match='Hard-linked'):
        copies.collect(value)
    assert copies.drafts.changes.list()['changes'] == []
    assert (root / 'example.py').read_bytes() == b'print(1)\n'


def test_potential_credentials_in_output_are_not_retained_as_proposals(tmp_path):
    root, copies, value, _ = fixture(tmp_path)
    target = ready_fixture(root, copies, value)
    # Synthetic credential-shaped fixture, never a real account token.
    (target / 'example.py').write_text('api_key = "sk-' + 'x' * 48 + '"\n', encoding='utf-8')
    with pytest.raises(ValueError):
        copies.collect(value)
    assert copies.drafts.changes.list()['changes'] == []
    assert (root / 'example.py').read_bytes() == b'print(1)\n'


def test_command_preparation_uses_managed_directory_and_exact_arguments(tmp_path, monkeypatch):
    root, copies, value, _ = fixture(tmp_path)
    target = ready_fixture(root, copies, value)
    received = []
    def prepare(command, *, managed_copy):
        received.append(command)
        assert managed_copy == value.model_dump()
        return {'run_id': 'a' * 32, 'contract_sha256': 'b' * 64}
    monkeypatch.setattr(copies.commands, 'prepare', prepare)
    command = MaterializedCommand(**value.model_dump(), executable='C:/fixture/python.exe', arguments=['-I', 'example.py'])
    output = copies.prepare_command(command)
    assert received[0].cwd == target.name
    assert received[0].arguments == ['-I', 'example.py']
    assert output['materialization_contract_sha256'] == value.contract_sha256
    with pytest.raises(ValueError, match='exact managed directory'):
        copies.prepare_command(command.model_copy(update={'cwd': '..'}))


def test_foreign_command_contract_never_dispatches(tmp_path):
    root, copies, value, _ = fixture(tmp_path)
    ready_fixture(root, copies, value)
    command = {'cwd': '.', 'project_id': 'project-A', 'root': str(root)}
    digest = contract_hash(command)
    with copies.drafts.records.connect() as db:
        db.execute('INSERT INTO command_runs VALUES (?,?,?,?,?,?,?,?)',
                   ('c' * 32, 'project-A', str(root), json.dumps(command), digest, 'prepared', None, 'fixture'))
    with pytest.raises(ValueError, match='different managed directory'):
        copies.command(MaterializedRun(**value.model_dump(), run_id='c' * 32, run_contract_sha256=digest))


def test_dispatch_composes_cancellation_and_restores_context(tmp_path, monkeypatch):
    root, copies, value, _ = fixture(tmp_path)
    ready_fixture(root, copies, value)
    command = {'cwd': copies.load(value)[1]['directory'], 'project_id': 'project-A', 'root': str(root)}
    digest = contract_hash(command)
    with copies.drafts.records.connect() as db:
        db.execute('INSERT INTO command_runs VALUES (?,?,?,?,?,?,?,?)',
                   ('c' * 32, 'project-A', str(root), json.dumps(command), digest, 'prepared', None, 'fixture'))
    accepted = True
    effects = []
    def authorize():
        if not accepted:
            raise ValueError('cancelled')
    async def running(_):
        nonlocal accepted
        accepted = False
        execution_dispatch_authorized.get()()
        effects.append('must not happen')
    monkeypatch.setattr(copies.commands, 'run', running)
    token = execution_dispatch_authorized.set(authorize)
    try:
        with pytest.raises(ValueError, match='cancelled'):
            asyncio.run(copies.run(MaterializedRun(**value.model_dump(), run_id='c' * 32, run_contract_sha256=digest)))
        assert effects == []
        assert execution_dispatch_authorized.get() is authorize
    finally:
        execution_dispatch_authorized.reset(token)


def test_normal_studio_entry_point_cannot_bypass_managed_revision_binding(tmp_path):
    root, copies, value, snapshot = fixture(tmp_path)
    target = ready_fixture(root, copies, value)
    command = {'cwd': target.name, 'project_id': 'project-A', 'root': str(root),
               'executable': str(tmp_path / 'never-start.exe'), 'managed_copy': value.model_dump()}
    digest = contract_hash(command)
    with copies.drafts.records.connect() as db:
        db.execute('INSERT INTO command_runs VALUES (?,?,?,?,?,?,?,?)',
                   ('d' * 32, 'project-A', str(root), json.dumps(command), digest, 'prepared', None, 'fixture'))
    copies.drafts.edit(DraftEdit(workspace_id=snapshot['workspace_id'], expected_revision=1,
                                path='example.py', before_sha256=sha('print(1)\n'), content='print(2)\n'))
    token = _approved_revision.set(1)
    try:
        with pytest.raises(ValueError, match='Draft changed'):
            asyncio.run(copies.commands.run(RunCommand(run_id='d' * 32, contract_sha256=digest)))
    finally:
        _approved_revision.reset(token)
    assert copies.commands.load(RunCommand(run_id='d' * 32, contract_sha256=digest))[0]['status'] == 'prepared'


@pytest.mark.anyio
async def test_copy_requires_approval_bound_to_exact_destination_and_manifest(tmp_path):
    from services.agent_api.app.draft_materializations import register_draft_materializations
    from services.agent_api.app.memory import MemoryRepository
    from services.agent_api.app.project_runtime import project_scope
    from services.agent_api.app.tools import ToolExecutor, ToolRegistry
    root, copies, value, _ = fixture(tmp_path)
    registry = ToolRegistry(root)
    register_draft_materializations(registry, copies.commands.database, 'project-A')
    memory = MemoryRepository(copies.commands.database)
    executor = ToolExecutor(registry, memory)
    with project_scope('project-A'):
        request = await executor.execute('draft_copy_create', value.model_dump())
    assert request['status'] == 'approval_required'
    preview = request['approval']['preview']
    target = copies.destination(value.model_dump())
    assert preview['execution_scope']['destination'] == str(target)
    assert not target.exists()
    # The approval's tool-specific detail contains exact file hashes, not just
    # an opaque copy ID. Inspect the preview recursively without fixing its UI layout.
    assert sha('print(1)\n') in json.dumps(preview)


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows directory claims required')
def test_synchronized_competing_materializations_create_only_one_copy(tmp_path):
    root, copies, value, _ = fixture(tmp_path)
    barrier = threading.Barrier(2)
    def compete():
        barrier.wait(timeout=5)
        try:
            copies.materialize(value)
            return 'created'
        except ValueError:
            return 'denied'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: compete(), range(2)))
    assert sorted(results) == ['created', 'denied']
    target, _ = copies.ready(value)
    assert [item.name for item in target.iterdir()] == ['example.py']
    assert (root / 'example.py').read_bytes() == b'print(1)\n'


@pytest.mark.skipif(os.name != 'nt', reason='Actual exclusive Windows creation required')
def test_native_copy_is_exclusive_and_original_files_unchanged(tmp_path):
    root, copies, value, _ = fixture(tmp_path)
    output = copies.materialize(value)
    assert output['status'] == 'ready'
    target, _ = copies.ready(value)
    assert (target / 'example.py').read_bytes() == b'print(1)\n'
    assert (root / 'example.py').read_bytes() == b'print(1)\n'
    assert (root / 'user.txt').read_bytes() == b'Unrelated user edits\n'
    with pytest.raises(ValueError, match='already exists'):
        copies.materialize(value)


@pytest.mark.skipif(os.name != 'nt', reason='Actual nested Windows directory creation required')
def test_native_copy_preserves_selected_nested_file_paths_only(tmp_path):
    root, copies, _, _ = fixture(tmp_path)
    (root / 'src').mkdir()
    (root / 'src' / 'nested.py').write_bytes(b'print(3)\n')
    snapshot = copies.drafts.create(SnapshotInput(files=[
        {'path': 'example.py', 'source_sha256': sha('print(1)\n')},
        {'path': 'src/nested.py', 'source_sha256': sha('print(3)\n')},
    ]))
    prepared = copies.prepare(DraftVersion(workspace_id=snapshot['workspace_id'], expected_revision=1))
    value = Materialization(materialization_id=prepared['materialization_id'], contract_sha256=prepared['contract_sha256'])
    copies.materialize(value)
    target, _ = copies.ready(value)
    assert (target / 'src' / 'nested.py').read_bytes() == b'print(3)\n'
    assert not (target / 'user.txt').exists()
    assert not (target / '.git').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Actual exclusive Windows creation required')
def test_native_interruption_retains_partial_directory_and_denies_retry(tmp_path, monkeypatch):
    root, copies, value, _ = fixture(tmp_path)
    import services.agent_api.app.draft_materializations as module
    original = module._create_owned_directory
    @contextmanager
    def interrupted(target):
        with original(target):
            raise ValueError('fixture interruption')
            yield
    monkeypatch.setattr(module, '_create_owned_directory', interrupted)
    with pytest.raises(ValueError, match='fixture interruption'):
        copies.materialize(value)
    assert copies.describe(value)['status'] == 'interrupted'
    assert copies.destination(value.model_dump()).is_dir()
    with pytest.raises(ValueError, match='already exists'):
        copies.materialize(value)
