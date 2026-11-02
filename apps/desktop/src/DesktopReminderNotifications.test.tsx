import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import DesktopReminderNotifications from './DesktopReminderNotifications'
import { workspaceRequest } from './api'

vi.mock('./api', () => ({ getSessionEpoch: () => 1, workspaceRequest: vi.fn() }))
beforeEach(() => { sessionStorage.clear() })
afterEach(() => { cleanup(); Reflect.deleteProperty(window, '__TAURI__'); vi.clearAllMocks() })

it('collapses multiple reminders into one generic notice without leaking private titles', async () => {
  const sendNotification = vi.fn()
  Object.defineProperty(window, '__TAURI__', { configurable: true, value: { notification: {
    isPermissionGranted: async () => true, requestPermission: vi.fn(), sendNotification,
  } } })
  vi.mocked(workspaceRequest).mockResolvedValue({ items: [
    { occurrence_id: 'a'.repeat(32), status: 'pending', title: 'Private health reminder' },
    { occurrence_id: 'b'.repeat(32), status: 'waiting_for_review', title: 'Private account content' },
  ] })
  render(<DesktopReminderNotifications projectId="A" />)
  fireEvent.click(screen.getByRole('button', { name: 'Enable desktop notices for selected scope' }))
  await waitFor(() => expect(sendNotification).toHaveBeenCalledTimes(1))
  expect(sendNotification).toHaveBeenCalledWith({ title: 'CAM', body: 'A reminder is waiting in your Decisions inbox.' })
  expect(workspaceRequest).toHaveBeenCalledWith('/notifications', 'GET', undefined, 'A', expect.any(AbortSignal))
})

it('discards a permission reply after the component was removed', async () => {
  let resolve!: (value: boolean) => void
  const sendNotification = vi.fn()
  Object.defineProperty(window, '__TAURI__', { configurable: true, value: { notification: {
    isPermissionGranted: () => new Promise<boolean>((done) => { resolve = done }), requestPermission: vi.fn(), sendNotification,
  } } })
  const mounted = render(<DesktopReminderNotifications projectId="A" />)
  fireEvent.click(screen.getByRole('button', { name: 'Enable desktop notices for selected scope' }))
  mounted.unmount()
  await act(async () => { resolve(true) })
  expect(sendNotification).not.toHaveBeenCalled()
  expect(workspaceRequest).not.toHaveBeenCalled()
})
