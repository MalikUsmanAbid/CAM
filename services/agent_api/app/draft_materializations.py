"""Explicitly approved file copies for draft commands; never a process sandbox.

Only selected snapshot files are copied into a newly owned project subdirectory.
No Git metadata, dependency copying, cleanup, automatic command or source apply.
Interrupted copies remain visible for review and are never silently retried.
"""

import json
import os
import uuid
from contextlib import ExitStack

from services.agent_api.app.action_policy import ActionCategory
from services.agent_api.app.agent_drafts import (
    AgentDrafts,
    DraftEdit,
    DraftVersion,
    Identifier,
    Sha,
    sha,
)
from services.agent_api.app.coding_changes import ProposeInput
from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.project_creation import _create_owned_directory
from services.agent_api.app.session_security import execution_dispatch_authorized
from services.agent_api.app.studio_commands import (
    PrepareCommand,
    RunCommand,
    StudioCommands,
    contract_hash,
)
from services.agent_api.app.tools import EmptyInput, Tool, ToolInput
from services.agent_api.app.workspace_records import now


class Materialization(ToolInput):
    materialization_id: Identifier
    contract_sha256: Sha


class MaterializedCommand(Materialization, PrepareCommand):
    pass


class MaterializedRun(Materialization):
    run_id: Identifier
    run_contract_sha256: Sha


