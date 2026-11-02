import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import RemindersPanel from './RemindersPanel'
import * as api from './api'

afterEach(() => { cleanup(); vi.restoreAllMocks() })
const schedule = { schedule_id: 'a'.repeat(32), title: 'Review progress', status: 'scheduled', kind: 'reminder', next_due: '2030-01-01T00:00:00+00:00', interval_seconds: null, contract_sha256: 'b'.repeat(64) }

it('reschedules the observed contract with explicit timezone and no task execution', async () => {
  const request = vi.spyOn(api, 'workspaceRequest')
    .mockResolvedValueOnce([schedule]).mockResolvedValueOnce({}).mockResolvedValueOnce([])
  render(<RemindersPanel projectId="alpha" />)
  fireEvent.click(screen.getByRole('button', { name: 'Show schedules' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Change time for Review progress' }))
  fireEvent.change(screen.getByLabelText('New local date and time'), { target: { value: '2030-01-02T10:00' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save reminder timing' }))
  await screen.findByRole('status')
  expect(request).toHaveBeenNthCalledWith(2, `/reminders/${schedule.schedule_id}`, 'PUT', {
    expected_contract_sha256: schedule.contract_sha256, due_at: new Date('2030-01-02T10:00').toISOString(), interval_seconds: null,
  }, 'alpha')
  expect(request).toHaveBeenCalledTimes(3)
})

it('cancels exactly the selected schedule contract within personal scope', async () => {
  const request = vi.spyOn(api, 'workspaceRequest')
    .mockResolvedValueOnce([schedule]).mockResolvedValueOnce({}).mockResolvedValueOnce([])
  render(<RemindersPanel projectId={null} />)
  fireEvent.click(screen.getByRole('button', { name: 'Show schedules' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Cancel Review progress and pending notifications' }))
  await waitFor(() => expect(request).toHaveBeenNthCalledWith(2, `/reminders/${schedule.schedule_id}/cancel`, 'POST', {
    expected_contract_sha256: schedule.contract_sha256,
  }, null))
})
