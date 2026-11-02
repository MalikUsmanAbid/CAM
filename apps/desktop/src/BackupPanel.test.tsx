import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import BackupPanel from './BackupPanel'
import { getBackupStatus, prepareBackup } from './api'

vi.mock('./api', () => ({ getSessionEpoch: () => 1, getBackupStatus: vi.fn(), prepareBackup: vi.fn(), discardBackup: vi.fn(), readEncryptedBackup: vi.fn() }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('clears passphrase fields after preparing an opaque approval and does not persist the secret in storage', async () => {
  vi.mocked(getBackupStatus).mockResolvedValue({ available: true, ready: [], pending: [] })
  vi.mocked(prepareBackup).mockResolvedValue({ status: 'approval_required', tool_name: 'backup_export', detail: 'Review export', result: null, capture_id: 'a'.repeat(32) })
  render(<BackupPanel authenticated />)
  await waitFor(() => expect(screen.getByRole('button', { name: 'Request encrypted export approval' })).toBeEnabled())
  const secret = 'fixture-exclusive-recovery-passphrase'
  fireEvent.change(screen.getByLabelText('Separate recovery passphrase'), { target: { value: secret } })
  fireEvent.change(screen.getByLabelText('Confirm recovery passphrase'), { target: { value: secret } })
  fireEvent.click(screen.getByRole('button', { name: 'Request encrypted export approval' }))
  await waitFor(() => expect(prepareBackup).toHaveBeenCalledWith(secret))
  expect(screen.getByLabelText('Separate recovery passphrase')).toHaveValue('')
  expect(screen.getByLabelText('Confirm recovery passphrase')).toHaveValue('')
  expect(JSON.stringify(localStorage) + JSON.stringify(sessionStorage)).not.toContain(secret)
})

it('does not offer a plaintext fallback when encryption support is unavailable', async () => {
  vi.mocked(getBackupStatus).mockResolvedValue({ available: false, ready: [], pending: [] })
  render(<BackupPanel authenticated />)
  await waitFor(() => expect(getBackupStatus).toHaveBeenCalled())
  expect(screen.getByRole('button', { name: 'Request encrypted export approval' })).toBeDisabled()
  expect(prepareBackup).not.toHaveBeenCalled()
})
