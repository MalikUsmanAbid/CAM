import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import VoicePanel from './VoicePanel'
import { transcribeCapture, voiceStatus } from './api'

vi.mock('./api', () => ({ getSessionEpoch: () => 1, voiceStatus: vi.fn(), transcribeCapture: vi.fn() }))
const originalMedia = Object.getOwnPropertyDescriptor(navigator, 'mediaDevices')
afterEach(() => {
  cleanup(); vi.resetAllMocks()
  if (originalMedia) Object.defineProperty(navigator, 'mediaDevices', originalMedia)
  else Reflect.deleteProperty(navigator, 'mediaDevices')
})

it('never opens the microphone when local transcription is unconfigured', async () => {
  const microphone = vi.fn()
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: microphone } })
  vi.mocked(voiceStatus).mockResolvedValue({ available: false, privacy: 'fixture' })
  render(<VoicePanel />)
  await screen.findByText('Local transcription is not configured. Microphone capture is disabled.')
  expect(screen.getByRole('button', { name: 'Hold to talk' })).toBeDisabled()
  expect(microphone).not.toHaveBeenCalled()
})

it('releases a late microphone permission result after the hold was released, without submitting audio', async () => {
  let accept!: (stream: MediaStream) => void
  const pending = new Promise<MediaStream>((resolve) => { accept = resolve })
  const microphone = vi.fn().mockReturnValue(pending)
  const stop = vi.fn()
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: microphone } })
  vi.mocked(voiceStatus).mockResolvedValue({ available: true, privacy: 'fixture' })
  render(<VoicePanel />)
  const button = screen.getByRole('button', { name: 'Hold to talk' })
  await waitFor(() => expect(button).toBeEnabled())
  fireEvent.keyDown(button, { code: 'Space', repeat: false })
  expect(button).toBeEnabled()
  fireEvent.keyUp(button, { code: 'Space' })
  expect(button).toBeDisabled()
  accept({ getTracks: () => [{ stop }] } as unknown as MediaStream)
  await waitFor(() => expect(stop).toHaveBeenCalledTimes(1))
  expect(transcribeCapture).not.toHaveBeenCalled()
  expect(screen.getByRole('status')).toHaveTextContent('Microphone off')
})
