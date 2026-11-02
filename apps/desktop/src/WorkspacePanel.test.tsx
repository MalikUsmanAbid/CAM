import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import WorkspacePanel from './WorkspacePanel'
import ConnectionsPanel from './ConnectionsPanel'
import { workspaceRequest } from './api'

vi.mock('./api', () => ({ getSessionEpoch: () => 1, workspaceRequest: vi.fn(), oauthRequest: vi.fn() }))
const request = vi.mocked(workspaceRequest)
beforeEach(() => { request.mockReset(); request.mockResolvedValue([]) })
afterEach(() => { cleanup() })

it('queues the selected coder with the inspected source contract rather than a generic chat request', async () => {
  render(<WorkspacePanel section="agents" projectId="A" authenticated />)
  fireEvent.change(screen.getByLabelText('Role'), { target: { value: 'coder' } })
  fireEvent.change(screen.getByLabelText('Request'), { target: { value: 'Fix subtraction' } })
  fireEvent.change(screen.getByLabelText('Inspected file path'), { target: { value: 'math.py' } })
  fireEvent.change(screen.getByLabelText('Inspected file SHA-256'), { target: { value: 'a'.repeat(64) } })
  fireEvent.click(screen.getByRole('button', { name: 'Queue role job' }))
  await waitFor(() => expect(request).toHaveBeenCalledWith('/agents', 'POST', {
    role: 'coder', arguments: { path: 'math.py', before_sha256: 'a'.repeat(64), request: 'Fix subtraction', context_paths: [] }, dependencies: [],
  }, 'A'))
})

it('records an explicit local reminder as a timezone-aware instant without an executable action', async () => {
  request.mockImplementation(async <T,>(path: string) => (path === '/notifications' ? { items: [] } : []) as T)
  render(<WorkspacePanel section="decisions" projectId="A" authenticated />)
  fireEvent.change(screen.getByLabelText('Reminder'), { target: { value: 'Review progress' } })
  fireEvent.change(screen.getByLabelText('Local date and time'), { target: { value: '2030-01-02T10:00' } })
  fireEvent.click(screen.getByRole('button', { name: 'Schedule reminder' }))
  await waitFor(() => expect(request).toHaveBeenCalledWith('/reminders', 'POST', {
    title: 'Review progress', kind: 'reminder', due_at: new Date('2030-01-02T10:00').toISOString(), interval_seconds: null,
  }, 'A'))
})

it('requires explicit consent before connecting an opaque protected account reference', async () => {
  request.mockResolvedValue({ accounts: [] })
  render(<ConnectionsPanel projectId={null} authenticated />)
  await screen.findByText('Connected accounts')
  fireEvent.change(screen.getByLabelText('Account label'), { target: { value: 'GitHub fixture' } })
  fireEvent.change(screen.getByLabelText('Existing backend-protected credential reference'), { target: { value: 'vault:' + 'a'.repeat(32) } })
  expect(screen.getByRole('button', { name: 'Connect configured token' })).toBeDisabled()
  fireEvent.click(screen.getByLabelText(/I consent to separately approved payloads/))
  fireEvent.click(screen.getByRole('button', { name: 'Connect configured token' }))
  await waitFor(() => expect(request).toHaveBeenCalledWith('/accounts', 'POST', {
    provider: 'github', label: 'GitHub fixture', secret_reference: 'vault:' + 'a'.repeat(32), scopes: ['issues:write'], transmission_consent: true,
  }))
})

it('routes a DOCX import to its exact hash-bound executor contract', async () => {
  render(<WorkspacePanel section="library" projectId="A" authenticated />)
  fireEvent.change(screen.getByLabelText('Source format'), { target: { value: 'docx' } })
  fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Source document' } })
  fireEvent.change(screen.getByLabelText('Project-relative source'), { target: { value: 'source.docx' } })
  fireEvent.change(screen.getByLabelText('Source SHA-256'), { target: { value: 'b'.repeat(64) } })
  fireEvent.click(screen.getByRole('button', { name: 'Request import approval' }))
  await waitFor(() => expect(request).toHaveBeenCalledWith('/knowledge/execute', 'POST', {
    tool: 'knowledge_import_docx', arguments: { path: 'source.docx', title: 'Source document', source_sha256: 'b'.repeat(64) },
  }, 'A'))
})

it('requests the exact research URL rather than reporting local echo as web execution', async () => {
  render(<WorkspacePanel section="library" projectId="A" authenticated />)
  fireEvent.change(screen.getByLabelText('Source title'), { target: { value: 'Official source' } })
  fireEvent.change(screen.getByLabelText('Exact public HTTPS URL'), { target: { value: 'https://example.com/docs' } })
  fireEvent.click(screen.getByRole('button', { name: 'Request retrieval approval' }))
  await waitFor(() => expect(request).toHaveBeenCalledWith('/knowledge/execute', 'POST', {
    tool: 'research_fetch', arguments: { url: 'https://example.com/docs', title: 'Official source' },
  }, 'A'))
})
