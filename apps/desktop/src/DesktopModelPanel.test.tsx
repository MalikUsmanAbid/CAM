import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import DesktopModelPanel from './DesktopModelPanel'
import { revokeModelCredential, storeModelCredential } from './api'

vi.mock('./api', () => ({ storeModelCredential: vi.fn(), revokeModelCredential: vi.fn() }))
const defaults = { provider: 'mock', model: 'jarvis-dev', endpoint: null, credential_reference: null, voice_enabled: false, voice_endpoint: null }
let invoke = vi.fn()
beforeEach(() => {
  vi.resetAllMocks()
  invoke = vi.fn(async (command: string) => command === 'local_model_configuration' ? defaults : undefined)
  Object.defineProperty(window, '__TAURI__', { value: { core: { invoke } }, configurable: true })
})
afterEach(() => { cleanup(); delete (window as unknown as { __TAURI__?: unknown }).__TAURI__ })

it('saves Gemini without a key or any paid connection attempt', async () => {
  await act(async () => { render(<DesktopModelPanel authenticated />) })
  fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'gemini' } })
  fireEvent.change(screen.getByLabelText('Model', { exact: true }), { target: { value: 'fixture-model' } })
  expect(screen.getByLabelText('Model endpoint')).toHaveValue('https://generativelanguage.googleapis.com/v1beta/openai/chat/completions')
  expect(screen.getByLabelText('Model endpoint')).toHaveAttribute('readonly')
  fireEvent.click(screen.getByRole('button', { name: 'Save for next desktop launch' }))
  await screen.findByRole('status')
  expect(invoke).toHaveBeenCalledWith('save_local_model_configuration', { configuration: {
    ...defaults, provider: 'gemini', model: 'fixture-model', endpoint: 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions',
  } })
  expect(storeModelCredential).not.toHaveBeenCalled()
})

it('sends a Claude key only to the vault, saves only its reference, and clears the input', async () => {
  const reference = 'vault:' + 'a'.repeat(32)
  vi.mocked(storeModelCredential).mockResolvedValue({ reference })
  await act(async () => { render(<DesktopModelPanel authenticated />) })
  fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'anthropic' } })
  fireEvent.change(screen.getByLabelText('Model', { exact: true }), { target: { value: 'fixture-claude' } })
  fireEvent.change(screen.getByLabelText('API key (optional for now)'), { target: { value: 'fixture-not-real-secret' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save for next desktop launch' }))
  await screen.findByRole('status')
  expect(storeModelCredential).toHaveBeenCalledWith('fixture-not-real-secret')
  expect(invoke).toHaveBeenLastCalledWith('save_local_model_configuration', { configuration: {
    ...defaults, provider: 'anthropic', model: 'fixture-claude', endpoint: 'https://api.anthropic.com/v1/messages', credential_reference: reference,
  } })
  expect(JSON.stringify(invoke.mock.calls)).not.toContain('fixture-not-real-secret')
  expect(screen.getByLabelText('API key (optional for now)')).toHaveValue('')
  vi.mocked(revokeModelCredential).mockResolvedValue({ status: 'revoked' })
  fireEvent.click(screen.getByRole('button', { name: 'Revoke protected key' }))
  await waitFor(() => expect(revokeModelCredential).toHaveBeenCalledWith(reference))
  await screen.findByText(/No API key configured/)
})

it('clears the old company credential when selecting another company', async () => {
  invoke.mockResolvedValue({ ...defaults, provider: 'anthropic', endpoint: 'https://api.anthropic.com/v1/messages', credential_reference: 'vault:' + 'b'.repeat(32) })
  await act(async () => { render(<DesktopModelPanel authenticated />) })
  await screen.findByText(/Protected key reference configured/)
  invoke.mockResolvedValue(undefined)
  fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'gemini' } })
  fireEvent.change(screen.getByLabelText('Model', { exact: true }), { target: { value: 'fixture-model' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save for next desktop launch' }))
  await screen.findByRole('status')
  expect(JSON.stringify(invoke.mock.calls.at(-1))).not.toContain('vault:')
  expect(storeModelCredential).not.toHaveBeenCalled()
})

it('does not save configuration when protected key storage fails', async () => {
  vi.mocked(storeModelCredential).mockRejectedValue(new Error('fixture failure'))
  await act(async () => { render(<DesktopModelPanel authenticated />) })
  fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'openai_compatible' } })
  fireEvent.change(screen.getByLabelText('Model', { exact: true }), { target: { value: 'fixture-model' } })
  fireEvent.change(screen.getByLabelText('Model endpoint'), { target: { value: 'https://compatible.example/v1' } })
  fireEvent.change(screen.getByLabelText('API key (optional for now)'), { target: { value: 'fixture-secret' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save for next desktop launch' }))
  await screen.findByRole('alert')
  expect(invoke).toHaveBeenCalledTimes(1)
})
