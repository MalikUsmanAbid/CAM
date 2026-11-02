import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import DraftCommandPanel from './DraftCommandPanel'
import { workspaceRequest } from './api'

vi.mock('./api', () => ({ getSessionEpoch: () => 1, workspaceRequest: vi.fn() }))
const request = vi.mocked(workspaceRequest)
const id = 'a'.repeat(32)
const hash = 'b'.repeat(64)
beforeEach(() => request.mockReset())
afterEach(() => cleanup())

it('prepares copies without automatically creating files or running commands', async () => {
  request.mockResolvedValue({ status: 'success', result: { materialization_id: id, contract_sha256: hash, status: 'prepared' } })
  render(<DraftCommandPanel projectId="A" workspaceId={'c'.repeat(32)} revision={4} />)
  fireEvent.click(screen.getByRole('button', { name: 'Prepare managed file copy' }))
  await screen.findByText('Copy status: prepared')
  expect(request).toHaveBeenCalledTimes(1)
  expect(request).toHaveBeenCalledWith('/knowledge/execute', 'POST', {
    tool: 'draft_copy_prepare', arguments: { workspace_id: 'c'.repeat(32), expected_revision: 4 },
  }, 'A')
  fireEvent.click(screen.getByRole('button', { name: 'Request file-copy approval' }))
  await waitFor(() => expect(request).toHaveBeenLastCalledWith('/knowledge/execute', 'POST', {
    tool: 'draft_copy_create', arguments: { materialization_id: id, contract_sha256: hash },
  }, 'A'))
})

it('uses the inspected copy contract and separate exact command approval', async () => {
  request.mockResolvedValueOnce({ status: 'success', result: { materialization_id: id, contract_sha256: hash, status: 'ready' } })
  render(<DraftCommandPanel projectId="A" workspaceId={'c'.repeat(32)} revision={4} />)
  fireEvent.change(screen.getByLabelText('Managed copy ID'), { target: { value: id } })
  fireEvent.change(screen.getByLabelText('Managed copy contract hash'), { target: { value: hash } })
  fireEvent.click(screen.getByRole('button', { name: 'Inspect copy and command history' }))
  await screen.findByText('Copy status: ready')
  fireEvent.change(screen.getByLabelText('Command executable (absolute .exe path)'), { target: { value: 'C:/fixtures/python.exe' } })
  fireEvent.change(screen.getByLabelText('Command arguments (JSON string array)'), { target: { value: '["-I","example.py"]' } })
  request.mockResolvedValueOnce({ status: 'success', result: { run_id: 'd'.repeat(32), contract_sha256: 'e'.repeat(64) } })
  fireEvent.click(screen.getByRole('button', { name: 'Prepare exact managed command' }))
  await screen.findByRole('button', { name: 'Request command approval' })
  expect(request).toHaveBeenLastCalledWith('/knowledge/execute', 'POST', {
    tool: 'draft_copy_command', arguments: { materialization_id: id, contract_sha256: hash,
      executable: 'C:/fixtures/python.exe', arguments: ['-I', 'example.py'], cwd: '.', timeout_seconds: 60, output_limit: 32000 },
  }, 'A')
  request.mockResolvedValueOnce({ status: 'approval_required' })
  fireEvent.click(screen.getByRole('button', { name: 'Request command approval' }))
  await waitFor(() => expect(request).toHaveBeenLastCalledWith('/knowledge/execute', 'POST', {
    tool: 'draft_copy_run', arguments: { materialization_id: id, contract_sha256: hash, run_id: 'd'.repeat(32), run_contract_sha256: 'e'.repeat(64) },
  }, 'A'))
})