class DraftMaterializations:
    def __init__(self, root, database, project):
        self.drafts = AgentDrafts(root, database, project)
        self.commands = StudioCommands(root, database, project)
        self.files = self.drafts.files

    def prepare(self, value):
        snapshot = self.drafts.inspect(value)
        if snapshot['status'] != 'open' or snapshot['revision'] != value.expected_revision:
            raise ValueError('Draft revision changed or closed.')
        identifier = uuid.uuid4().hex
        contract = {'project_id': self.drafts.project_id, 'root': self.drafts.root,
                    'workspace_id': value.workspace_id, 'revision': value.expected_revision,
                    'directory': 'jarvis-draft-' + identifier,
                    'files': [{'path': item['path'], 'sha256': item['sha256'], 'base_sha': item['base_sha']}
                              for item in snapshot['files']]}
        # A file cannot also be another selected file's parent directory.
        names = [item['path'].casefold() for item in contract['files']]
        if any(a.startswith(b + '/') for a in names for b in names if a != b):
            raise ValueError('Selected file paths conflict with a directory.')
        checksum = contract_hash(contract)
        execution_dispatch_authorized.get()()
        with self.drafts.records.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM draft_materializations WHERE project_id=?', (self.drafts.project_id,)).fetchone()[0] >= 128:
                raise ValueError('Managed copy retention limit reached; review retained directories before maintenance.')
            db.execute('INSERT INTO draft_materializations VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (identifier, self.drafts.project_id, self.drafts.root, value.workspace_id,
                        value.expected_revision, json.dumps(contract), checksum, 'prepared', None, None, now()))
        return {**self.describe(Materialization(materialization_id=identifier, contract_sha256=checksum)),
                'warning': 'Copies selected files only. Approved commands can access your user files and network; this directory is not a sandbox.'}

    def load(self, value, *, current=True):
        with self.drafts.records.connect() as db:
            row = db.execute('SELECT * FROM draft_materializations WHERE materialization_id=? AND project_id=? AND root=?',
                             (value.materialization_id, self.drafts.project_id, self.drafts.root)).fetchone()
        if row is None or row['contract_sha'] != value.contract_sha256:
            raise ValueError('Managed copy is unavailable for this project and contract.')
        row = dict(row)
        contract = json.loads(row['contract_json'])
        if (contract_hash(contract) != value.contract_sha256 or contract['root'] != self.drafts.root
                or contract['project_id'] != self.drafts.project_id
                or contract['directory'] != 'jarvis-draft-' + value.materialization_id
                or contract['workspace_id'] != row['workspace_id'] or contract['revision'] != row['revision']):
            raise ValueError('Managed copy contract integrity failed.')
        if current:
            snapshot = self.drafts.inspect(DraftVersion(workspace_id=row['workspace_id'], expected_revision=row['revision']))
            manifest = [{'path': item['path'], 'sha256': item['sha256'], 'base_sha': item['base_sha']} for item in snapshot['files']]
            if snapshot['revision'] != row['revision'] or snapshot['status'] != 'open' or manifest != contract['files']:
                raise ValueError('Draft changed or closed; prepare a fresh managed copy.')
        return row, contract

    def destination(self, arguments):
        _, contract = self.load(Materialization(**{key: arguments[key] for key in ('materialization_id', 'contract_sha256')}))
        return self.files.guard.resolve(contract['directory'])

    def describe(self, value):
        row, contract = self.load(value, current=False)
        return {'materialization_id': row['materialization_id'], 'contract_sha256': row['contract_sha'],
                'status': row['status'], 'contract': contract,
                'runs': self.runs(contract['directory'])}

    def preview_copy(self, arguments):
        row, contract = self.load(Materialization(**arguments))
        if row['status'] != 'prepared':
            raise ValueError('Managed copy already claimed; inspect retained state.')
        return {'operation': 'Create an absent directory and copy only these selected draft files.',
                'directory': contract['directory'], 'workspace_id': contract['workspace_id'],
                'revision': contract['revision'], 'files': contract['files'],
                'warning': 'Creates visible untracked files inside the selected project. No automatic cleanup, dependency installation or source apply.'}

    def runs(self, directory):
        with self.drafts.records.connect() as db:
            rows = db.execute('SELECT run_id,contract_sha,status,contract_json,result_json FROM command_runs WHERE project_id=? AND root=? ORDER BY created_at DESC LIMIT 100',
                              (self.drafts.project_id, self.drafts.root)).fetchall()
        return [{'run_id': row['run_id'], 'contract_sha256': row['contract_sha'], 'status': row['status'],
                 'result': json.loads(row['result_json']) if row['result_json'] else None}
                for row in rows if json.loads(row['contract_json'])['cwd'] == directory]

    def listing(self):
        with self.drafts.records.connect() as db:
            return {'materializations': [dict(row) for row in db.execute(
                'SELECT materialization_id,contract_sha AS contract_sha256,workspace_id,revision,status,created_at FROM draft_materializations WHERE project_id=? AND root=? ORDER BY created_at DESC LIMIT 128',
                (self.drafts.project_id, self.drafts.root))]}

    def ready(self, value):
        row, contract = self.load(value)
        target = self.files.guard.root / contract['directory']
        if row['status'] != 'ready' or target.is_symlink() or target.is_junction() or not target.is_dir():
            raise ValueError('Managed copy is not ready; inspect retained data before recovery.')
        identity = target.stat()
        if (str(identity.st_dev), str(identity.st_ino)) != (row['device'], row['inode']):
            raise ValueError('Managed directory identity changed.')
        return target, contract

    def materialize(self, value):
        if os.name != 'nt':
            raise ValueError('Verified managed file creation requires Windows.')
        row, contract = self.load(value)
        target = self.destination(value.model_dump())
        if target.exists():
            raise ValueError('Managed destination already exists; no overwrite or cleanup is permitted.')
        snapshot = self.drafts.inspect(DraftVersion(workspace_id=row['workspace_id'], expected_revision=row['revision']))
        execution_dispatch_authorized.get()()
        with self.drafts.records.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("UPDATE draft_materializations SET status='materializing' WHERE materialization_id=? AND status='prepared'", (value.materialization_id,)).rowcount != 1:
                raise ValueError('Managed copy already claimed; interrupted copies cannot retry.')
        try:
            with ExitStack() as stack:
                stack.enter_context(self.files._scan(target.parent, writable=True))
                self.load(value)
                execution_dispatch_authorized.get()()
                stack.enter_context(_create_owned_directory(target))
                stack.enter_context(self.files._scan(target, writable=True))
                made = {target}
                for item in snapshot['files']:
                    relative = self.files._relative(item['path'])
                    destination = target / relative
                    for parent in reversed(destination.parents):
                        if parent == target or not parent.is_relative_to(target) or parent in made:
                            continue
                        execution_dispatch_authorized.get()()
                        self.load(value)
                        stack.enter_context(_create_owned_directory(parent))
                        stack.enter_context(self.files._scan(parent, writable=True))
                        made.add(parent)
                    self.load(value)
                    execution_dispatch_authorized.get()()
                    # Exclusive creation inside handle-pinned, newly owned directories.
                    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY, 0o600)
                    with os.fdopen(fd, 'wb') as output:
                        self.files.verify_opened(output.fileno(), destination)
                        execution_dispatch_authorized.get()()
                        output.write(item['content'].encode())
                        output.flush()
                        os.fsync(output.fileno())
                self.load(value)
                execution_dispatch_authorized.get()()
                identity = target.stat()
                with self.drafts.records.connect() as db:
                    db.execute("UPDATE draft_materializations SET status='ready',device=?,inode=? WHERE materialization_id=? AND status='materializing'",
                               (str(identity.st_dev), str(identity.st_ino), value.materialization_id))
        except BaseException:
            with self.drafts.records.connect() as db:
                db.execute("UPDATE draft_materializations SET status='interrupted' WHERE materialization_id=? AND status='materializing'", (value.materialization_id,))
            raise
        return self.describe(value)

    def prepare_command(self, value):
        target, contract = self.ready(value)
        if value.cwd != '.':
            raise ValueError('Managed commands use the exact managed directory as their working directory.')
        with self.files._scan(target):
            self.check_inputs(value)
            prepared = self.commands.prepare(PrepareCommand(executable=value.executable, arguments=value.arguments, cwd=contract['directory'],
                                                            timeout_seconds=value.timeout_seconds, output_limit=value.output_limit),
                                             managed_copy={'materialization_id': value.materialization_id, 'contract_sha256': value.contract_sha256})
        return {**prepared, 'materialization_id': value.materialization_id, 'materialization_contract_sha256': value.contract_sha256,
                'evidence_scope': 'Selected copied files only, not the full repository or installed dependencies.'}

    def check_inputs(self, value):
        target, contract = self.ready(value)
        for item in contract['files']:
            path = target / self.files._relative(item['path'])
            if path.is_symlink() or path.is_junction():
                raise ValueError('Managed input was linked or changed; prepare a fresh copy.')
            with self.files._scan(path.parent):
                if sha(self.files._text(path, 20000)) != item['sha256']:
                    raise ValueError('Managed input changed; prepare a fresh copy before another command.')

    def command(self, value):
        _, contract = self.ready(value)
        run = RunCommand(run_id=value.run_id, contract_sha256=value.run_contract_sha256)
        _, command = self.commands.load(run)
        if command['cwd'] != contract['directory'] or 'git_scope' in command:
            raise ValueError('Command belongs to a different managed directory.')
        return run, command

    async def run(self, value):
        run, _ = self.command(value)
        previous = execution_dispatch_authorized.get()
        def authorize():
            previous()
            self.ready(value)
        token = execution_dispatch_authorized.set(authorize)
        try:
            authorize()
            self.check_inputs(value)
            return await self.commands.run(run)
        finally:
            execution_dispatch_authorized.reset(token)

    def preview_run(self, arguments):
        value = MaterializedRun(**arguments)
        run, _ = self.command(value)
        self.check_inputs(value)
        return self.commands.preview(run.model_dump())

    def collect(self, value):
        target, contract = self.ready(value)
        # Collect explicit selected text only. Generated files and dependencies
        # never expand the write set, and collection never applies source edits.
        proposed = []
        with self.files._scan(target):
            contents = []
            for item in contract['files']:
                path = target / self.files._relative(item['path'])
                if path.is_symlink() or path.is_junction():
                    raise ValueError('Linked output is excluded.')
                with self.files._scan(path.parent):
                    content = self.files._text(path, 20000)
                # Reuse the strict text/credential contract before retention.
                DraftEdit(workspace_id=contract['workspace_id'], expected_revision=contract['revision'],
                          path=item['path'], before_sha256=item['sha256'], content=content)
                contents.append((item, content))
            for item, content in contents:
                if sha(content) == item['base_sha']:
                    continue
                execution_dispatch_authorized.get()()
                self.ready(value)
                if self.files.read(item['path'], 8000)['sha256'] != item['base_sha']:
                    raise ValueError('User source changed; retain managed output and review earlier unapplied proposals.')
                proposed.append(self.drafts.changes.propose(ProposeInput(path=item['path'], before_sha256=item['base_sha'], content=content)))
        return {'materialization_id': value.materialization_id, 'proposals': proposed, 'source_files_modified': False,
                'warning': 'Collected current copied text only. Command results do not prove these later bytes were tested. Partial proposal preparation never applies files.'}


