import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import DiagnosticsDownload from './DiagnosticsDownload'
import * as api from './api'

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('downloads a redacted report only after the user clicks', async () => {
  const request = vi.spyOn(api, 'workspaceRequest').mockResolvedValue({ platform: 'Windows', python: '3.12.9', provider: 'mock', private_field: 'do not export' })
  const create = vi.fn((blob: Blob) => { expect(blob).toBeInstanceOf(Blob); return 'blob:fixture' }), revoke = vi.fn()
  vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke })
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  render(<DiagnosticsDownload />)
  expect(request).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Download redacted diagnostics' }))
  await waitFor(() => expect(click).toHaveBeenCalledTimes(1))
  expect(create).toHaveBeenCalledTimes(1)
  expect(create.mock.calls[0][0]).toBeInstanceOf(Blob)
})

it('does not produce a download from a late response after logout', async () => {
  let resolve!: (value: unknown) => void
  const epoch = vi.spyOn(api, 'getSessionEpoch').mockReturnValue(1)
  vi.spyOn(api, 'workspaceRequest').mockImplementationOnce(() => new Promise((accept) => { resolve = accept }))
  const create = vi.fn()
  vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: vi.fn() })
  render(<DiagnosticsDownload />)
  fireEvent.click(screen.getByRole('button', { name: 'Download redacted diagnostics' }))
  epoch.mockReturnValue(2)
  await act(async () => resolve({ platform: 'Windows' }))
  expect(create).not.toHaveBeenCalled()
})
