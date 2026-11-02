import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import RecoveryPanel from './RecoveryPanel'
import * as api from './api'

afterEach(() => { cleanup(); vi.restoreAllMocks() })
const change = { change_id: 'checkpoint', path: 'main.py', proposal_sha256: 'proposal', status: 'applying', checkpoint_locations: ['.jarvis-backup-fixture-apply'] }
const observation = { observation_sha256: 'observation', mode: 'restore_missing', final_status: 'proposed', retained_checkpoint: '.jarvis-backup-fixture-apply', diff: '+original source' }

it('inspects the actual checkpoint and approves its exact observed recovery separately', async () => {
  const execute = vi.spyOn(api, 'executeStudioTool')
    .mockResolvedValueOnce({ status: 'success', detail: '', result: { changes: [change] } })
    .mockResolvedValueOnce({ status: 'success', detail: '', result: observation })
    .mockResolvedValueOnce({ status: 'approval_required', detail: '', result: null, approval: { request_id: 'grant', preview: { policy_revision: 1 } } })
  const approve = vi.spyOn(api, 'decideApproval').mockResolvedValue({} as api.ApprovalItem)
  const consume = vi.spyOn(api, 'continueStudioTool').mockResolvedValue({ status: 'success', detail: '', result: { status: 'proposed' } })
  render(<RecoveryPanel projectId="alpha" authenticated />)
  await act(async () => undefined)
  fireEvent.click(screen.getByRole('button', { name: 'Load checkpoint history' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Inspect interrupted main.py' }))
  expect(await screen.findByText('+original source')).toBeInTheDocument()
  expect(approve).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Request recovery approval' }))
  expect(execute).toHaveBeenLastCalledWith('studio_recover', { change_id: 'checkpoint', path: 'main.py', proposal_sha256: 'proposal', observation_sha256: 'observation' }, 'alpha')
  fireEvent.click(await screen.findByRole('button', { name: 'Approve exact recovery' }))
  expect(await screen.findByText('Recovery verified. Checkpoint state: proposed.')).toBeInTheDocument()
  expect(approve).toHaveBeenCalledWith('grant', true, 'alpha')
  expect(consume).toHaveBeenCalledWith('grant', 'alpha')
})

it('does not continue recovery after unmount while approving', async () => {
  let finish: (value: api.ApprovalItem) => void = () => undefined
  vi.spyOn(api, 'executeStudioTool').mockResolvedValue({ status: 'approval_required', detail: '', result: null, approval: { request_id: 'read', preview: {} } })
  vi.spyOn(api, 'decideApproval').mockImplementation(() => new Promise((resolve) => { finish = resolve }))
  const consume = vi.spyOn(api, 'continueStudioTool')
  const { unmount } = render(<RecoveryPanel projectId="alpha" authenticated />)
  await act(async () => undefined)
  fireEvent.click(screen.getByRole('button', { name: 'Load checkpoint history' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Approve checkpoint read' }))
  unmount()
  await act(async () => { finish({} as api.ApprovalItem) })
  expect(consume).not.toHaveBeenCalled()
})

it('rejects a pending checkpoint action without invoking the executor', async () => {
  vi.spyOn(api, 'executeStudioTool').mockResolvedValue({ status: 'approval_required', detail: '', result: null, approval: { request_id: 'read', preview: {} } })
  const approve = vi.spyOn(api, 'decideApproval').mockResolvedValue({} as api.ApprovalItem)
  const consume = vi.spyOn(api, 'continueStudioTool')
  render(<RecoveryPanel projectId="alpha" authenticated />)
  await act(async () => undefined)
  fireEvent.click(screen.getByRole('button', { name: 'Load checkpoint history' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Reject checkpoint action' }))
  expect(await screen.findByText('Recovery action rejected.')).toBeInTheDocument()
  expect(approve).toHaveBeenCalledWith('read', false, 'alpha')
  expect(consume).not.toHaveBeenCalled()
})

it('reopens the actual selected file checkpoint without applying any file', async () => {
  const reviewed = { status: 'success', detail: '', result: { path: 'main.py', content: 'actual proposed source', before_content: 'original source', status: 'proposed' } }
  const execute = vi.spyOn(api, 'executeStudioTool')
    .mockResolvedValueOnce({ status: 'success', detail: '', result: { changes: [{ ...change, status: 'proposed' }] } })
    .mockResolvedValueOnce(reviewed)
  const display = vi.fn()
  render(<RecoveryPanel projectId="alpha" authenticated onReview={display} />)
  await act(async () => undefined)
  fireEvent.click(screen.getByRole('button', { name: 'Load checkpoint history' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Review main.py checkpoint checkpoi' }))
  await act(async () => undefined)
  expect(execute).toHaveBeenLastCalledWith('studio_inspect_change', { change_id: 'checkpoint', path: 'main.py', proposal_sha256: 'proposal' }, 'alpha')
  expect(display).toHaveBeenCalledWith(reviewed, 0)
  expect(execute).not.toHaveBeenCalledWith('studio_apply', expect.anything(), expect.anything())
})
