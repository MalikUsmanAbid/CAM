import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import LibraryBrowser from './LibraryBrowser'
import * as api from './api'

afterEach(() => { cleanup(); vi.restoreAllMocks() })
const id = 'a'.repeat(32), hash = 'b'.repeat(64)
const snapshot = { document_id: id, title: 'Saved source', source_path: 'source.txt', source_sha: hash, content: 'Snapshot text' }

it('requires exact snapshot consent and a separate approval before forgetting', async () => {
  const execute = vi.spyOn(api, 'workspaceRequest')
    .mockResolvedValueOnce({ status: 'success', detail: '', result: { documents: [snapshot] } })
    .mockResolvedValueOnce({ status: 'success', detail: '', result: snapshot })
    .mockResolvedValueOnce({ status: 'approval_required', detail: '', result: null, approval: { request_id: 'grant', preview: { document_id: id, source_sha256: hash } } })
  const approve = vi.spyOn(api, 'decideApproval').mockResolvedValue({} as api.ApprovalItem)
  const consume = vi.spyOn(api, 'continueStudioTool').mockResolvedValue({ status: 'success', detail: '', result: { status: 'forgotten', document_id: id } })
  render(<LibraryBrowser projectId="alpha" />)
  fireEvent.click(screen.getByRole('button', { name: 'Load saved sources' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Saved source' }))
  expect(await screen.findByText('Snapshot text')).toBeInTheDocument()
  const removal = screen.getByRole('button', { name: 'Request snapshot removal approval' })
  expect(removal).toBeDisabled()
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(removal)
  await screen.findByRole('button', { name: 'Approve library action' })
  expect(execute).toHaveBeenLastCalledWith('/knowledge/execute', 'POST', { tool: 'knowledge_forget', arguments: { document_id: id, source_sha256: hash, confirm_forget: true } }, 'alpha')
  expect(approve).not.toHaveBeenCalled()
  expect(consume).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Approve library action' }))
  await screen.findByRole('status')
  expect(approve).toHaveBeenCalledWith('grant', true, 'alpha')
  expect(consume).toHaveBeenCalledWith('grant', 'alpha')
  expect(screen.queryByText('Snapshot text')).not.toBeInTheDocument()
})

it('ignores a late read when the session changes on the same mounted panel', async () => {
  let resolve!: (value: unknown) => void
  const epoch = vi.spyOn(api, 'getSessionEpoch').mockReturnValue(1)
  vi.spyOn(api, 'workspaceRequest').mockImplementationOnce(() => new Promise((accept) => { resolve = accept }))
  render(<LibraryBrowser projectId="alpha" />)
  fireEvent.click(screen.getByRole('button', { name: 'Load saved sources' }))
  epoch.mockReturnValue(2)
  await act(async () => resolve({ status: 'success', detail: '', result: { documents: [snapshot] } }))
  expect(screen.queryByRole('button', { name: 'Saved source' })).not.toBeInTheDocument()
})

it('retains the first source page when the next page requires approval', async () => {
  const request = vi.spyOn(api, 'workspaceRequest')
    .mockResolvedValueOnce({ status: 'success', detail: '', result: { documents: [snapshot], next_cursor: 'observed-cursor' } })
    .mockResolvedValueOnce({ status: 'approval_required', detail: '', result: null, approval: { request_id: 'page-grant', preview: { cursor: 'observed-cursor' } } })
  vi.spyOn(api, 'decideApproval').mockResolvedValue({} as api.ApprovalItem)
  vi.spyOn(api, 'continueStudioTool').mockResolvedValue({ status: 'success', detail: '', result: {
    documents: [{ ...snapshot, document_id: 'c'.repeat(32), title: 'Older source' }], next_cursor: null,
  } })
  render(<LibraryBrowser projectId="alpha" />)
  fireEvent.click(screen.getByRole('button', { name: 'Load saved sources' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Load more saved sources' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Approve library action' }))
  await screen.findByRole('button', { name: 'Older source' })
  expect(screen.getByRole('button', { name: 'Saved source' })).toBeInTheDocument()
  expect(request).toHaveBeenLastCalledWith('/knowledge/execute', 'POST', { tool: 'knowledge_document_page', arguments: { cursor: 'observed-cursor' } }, 'alpha')
})

it('clears an old page cursor when the text filter changes', async () => {
  const request = vi.spyOn(api, 'workspaceRequest').mockResolvedValueOnce({ status: 'success', detail: '', result: { documents: [snapshot], next_cursor: 'old-cursor' } })
    .mockResolvedValueOnce({ status: 'success', detail: '', result: { documents: [], next_cursor: null } })
  render(<LibraryBrowser projectId="alpha" />)
  fireEvent.click(screen.getByRole('button', { name: 'Load saved sources' }))
  await screen.findByRole('button', { name: 'Load more saved sources' })
  fireEvent.change(screen.getByLabelText('Filter saved library text'), { target: { value: 'Other text' } })
  expect(screen.queryByRole('button', { name: 'Load more saved sources' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Load saved sources' }))
  await act(async () => undefined)
  expect(request).toHaveBeenLastCalledWith('/knowledge/execute', 'POST', { tool: 'knowledge_document_page', arguments: { query: 'Other text' } }, 'alpha')
})
