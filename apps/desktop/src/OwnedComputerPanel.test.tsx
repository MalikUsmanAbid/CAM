import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import OwnedComputerPanel from './OwnedComputerPanel'
import { executeOwnedComputerTool, getTools } from './api'

vi.mock('./api', () => ({ getSessionEpoch: () => 1, getTools: vi.fn(), executeOwnedComputerTool: vi.fn() }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('keeps all attended actions unavailable when the production acceptance gate is closed', async () => {
  vi.mocked(getTools).mockResolvedValue([{ name: 'computer_owned_open', enabled: false }])
  render(<OwnedComputerPanel projectId={null} authenticated />)
  await waitFor(() => expect(getTools).toHaveBeenCalled())
  expect(screen.getByText(/Windows security acceptance has not enabled/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Request attended session approval' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Request exact Invoke approval' })).toBeDisabled()
  expect(executeOwnedComputerTool).not.toHaveBeenCalled()
})
