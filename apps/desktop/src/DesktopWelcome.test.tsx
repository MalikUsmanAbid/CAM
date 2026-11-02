import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import DesktopWelcome from './DesktopWelcome'
import { initializeDesktopSession } from './api'

vi.mock('./api', () => ({ initializeDesktopSession: vi.fn() }))
vi.mock('./ModelSetup', () => ({ default: () => <p>Provider selection fixture</p> }))
let invoke = vi.fn()
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(initializeDesktopSession).mockResolvedValue()
  invoke = vi.fn(async (command: string) => command === 'desktop_setup_status' ? { completed: false, existing_configuration: false } : undefined)
  Object.defineProperty(window, '__TAURI__', { configurable: true, value: { core: { invoke } } })
})
afterEach(() => { cleanup(); Reflect.deleteProperty(window, '__TAURI__') })

it('shows setup before the dashboard and skipping writes only the welcome marker', async () => {
  render(<DesktopWelcome><p>My dashboard</p></DesktopWelcome>)
  await screen.findByRole('heading', { name: 'Make CAM yours' })
  expect(screen.queryByText('My dashboard')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Set up later' }))
  await screen.findByText('My dashboard')
  expect(invoke.mock.calls.map(call => call[0])).toEqual(['desktop_setup_status', 'finish_desktop_setup'])
})

it('preserves existing configuration without a save when keeping settings', async () => {
  invoke.mockImplementation(async command => command === 'desktop_setup_status' ? { completed: false, existing_configuration: true } : undefined)
  render(<DesktopWelcome><p>Existing dashboard</p></DesktopWelcome>)
  fireEvent.click(await screen.findByRole('button', { name: 'Keep my settings and continue' }))
  await screen.findByText('Existing dashboard')
  expect(invoke).not.toHaveBeenCalledWith('save_local_model_configuration', expect.anything())
})

it('does not show setup again after native completion', async () => {
  invoke.mockResolvedValue({ completed: true, existing_configuration: true })
  render(<DesktopWelcome><p>Ready dashboard</p></DesktopWelcome>)
  await screen.findByText('Ready dashboard')
  expect(screen.queryByText('Provider selection fixture')).not.toBeInTheDocument()
})

it('allows recovery without overwriting settings if native status is unavailable', async () => {
  invoke.mockRejectedValue(new Error('fixture failure'))
  render(<DesktopWelcome><p>Recovered dashboard</p></DesktopWelcome>)
  fireEvent.click(await screen.findByRole('button', { name: 'Continue without changing settings' }))
  await screen.findByText('Recovered dashboard')
  expect(invoke).toHaveBeenCalledTimes(1)
})

it('does not claim skip persisted when the native marker write fails', async () => {
  invoke.mockImplementation(async command => {
    if (command === 'desktop_setup_status') return { completed: false, existing_configuration: false }
    throw new Error('fixture write failure')
  })
  render(<DesktopWelcome><p>Dashboard after failure</p></DesktopWelcome>)
  fireEvent.click(await screen.findByRole('button', { name: 'Set up later' }))
  await screen.findByRole('alert')
  expect(screen.queryByText('Dashboard after failure')).not.toBeInTheDocument()
})
