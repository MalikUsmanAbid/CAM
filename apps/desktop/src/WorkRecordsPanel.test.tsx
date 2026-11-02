import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import WorkRecordsPanel from './WorkRecordsPanel'
import * as api from './api'

afterEach(() => { cleanup(); vi.restoreAllMocks() })
const row = { record_id: 'a'.repeat(32), title: 'Fixture lead', content: 'Existing content', kind: 'lead', status: 'draft', revision: 7 }

it('sends the observed revision and preserves the draft when a newer edit rejects it', async () => {
  const request = vi.spyOn(api, 'workspaceRequest').mockResolvedValueOnce({ items: [row], next_cursor: null })
    .mockRejectedValueOnce(new Error('Record changed; refresh before editing.'))
  render(<WorkRecordsPanel projectId="alpha" />)
  fireEvent.click(screen.getByRole('button', { name: 'Apply filters and refresh' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Edit Fixture lead' }))
  fireEvent.change(screen.getByLabelText('Record title'), { target: { value: 'My edited title' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save record changes' }))
  await screen.findByRole('alert')
  expect(request).toHaveBeenLastCalledWith(`/records/${row.record_id}`, 'PUT', {
    title: 'My edited title', content: row.content, kind: 'lead', status: 'draft', expected_revision: 7,
  }, 'alpha')
  expect(screen.getByLabelText('Record title')).toHaveValue('My edited title')
  expect(request).toHaveBeenCalledTimes(2)
})

it('loads another bounded page within personal scope', async () => {
  const request = vi.spyOn(api, 'workspaceRequest').mockResolvedValueOnce({ items: [row], next_cursor: 'fixture-cursor' })
    .mockResolvedValueOnce({ items: [{ ...row, record_id: 'b'.repeat(32), title: 'Another lead' }], next_cursor: null })
  render(<WorkRecordsPanel projectId={null} />)
  fireEvent.click(screen.getByRole('button', { name: 'Apply filters and refresh' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Load more records' }))
  await screen.findByRole('button', { name: 'Edit Another lead' })
  expect(request).toHaveBeenLastCalledWith('/records/page?cursor=fixture-cursor', 'GET', undefined, null)
})

it('protects unsaved new-record content from row actions', async () => {
  vi.spyOn(api, 'workspaceRequest').mockResolvedValueOnce({ items: [row], next_cursor: null })
  render(<WorkRecordsPanel projectId="alpha" />)
  fireEvent.click(screen.getByRole('button', { name: 'Apply filters and refresh' }))
  await screen.findByRole('button', { name: 'Edit Fixture lead' })
  fireEvent.change(screen.getByLabelText('Record title'), { target: { value: 'Unsaved note' } })
  expect(screen.getByRole('button', { name: 'Edit Fixture lead' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Archive: Fixture lead' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Clear new record draft' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Edit Fixture lead' })).toBeEnabled())
})
