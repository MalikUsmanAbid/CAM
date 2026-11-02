import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import CommandPanel from './CommandPanel'
import * as api from './api'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('prepares Git inspection without executing until the exact command is approved', async () => {
  const execute = vi.spyOn(api, 'executeStudioTool').mockResolvedValue({ status: 'success', detail: '', result: { run_id: 'git-run', contract_sha256: 'hash', command: { arguments: ['status'] } } })
  const consume = vi.spyOn(api, 'continueStudioTool')
  render(<CommandPanel projectId="alpha" authenticated />)
  fireEvent.click(screen.getByRole('button', { name: 'Review Git status' }))
  expect(await screen.findByRole('button', { name: 'Request command approval' })).toBeInTheDocument()
  expect(execute).toHaveBeenCalledExactlyOnceWith('studio_prepare_git', { operation: 'status' }, 'alpha')
  expect(consume).not.toHaveBeenCalled()
})

it('requires a separate exact approval and displays real failed execution evidence', async () => {
  const execute = vi.spyOn(api, 'executeStudioTool')
    .mockResolvedValueOnce({ status: 'success', detail: '', result: { run_id: 'run', contract_sha256: 'hash', command: { executable: 'fixture.exe', arguments: ['test'], cwd: '.' } } })
    .mockResolvedValueOnce({ status: 'approval_required', detail: '', result: null, approval: { request_id: 'grant', preview: { command: { arguments: ['test'] } } } })
  const approve = vi.spyOn(api, 'decideApproval').mockResolvedValue({} as api.ApprovalItem)
  const consume = vi.spyOn(api, 'continueStudioTool').mockResolvedValue({ status: 'failed', detail: 'Inspect evidence.', result: { exit_code: 7, output: '<script>failed test</script>' } })
  const { container } = render(<CommandPanel projectId="alpha" authenticated />)
  fireEvent.change(screen.getByLabelText('Command executable path'), { target: { value: 'fixture.exe' } })
  fireEvent.change(screen.getByLabelText('Command arguments JSON'), { target: { value: '["test"]' } })
  fireEvent.click(screen.getByRole('button', { name: 'Review command' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Request command approval' }))
  expect(approve).not.toHaveBeenCalled()
  fireEvent.click(await screen.findByRole('button', { name: 'Approve and run exact command' }))
  expect(await screen.findByText('Command outcome: failed. Exit code: 7.')).toBeInTheDocument()
  expect(execute).toHaveBeenLastCalledWith('studio_run_command', { run_id: 'run', contract_sha256: 'hash' }, 'alpha')
  expect(approve).toHaveBeenCalledWith('grant', true, 'alpha')
  expect(consume).toHaveBeenCalledWith('grant', 'alpha')
  expect(container.querySelector('script')).toBeNull()
})

it('discards late command evidence from a previously selected project', async () => {
  let finish: (value: api.StudioResult) => void = () => undefined
  vi.spyOn(api, 'executeStudioTool').mockImplementation(() => new Promise((resolve) => { finish = resolve }))
  const { rerender } = render(<CommandPanel key="alpha" projectId="alpha" authenticated />)
  fireEvent.change(screen.getByLabelText('Command executable path'), { target: { value: 'fixture.exe' } })
  fireEvent.click(screen.getByRole('button', { name: 'Review command' }))
  rerender(<CommandPanel key="beta" projectId="beta" authenticated />)
  await act(async () => { finish({ status: 'success', detail: '', result: { run_id: 'old', command: { private: 'alpha private command' } } }) })
  expect(screen.queryByText(/alpha private command/)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Request command approval' })).not.toBeInTheDocument()
})

it('rejects command approval without calling continuation', async () => {
  vi.spyOn(api, 'executeStudioTool')
    .mockResolvedValueOnce({ status: 'success', detail: '', result: { run_id: 'run', contract_sha256: 'hash', command: { executable: 'fixture.exe' } } })
    .mockResolvedValueOnce({ status: 'approval_required', detail: '', result: null, approval: { request_id: 'grant', preview: {} } })
  const decide = vi.spyOn(api, 'decideApproval').mockResolvedValue({} as api.ApprovalItem)
  const consume = vi.spyOn(api, 'continueStudioTool')
  render(<CommandPanel projectId="alpha" authenticated />)
  fireEvent.change(screen.getByLabelText('Command executable path'), { target: { value: 'fixture.exe' } })
  fireEvent.click(screen.getByRole('button', { name: 'Review command' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Request command approval' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Reject command approval' }))
  expect(await screen.findByText('Approval rejected. No command was dispatched.')).toBeInTheDocument()
  expect(decide).toHaveBeenCalledWith('grant', false, 'alpha')
  expect(consume).not.toHaveBeenCalled()
})
