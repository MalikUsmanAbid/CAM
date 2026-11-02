import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import AgentDraftPanel from './AgentDraftPanel'
import { workspaceRequest } from './api'

vi.mock('./api', () => ({ getSessionEpoch: () => 1, workspaceRequest: vi.fn() }))
const request = vi.mocked(workspaceRequest)
beforeEach(() => { request.mockReset(); request.mockResolvedValue({ status: 'approval_required' }) })
afterEach(() => cleanup())

it('includes multiple explicitly inspected source files in one snapshot request', async () => {
  render(<AgentDraftPanel projectId="A" />)
  fireEvent.change(screen.getByLabelText('Snapshot source path'), { target: { value: 'math.py' } })
  fireEvent.change(screen.getByLabelText('Inspected source hash'), { target: { value: 'a'.repeat(64) } })
  fireEvent.click(screen.getByRole('button', { name: 'Add source to snapshot' }))
  fireEvent.change(screen.getByLabelText('Snapshot source path'), { target: { value: 'test_math.py' } })
  fireEvent.change(screen.getByLabelText('Inspected source hash'), { target: { value: 'b'.repeat(64) } })
  fireEvent.click(screen.getByRole('button', { name: 'Request isolated snapshot' }))
  await waitFor(() => expect(request).toHaveBeenCalledWith('/knowledge/execute', 'POST', {
    tool: 'draft_create', arguments: { files: [
      { path: 'math.py', source_sha256: 'a'.repeat(64) },
      { path: 'test_math.py', source_sha256: 'b'.repeat(64) },
    ] },
  }, 'A'))
})

it('creates an isolated snapshot through its hash-bound tool approval contract', async () => {
  render(<AgentDraftPanel projectId="A" />)
  fireEvent.change(screen.getByLabelText('Snapshot source path'), { target: { value: 'math.py' } })
  fireEvent.change(screen.getByLabelText('Inspected source hash'), { target: { value: 'a'.repeat(64) } })
  fireEvent.click(screen.getByRole('button', { name: 'Request isolated snapshot' }))
  await waitFor(() => expect(request).toHaveBeenCalledWith('/knowledge/execute', 'POST', {
    tool: 'draft_create', arguments: { files: [{ path: 'math.py', source_sha256: 'a'.repeat(64) }] },
  }, 'A'))
})

it('queues the coder against the inspected virtual revision instead of a real working-tree edit', async () => {
  const workspace = 'b'.repeat(32)
  request.mockResolvedValue({ status: 'success', result: {
    workspace_id: workspace, revision: 7, status: 'open',
    files: [{ path: 'math.py', base_sha: 'c'.repeat(64), sha256: 'd'.repeat(64), content: 'draft content', changed: true }],
  } })
  render(<AgentDraftPanel projectId="A" />)
  fireEvent.change(screen.getByLabelText('Draft workspace ID'), { target: { value: workspace } })
  fireEvent.click(screen.getByRole('button', { name: 'Inspect draft workspace' }))
  await screen.findByText('Revision 7 · open')
  fireEvent.change(screen.getByLabelText('Model correction request'), { target: { value: 'Fix addition' } })
  request.mockResolvedValue({ status: 'queued' })
  fireEvent.click(screen.getByRole('button', { name: 'Queue coder in isolated draft' }))
  await waitFor(() => expect(request).toHaveBeenCalledWith('/agents', 'POST', {
    role: 'coder', arguments: { workspace_id: workspace, expected_revision: 7, path: 'math.py', before_sha256: 'd'.repeat(64), request: 'Fix addition' }, dependencies: [],
  }, 'A'))
})
