import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import MastodonPublisher from './MastodonPublisher'

afterEach(() => cleanup())

it('binds exact text and selected visibility to the saved credential instance', async () => {
  const onRequest = vi.fn().mockResolvedValue(undefined)
  render(<MastodonPublisher account={{ account_id: 'a'.repeat(32), revision: 2, service_base: 'https://social.example' }} onRequest={onRequest} busy={false} />)
  fireEvent.change(screen.getByLabelText('Exact Mastodon text'), { target: { value: 'Approved instance post' } })
  const consent = screen.getByLabelText(/I reviewed this exact text, instance and audience/)
  fireEvent.click(consent)
  fireEvent.change(screen.getByLabelText('Mastodon visibility'), { target: { value: 'public' } })
  expect(consent).not.toBeChecked()
  fireEvent.click(consent)
  fireEvent.click(screen.getByRole('button', { name: 'Request Mastodon post approval' }))
  await waitFor(() => expect(onRequest).toHaveBeenCalledWith({ operation_id: expect.stringMatching(/^[a-f0-9]{32}$/),
    account_id: 'a'.repeat(32), account_revision: 2, service_base: 'https://social.example', body: 'Approved instance post',
    visibility: 'public', confirm_visibility: true, sensitive: false, spoiler_text: '' }))
})
