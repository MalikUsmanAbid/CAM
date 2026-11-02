import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ModelSetup from './ModelSetup'
import { discoverModels, storeModelCredential } from './api'
import { defaultModelConfiguration } from './model-setup'

vi.mock('./api', () => ({ discoverModels: vi.fn(), storeModelCredential: vi.fn(), getSessionEpoch: () => 0 }))
const reference = 'vault:' + 'a'.repeat(32)
let invoke = vi.fn()
beforeEach(() => {
  vi.resetAllMocks()
  invoke = vi.fn(async (command: string) => command === 'local_model_configuration' ? { ...defaultModelConfiguration, voice_enabled: true, voice_endpoint: 'http://127.0.0.1:9000' } : undefined)
  Object.defineProperty(window, '__TAURI__', { configurable: true, value: { core: { invoke } } })
})
afterEach(() => { cleanup(); Reflect.deleteProperty(window, '__TAURI__') })

it.each(['Google Gemini', 'Anthropic Claude', 'NVIDIA'])('offers %s without replacing current settings on selection', async name => {
  await act(async () => { render(<ModelSetup authenticated />) })
  fireEvent.click(screen.getByRole('button', { name: new RegExp(name) }))
  expect(screen.getByLabelText('API key')).toHaveAttribute('type', 'password')
  expect(screen.getByRole('button', { name: 'Save AI setup' })).toBeDisabled()
  expect(invoke).toHaveBeenCalledTimes(1)
  expect(storeModelCredential).not.toHaveBeenCalled()
})

it('loads actual account choices only on request and saves NVIDIA using a protected ref, preserving voice settings', async () => {
  vi.mocked(storeModelCredential).mockResolvedValue({ reference })
  vi.mocked(discoverModels).mockResolvedValue({ models: [{ id: 'vendor/chat-model', name: 'My account model' }], partial: false, notice: '' })
  await act(async () => { render(<ModelSetup authenticated />) })
  expect(discoverModels).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: /NVIDIA/ }))
  fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'fake-secret' } })
  fireEvent.click(screen.getByRole('button', { name: 'Load available models' }))
  await screen.findByRole('option', { name: 'My account model' })
  expect(discoverModels).toHaveBeenCalledWith('nvidia', reference)
  expect(screen.getByLabelText('API key')).toHaveValue('')
  fireEvent.change(screen.getByLabelText('Model', { exact: true }), { target: { value: 'vendor/chat-model' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save AI setup' }))
  await screen.findByText(/AI setup saved/)
  expect(invoke).toHaveBeenCalledWith('save_local_model_configuration', { configuration: {
    ...defaultModelConfiguration, provider: 'openai_compatible', endpoint: 'https://integrate.api.nvidia.com/v1/chat/completions', model: 'vendor/chat-model', credential_reference: reference,
    voice_enabled: true, voice_endpoint: 'http://127.0.0.1:9000',
  } })
  expect(JSON.stringify(invoke.mock.calls)).not.toContain('fake-secret')
  expect(storeModelCredential).toHaveBeenCalledTimes(1)
})

it('clears model choices and the previous company key when changing companies', async () => {
  invoke.mockResolvedValue({ ...defaultModelConfiguration, provider: 'anthropic', model: 'old-model', endpoint: 'https://api.anthropic.com/v1/messages', credential_reference: reference })
  await act(async () => { render(<ModelSetup authenticated />) })
  expect(screen.getByLabelText('Model', { exact: true })).toHaveValue('old-model')
  fireEvent.click(screen.getByRole('button', { name: /Google Gemini/ }))
  expect(screen.getByLabelText('Model', { exact: true })).toHaveValue('')
  fireEvent.click(screen.getByRole('button', { name: 'Load available models' }))
  await screen.findByRole('alert')
  expect(discoverModels).not.toHaveBeenCalled()
  expect(invoke).toHaveBeenCalledTimes(1)
})

it('keeps existing settings when they cannot be loaded and disables configuration writes', async () => {
  invoke.mockRejectedValue(new Error('fixture'))
  render(<ModelSetup authenticated />)
  await screen.findByRole('alert')
  expect(screen.getByRole('button', { name: /Google Gemini/ })).toBeDisabled()
  expect(invoke).toHaveBeenCalledTimes(1)
})

it('does not save a config if protected key storage fails', async () => {
  vi.mocked(storeModelCredential).mockRejectedValue(new Error('storage unavailable'))
  await act(async () => { render(<ModelSetup authenticated />) })
  fireEvent.click(screen.getByRole('button', { name: /Anthropic Claude/ }))
  fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'fake-secret' } })
  fireEvent.click(screen.getByText('Advanced settings'))
  fireEvent.change(screen.getByLabelText('Model ID'), { target: { value: 'fixture-model' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save AI setup' }))
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('storage unavailable'))
  expect(invoke).toHaveBeenCalledTimes(1)
})