def register_draft_materializations(registry, database, project):
    copies = DraftMaterializations(registry.workspace_root, database, project)
    async def prepare(value):
        return copies.prepare(value)
    async def create(value):
        return copies.materialize(value)
    async def inspect(value):
        return copies.describe(value)
    async def listing(_):
        return copies.listing()
    async def command(value):
        return copies.prepare_command(value)
    async def collect(value):
        return copies.collect(value)
    for name, schema, risk, handler, category in [
        ('draft_copy_prepare', DraftVersion, RiskLevel.LOW, prepare, ActionCategory.READ),
        ('draft_copy_create', Materialization, RiskLevel.HIGH, create, ActionCategory.EDIT),
        ('draft_copy_inspect', Materialization, RiskLevel.LOW, inspect, ActionCategory.READ),
        ('draft_copy_list', EmptyInput, RiskLevel.LOW, listing, ActionCategory.READ),
        ('draft_copy_command', MaterializedCommand, RiskLevel.LOW, command, ActionCategory.READ),
        ('draft_copy_run', MaterializedRun, RiskLevel.HIGH, copies.run, ActionCategory.COMMAND),
        ('draft_copy_collect', Materialization, RiskLevel.LOW, collect, ActionCategory.READ),
    ]:
        registry.register(Tool(name, 'Operate on selected draft file copies; commands require exact approval and are not sandboxed.',
                               schema, risk, 130 if name == 'draft_copy_run' else 15, handler, category=category,
                               resolve_destination=copies.destination if name in {'draft_copy_create', 'draft_copy_run', 'draft_copy_command'} else None,
                               preview_details=copies.preview_run if name == 'draft_copy_run' else copies.preview_copy if name == 'draft_copy_create' else None))
