import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import SkillLifecyclePanel from './SkillLifecyclePanel'
import * as api from './api'

afterEach(() => { cleanup(); vi.restoreAllMocks() })
const hash = 'a'.repeat(64)
const inspected = { skill_id: 'project_reader', version: '1.0.0', manifest_sha256: hash, manifest: { instructions: 'Read selected project files.' } }

it('binds uninstall to the inspected version and hash and requires separate approval', async () => {
  const request = vi.spyOn(api, 'workspaceRequest')
    .mockResolvedValueOnce({ status: 'success', detail: '', result: inspected })
    .mockResolvedValueOnce({ status: 'approval_required', detail: '', result: null, approval: { request_id: 'grant', preview: { version: '1.0.0', manifest_sha256: hash } } })
  const approve = vi.spyOn(api, 'decideApproval').mockResolvedValue({} as api.ApprovalItem)
  const consume = vi.spyOn(api, 'continueStudioTool').mockResolvedValue({ status: 'success', detail: '', result: { status: 'uninstalled' } })
  const changed = vi.fn()
  render(<SkillLifecyclePanel projectId="alpha" skillId="project_reader" onChanged={changed} />)
  fireEvent.click(screen.getByRole('button', { name: 'Inspect installed manifest' }))
  const uninstall = await screen.findByRole('button', { name: 'Request exact uninstall approval' })
  expect(uninstall).toBeDisabled()
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(uninstall)
  await screen.findByRole('button', { name: 'Approve skill action' })
  expect(request).toHaveBeenLastCalledWith('/knowledge/execute', 'POST', { tool: 'skill_uninstall', arguments: {
    skill_id: 'project_reader', expected_version: '1.0.0', manifest_sha256: hash, confirm_uninstall: true,
  } }, 'alpha')
  expect(approve).not.toHaveBeenCalled()
  expect(consume).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Approve skill action' }))
  await waitFor(() => expect(changed).toHaveBeenCalledTimes(1))
  expect(approve).toHaveBeenCalledWith('grant', true, 'alpha')
  expect(consume).toHaveBeenCalledWith('grant', 'alpha')
})

it('rejecting an uninstall never invokes the executor continuation', async () => {
  vi.spyOn(api, 'workspaceRequest')
    .mockResolvedValueOnce({ status: 'success', detail: '', result: inspected })
    .mockResolvedValueOnce({ status: 'approval_required', detail: '', result: null, approval: { request_id: 'grant', preview: {} } })
  const approve = vi.spyOn(api, 'decideApproval').mockResolvedValue({} as api.ApprovalItem)
  const consume = vi.spyOn(api, 'continueStudioTool')
  const changed = vi.fn()
  render(<SkillLifecyclePanel projectId="alpha" skillId="project_reader" onChanged={changed} />)
  fireEvent.click(screen.getByRole('button', { name: 'Inspect installed manifest' }))
  await screen.findByRole('checkbox')
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: 'Request exact uninstall approval' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Reject skill action' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Reject skill action' })).not.toBeInTheDocument())
  expect(approve).toHaveBeenCalledWith('grant', false, 'alpha')
  expect(consume).not.toHaveBeenCalled()
  expect(changed).not.toHaveBeenCalled()
})
