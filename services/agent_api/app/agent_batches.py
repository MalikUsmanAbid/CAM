"""Bounded automatic role graphs. Completion means proposals, never applied code."""

import asyncio
import json
import os
import uuid

from services.agent_api.app.agent_workspace import ROLE_TOOLS, AgentJobInput
from services.agent_api.app.project_files import ProjectFiles
from services.agent_api.app.project_runtime import runtime_for
from services.agent_api.app.session_security import execution_dispatch_authorized
from services.agent_api.app.studio_ai import AIProposalInput
from services.agent_api.app.usage_policy import budget_task
from services.agent_api.app.workspace_records import WorkspaceRecords, now


class _PauseBatch(Exception):
    pass


class AgentBatches:
    def __init__(self, app):
        self.app = app
        self.records = WorkspaceRecords(app.state.settings.database_path)

    def create(self, project_id, value: AIProposalInput):
        runtime = runtime_for(self.app, project_id)
        if project_id is None:
            raise ValueError("Coordinated work requires a registered project.")
        identifier = uuid.uuid4().hex
        roles = ['coordinator', 'researcher', 'coder', 'reviewer']
        ids = {role: uuid.uuid4().hex for role in roles}
        requests = {
            'coordinator': {'message': 'Plan the requested bounded coding change to ' + value.path + '. No authority to execute commands or apply edits. Request: ' + value.request},
            'researcher': {'message': 'Identify project constraints and missing evidence for ' + value.path + '. Do not claim live web research occurred. Request: ' + value.request},
            'coder': value.model_dump(),
            'reviewer': {'message': 'Review the actual proposed change and recorded command evidence. Identify omissions and missing tests; do not infer success from passing counts or another role\'s prose. Request: ' + value.request},
        }
        dependencies = {'coordinator': [], 'researcher': [], 'coder': [ids['coordinator'], ids['researcher']], 'reviewer': [ids['coder']]}
        prepared = []
        for role in roles:
            normalized = runtime.executor.registry.validate_input(ROLE_TOOLS[role], requests[role]).model_dump()
            AgentJobInput(role=role, arguments=normalized, dependencies=dependencies[role])
            paths = []
            if role == 'coder':
                files = ProjectFiles(runtime.root)
                paths = sorted({os.path.normcase(files._relative(path)) for path in [value.path, *value.context_paths]})
            prepared.append((ids[role], project_id, str(runtime.root), role, json.dumps(normalized), json.dumps(dependencies[role]), json.dumps(paths), 'queued', None, None, now(), now()))
        with self.records.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            pending = db.execute("SELECT COUNT(*) FROM agent_jobs WHERE project_id=? AND status IN ('queued','running','waiting_for_approval')", (project_id,)).fetchone()[0]
            if pending + len(prepared) > 100:
                raise ValueError("Project queue capacity exceeded.")
            db.execute("INSERT INTO agent_batches VALUES (?,?,?,?,?,?)", (identifier, project_id, str(runtime.root), 'queued', now(), now()))
            db.executemany("INSERT INTO agent_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", prepared)
            db.executemany("INSERT INTO agent_batch_jobs VALUES (?,?,?)", [(identifier, ids[role], index) for index, role in enumerate(roles)])
        return self.get(project_id, identifier)

    def get(self, project_id, batch_id):
        with self.records.connect() as db:
            row = db.execute("SELECT * FROM agent_batches WHERE project_id=? AND batch_id=?", (project_id, batch_id)).fetchone()
            if row is None:
                raise KeyError(batch_id)
            members = [item[0] for item in db.execute("SELECT job_id FROM agent_batch_jobs WHERE batch_id=? ORDER BY position", (batch_id,))]
        return {**dict(row), 'jobs': [self.app.state.agent_workspace.get(project_id, job_id) for job_id in members],
                'output_kind': 'analysis and patch proposals; no file apply, test/build execution or verified completion'}

    def list(self, project_id):
        with self.records.connect() as db:
            ids = [row[0] for row in db.execute("SELECT batch_id FROM agent_batches WHERE project_id=? ORDER BY created_at DESC LIMIT 20", (project_id,))]
        return [self.get(project_id, identifier) for identifier in ids]

    def recover(self):
        with self.records.connect() as db:
            db.execute("UPDATE agent_batches SET status='interrupted',updated_at=? WHERE status='running'", (now(),))

    async def run(self, project_id, batch_id):
        runtime = runtime_for(self.app, project_id)
        with self.records.connect() as db:
            if db.execute("UPDATE agent_batches SET status='running',updated_at=? WHERE batch_id=? AND project_id=? AND root=? AND status IN ('queued','paused','interrupted')", (now(), batch_id, project_id, str(runtime.root))).rowcount != 1:
                raise ValueError("Batch is already claimed, completed, cancelled or outside this project root.")
        previous = execution_dispatch_authorized.get()
        def authorize():
            previous()
            if self.get(project_id, batch_id)['status'] != 'running':
                raise ValueError("Coordinated work stopped before dispatch.")
        token = execution_dispatch_authorized.set(authorize)
        budget = budget_task.set(batch_id)
        status = 'interrupted'
        try:
            # The first two independent roles can overlap while sharing the
            # existing inference queue. Dependent coder/reviewer jobs serialize.
            snapshot = self.get(project_id, batch_id)
            first = [job for job in snapshot['jobs'][:2] if job['status'] in ('queued', 'interrupted')]
            if first:
                outcomes = await asyncio.gather(*(self.app.state.agent_workspace.run(project_id, job['job_id']) for job in first), return_exceptions=True)
                if any(isinstance(result, BaseException) for result in outcomes):
                    raise _PauseBatch
            for job in self.get(project_id, batch_id)['jobs']:
                authorize()
                if job['status'] == 'completed':
                    continue
                if job['status'] not in ('queued', 'interrupted'):
                    raise _PauseBatch
                result = await self.app.state.agent_workspace.run(project_id, job['job_id'])
                if result['status'] != 'completed':
                    raise _PauseBatch
            status = 'completed'
        except _PauseBatch:
            status = 'paused'
        finally:
            execution_dispatch_authorized.reset(token)
            budget_task.reset(budget)
            with self.records.connect() as db:
                db.execute("UPDATE agent_batches SET status=?,updated_at=? WHERE batch_id=? AND status='running'", (status, now(), batch_id))
        return self.get(project_id, batch_id)

    def cancel(self, project_id, batch_id):
        batch = self.get(project_id, batch_id)
        with self.records.connect() as db:
            if db.execute("UPDATE agent_batches SET status='cancelled',updated_at=? WHERE batch_id=? AND project_id=? AND status IN ('queued','paused','interrupted','running')", (now(), batch_id, project_id)).rowcount != 1:
                raise ValueError("Batch is no longer cancellable.")
        for job in batch['jobs']:
            if job['status'] in ('queued', 'interrupted', 'running', 'waiting_for_approval'):
                self.app.state.agent_workspace.cancel(project_id, job['job_id'])
