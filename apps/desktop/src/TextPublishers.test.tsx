import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import BlueskyPublisher from './BlueskyPublisher'
import { newRecordKey } from './bluesky-record-key'
import ThreadsPublisher from './ThreadsPublisher'
import PinterestPublisher from './PinterestPublisher'

afterEach(() => cleanup())
const account = { account_id: 'a'.repeat(32), revision: 3, service_base: 'https://pds.example' }

it('generates canonical timestamp-based record keys for exact Bluesky approvals', () => {
  expect(newRecordKey()).toMatch(/^[234567abcdefghij][234567abcdefghijklmnopqrstuvwxyz]{12}$/)
})

it('binds Bluesky text, DID and PDS to a separate approval request', () => {
  const request = vi.fn().mockResolvedValue(undefined)
  render(<BlueskyPublisher account={account} onRequest={request} busy={false} />)
  fireEvent.change(screen.getByLabelText('Exact Bluesky repository DID'), { target: { value: 'did:plc:' + 'b'.repeat(24) } })
  fireEvent.change(screen.getByLabelText('Exact Bluesky text'), { target: { value: 'Approved public text' } })
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: 'Request Bluesky post approval' }))
  expect(request).toHaveBeenCalledWith(expect.objectContaining({ account_id: account.account_id, account_revision: 3,
    service_base: account.service_base, repository_did: 'did:plc:' + 'b'.repeat(24), body: 'Approved public text', publish_publicly: true }))
  expect(request.mock.calls[0][0].record_key).toMatch(/^[234567abcdefghij][234567abcdefghijklmnopqrstuvwxyz]{12}$/)
  expect(screen.getByRole('checkbox')).not.toBeChecked()
})

it('resets Threads publication consent when exact text changes', () => {
  const request = vi.fn().mockResolvedValue(undefined)
  render(<ThreadsPublisher account={account} onRequest={request} busy={false} />)
  fireEvent.change(screen.getByLabelText('Exact Threads text'), { target: { value: 'First text' } })
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.change(screen.getByLabelText('Exact Threads text'), { target: { value: 'Changed text' } })
  expect(screen.getByRole('checkbox')).not.toBeChecked()
  expect(screen.getByRole('button', { name: 'Request Threads post approval' })).toBeDisabled()
  expect(request).not.toHaveBeenCalled()
})

it('binds Pinterest board, image URL and alt text to a separate approval', () => {
  const request = vi.fn().mockResolvedValue(undefined)
  render(<PinterestPublisher account={account} onRequest={request} busy={false} />)
  fireEvent.change(screen.getByLabelText('Exact Pinterest board ID'), { target: { value: '12345678' } })
  fireEvent.change(screen.getByLabelText('Exact pin title'), { target: { value: 'Approved pin' } })
  fireEvent.change(screen.getByLabelText('Image alternative text'), { target: { value: 'Approved alternative text' } })
  fireEvent.change(screen.getByLabelText('Exact public HTTPS image URL'), { target: { value: 'https://images.example/photo.png' } })
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: 'Request Pinterest pin approval' }))
  expect(request).toHaveBeenCalledWith(expect.objectContaining({ board_id: '12345678', title: 'Approved pin',
    alt_text: 'Approved alternative text', image_url: 'https://images.example/photo.png', publish_to_board: true }))
})
