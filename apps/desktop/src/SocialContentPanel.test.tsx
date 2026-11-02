import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import SocialContentPanel from './SocialContentPanel'
import { workspaceRequest } from './api'

vi.mock('./api', () => ({ getSessionEpoch: () => 1, workspaceRequest: vi.fn() }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('stores distinct selected platform drafts through the scoped executor without requesting publication', async () => {
  vi.mocked(workspaceRequest).mockResolvedValue({ status: 'approval_required' })
  render(<SocialContentPanel projectId="A" />)
  fireEvent.change(screen.getByLabelText('Campaign title'), { target: { value: 'Fixture campaign' } })
  fireEvent.change(screen.getByLabelText('linkedin draft'), { target: { value: 'Member variant' } })
  fireEvent.click(screen.getByLabelText('instagram'))
  fireEvent.change(screen.getByLabelText('instagram draft'), { target: { value: 'Photo caption draft' } })
  fireEvent.click(screen.getByRole('button', { name: 'Request campaign storage approval' }))
  await waitFor(() => expect(workspaceRequest).toHaveBeenCalledWith('/knowledge/execute', 'POST', {
    tool: 'social_prepare', arguments: { title: 'Fixture campaign', variants: [
      { platform: 'linkedin', text: 'Member variant', media_notes: '' },
      { platform: 'instagram', text: 'Photo caption draft', media_notes: '' },
    ], sources: [], previous_id: null },
  }, 'A'))
  expect(vi.mocked(workspaceRequest).mock.calls.every((call) => call[0] !== '/accounts/execute')).toBe(true)
})
