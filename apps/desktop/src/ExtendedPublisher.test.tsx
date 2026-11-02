import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ExtendedPublisher from './ExtendedPublisher'
import { workspaceRequest } from './api'

vi.mock('./api', () => ({ getSessionEpoch: () => 1, workspaceRequest: vi.fn() }))
const request = vi.mocked(workspaceRequest)
beforeEach(() => { request.mockReset(); request.mockResolvedValue({ status: 'approval_required' }) })
afterEach(() => cleanup())

it('requests exact Facebook approval without making a provider request in the browser', async () => {
  render(<ExtendedPublisher account={{ provider: 'facebook', account_id: 'a'.repeat(32), revision: 2 }} />)
  fireEvent.change(screen.getByLabelText('Exact Page ID'), { target: { value: '123' } })
  fireEvent.change(screen.getByLabelText('Exact post text'), { target: { value: 'Exact approved text' } })
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: 'Request exact publication approval' }))
  await waitFor(() => expect(request).toHaveBeenCalledWith('/accounts/execute', 'POST', {
    tool: 'account_facebook_post', arguments: expect.objectContaining({ account_id: 'a'.repeat(32), account_revision: 2, page_id: '123', body: 'Exact approved text', publish_publicly: true }),
  }))
})

it('renders current TikTok privacy choices with no default selection', async () => {
  request.mockResolvedValue({ status: 'success', result: { receipt: { creator_contract_sha256: 'c'.repeat(64), creator: {
    creator_nickname: 'Fixture', creator_username: 'fixture', privacy_level_options: ['SELF_ONLY'], max_video_post_duration_sec: 60,
  } } } })
  render(<ExtendedPublisher account={{ provider: 'tiktok', account_id: 'a'.repeat(32), revision: 1 }} />)
  fireEvent.click(screen.getByRole('button', { name: 'Request current creator settings' }))
  await screen.findByRole('option', { name: 'SELF_ONLY' })
  expect(screen.getByLabelText('TikTok visibility')).toHaveValue('')
  expect(screen.queryByRole('option', { name: 'PUBLIC_TO_EVERYONE' })).not.toBeInTheDocument()
})

it('resets financial consent when the exact paid allowance changes', () => {
  render(<ExtendedPublisher account={{ provider: 'x', account_id: 'a'.repeat(32), revision: 1 }} />)
  fireEvent.click(screen.getByLabelText('I accept provider charges within this configured allowance for separate financial approval.'))
  fireEvent.change(screen.getByLabelText('Maximum cost per request in micro-USD'), { target: { value: '2000' } })
  expect(screen.getByLabelText('I accept provider charges within this configured allowance for separate financial approval.')).not.toBeChecked()
  expect(request).not.toHaveBeenCalled()
})
