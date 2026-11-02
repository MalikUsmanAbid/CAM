import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import NewProjectPanel from './NewProjectPanel'
import * as api from './api'

afterEach(() => { cleanup(); vi.restoreAllMocks() })
const approval: api.StudioResult = { status: 'approval_required', detail: '', result: null, approval: { request_id: 'grant', preview: { execution_scope: { destination: 'fixture/workspace/new' }, policy_revision: 1 } } }
async function review() {
  await act(async () => undefined)
  fireEvent.change(screen.getByLabelText('New project name'), { target: { value: 'Fixture' } })
  fireEvent.change(screen.getByLabelText('New project folder'), { target: { value: 'new' } })
  fireEvent.click(screen.getByRole('button', { name: 'Review new project' }))
  return await screen.findByRole('button', { name: 'Approve empty project creation' })
}

it('creates only after approving the actual resolved destination', async () => {
  const prepare = vi.spyOn(api, 'prepareNewProject').mockResolvedValue(approval)
  const decide = vi.spyOn(api, 'decideApproval').mockResolvedValue({} as api.ApprovalItem)
  const execute = vi.spyOn(api, 'continueNewProject').mockResolvedValue({ status: 'success', detail: '', result: { project_id: 'new' } })
  const projects = [{ project_id: 'new', name: 'Fixture', root: 'fixture/workspace/new', created_at: '' }]
  vi.spyOn(api, 'getProjects').mockResolvedValue(projects)
  const changed = vi.fn()
  render(<NewProjectPanel authenticated onCreated={changed} />)
  const button = await review()
  expect(prepare).toHaveBeenCalledWith('Fixture', 'new')
  expect(screen.getByText(/Create Fixture at fixture\/workspace\/new/)).toBeInTheDocument()
  expect(execute).not.toHaveBeenCalled()
  fireEvent.click(button)
  await screen.findByText('Empty project created. Select it in the workspace selector.')
  expect(decide).toHaveBeenCalledWith('grant', true)
  expect(execute).toHaveBeenCalledWith('grant')
  expect(changed).toHaveBeenCalledWith(projects)
})

it('rejects without continuing project creation', async () => {
  vi.spyOn(api, 'prepareNewProject').mockResolvedValue(approval)
  const decide = vi.spyOn(api, 'decideApproval').mockResolvedValue({} as api.ApprovalItem)
  const execute = vi.spyOn(api, 'continueNewProject')
  render(<NewProjectPanel authenticated onCreated={vi.fn()} />)
  await review()
  fireEvent.click(screen.getByRole('button', { name: 'Reject project creation' }))
  await screen.findByText('Project creation rejected.')
  expect(decide).toHaveBeenCalledWith('grant', false)
  expect(execute).not.toHaveBeenCalled()
})

it('does not continue an approval after the panel unmounts', async () => {
  let finish: (value: api.ApprovalItem) => void = () => undefined
  vi.spyOn(api, 'prepareNewProject').mockResolvedValue(approval)
  vi.spyOn(api, 'decideApproval').mockImplementation(() => new Promise((resolve) => { finish = resolve }))
  const execute = vi.spyOn(api, 'continueNewProject')
  const { unmount } = render(<NewProjectPanel authenticated onCreated={vi.fn()} />)
  fireEvent.click(await review())
  unmount()
  await act(async () => { finish({} as api.ApprovalItem) })
  expect(execute).not.toHaveBeenCalled()
})

it('invalidates prepared approval when the requested folder changes', async () => {
  vi.spyOn(api, 'prepareNewProject').mockResolvedValue(approval)
  const execute = vi.spyOn(api, 'continueNewProject')
  render(<NewProjectPanel authenticated onCreated={vi.fn()} />)
  await review()
  fireEvent.change(screen.getByLabelText('New project folder'), { target: { value: 'different' } })
  expect(screen.queryByRole('button', { name: 'Approve empty project creation' })).not.toBeInTheDocument()
  expect(execute).not.toHaveBeenCalled()
})
