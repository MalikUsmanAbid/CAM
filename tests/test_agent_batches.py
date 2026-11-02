"""Coordinated proposal graph regressions. Written for the deferred isolated run."""

import hashlib

from fastapi.testclient import TestClient

from services.agent_api.app.coding_changes import CodingChanges, ProposeInput
from services.agent_api.app.main import app
from services.agent_api.app.project_runtime import runtime_for


def test_role_graph_executes_real_proposal_and_reviewer_reads_stored_diff(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    before = b'def add(a, b):\n    return a - b\n'
    after = 'def add(a, b):\n    return a + b\n'
    (root / 'math.py').write_bytes(before)
    (root / 'unrelated.txt').write_bytes(b'User edit stays intact.\n')
    calls = []
    with TestClient(app) as client:
        project = client.post('/api/v1/projects', json={'name': 'Fixture', 'root': str(root)}).json()['project_id']
        runtime = runtime_for(app, project)
        async def analyze(value):
            calls.append(value.message)
            return {'message': 'Fixture analysis; no tests executed.', 'provider': 'fixture', 'model': 'fixture'}
        async def propose(value):
            calls.append('actual_coding_proposal')
            return CodingChanges(runtime.root, runtime.memory.database_path, project).propose(
                ProposeInput(path=value.path, before_sha256=value.before_sha256, content=after))
        runtime.executor.registry.get('studio_project_chat').handler = analyze
        coder = runtime.executor.registry.get('studio_ai_propose')
        coder.enabled = True
        coder.handler = propose
        created = client.post('/api/v1/workspace/agent-batches?project_id=' + project, json={
            'path': 'math.py', 'before_sha256': hashlib.sha256(before).hexdigest(), 'request': 'Correct addition.', 'context_paths': [],
        }).json()
        assert len(created['jobs']) == 4
        assert created['jobs'][2]['dependencies'] == [created['jobs'][0]['job_id'], created['jobs'][1]['job_id']]
        result = client.post(f"/api/v1/workspace/agent-batches/{created['batch_id']}/run?project_id={project}")
        assert result.status_code == 200
        assert result.json()['status'] == 'completed'
        assert all(job['status'] == 'completed' for job in result.json()['jobs'])
        assert calls.count('actual_coding_proposal') == 1
        reviewer = next(message for message in calls if message.startswith('Role: reviewer.'))
        assert 'return a - b' in reviewer and 'return a + b' in reviewer
        assert (root / 'math.py').read_bytes() == before
        assert (root / 'unrelated.txt').read_bytes() == b'User edit stays intact.\n'
        assert client.post(f"/api/v1/workspace/agent-batches/{created['batch_id']}/run?project_id={project}").status_code == 400
        assert calls.count('actual_coding_proposal') == 1


def test_cancelled_graph_does_not_dispatch_any_role(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    with TestClient(app) as client:
        project = client.post('/api/v1/projects', json={'name': 'Fixture', 'root': str(root)}).json()['project_id']
        runtime = runtime_for(app, project)
        calls = []
        async def forbidden(value):
            calls.append(value)
            raise AssertionError('Cancelled work must not execute.')
        runtime.executor.registry.get('studio_project_chat').handler = forbidden
        created = client.post('/api/v1/workspace/agent-batches?project_id=' + project, json={
            'path': 'math.py', 'before_sha256': 'a' * 64, 'request': 'Prepare a proposal.', 'context_paths': [],
        }).json()
        endpoint = f"/api/v1/workspace/agent-batches/{created['batch_id']}"
        assert client.post(endpoint + '/cancel?project_id=' + project).status_code == 200
        assert client.post(endpoint + '/run?project_id=' + project).status_code == 400
        assert calls == []
