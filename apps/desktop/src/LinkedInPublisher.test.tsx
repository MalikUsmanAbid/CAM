import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import LinkedInPublisher from './LinkedInPublisher'

afterEach(() => cleanup())

it('requires fresh consent for the exact public text and connected member contract', async () => {
  const onRequest = vi.fn().mockResolvedValue(undefined)
  render(<LinkedInPublisher account={{ account_id: 'a'.repeat(32), revision: 3 }} onRequest={onRequest} busy={false} />)
  fireEvent.change(screen.getByLabelText('Exact LinkedIn member URN'), { target: { value: 'urn:li:person:fixture' } })
  fireEvent.change(screen.getByLabelText('Exact public post'), { target: { value: 'Reviewed public text' } })
  const consent = screen.getByLabelText(/I want this exact text publicly posted/)
  expect(screen.getByRole('button', { name: 'Request public post approval' })).toBeDisabled()
  fireEvent.click(consent)
  fireEvent.change(screen.getByLabelText('Exact public post'), { target: { value: 'Changed public text' } })
  expect(consent).not.toBeChecked()
  fireEvent.click(consent)
  fireEvent.click(screen.getByRole('button', { name: 'Request public post approval' }))
  await waitFor(() => expect(onRequest).toHaveBeenCalledWith({ operation_id: expect.stringMatching(/^[a-f0-9]{32}$/),
    account_id: 'a'.repeat(32), account_revision: 3, author: 'urn:li:person:fixture', body: 'Changed public text', publish_publicly: true }))
  expect(consent).not.toBeChecked()
})
