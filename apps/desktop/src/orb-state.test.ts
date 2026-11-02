import { describe, expect, it } from 'vitest'
import { deriveOrbState } from './orb-state'

describe('assistant orb state', () => {
  const idle = { online: true, listening: false, busy: false, responseVisible: false }
  it('stays offline despite stale activity when the API is unavailable', () => {
    expect(deriveOrbState({ ...idle, online: false, listening: true, busy: true, responseVisible: true })).toBe('offline')
  })
  it('listens only when a real microphone session is explicitly active', () => {
    expect(deriveOrbState(idle)).toBe('idle')
    expect(deriveOrbState({ ...idle, listening: true })).toBe('listening')
  })
  it('prioritizes actual work over a previously received response', () => {
    expect(deriveOrbState({ ...idle, busy: true, responseVisible: true })).toBe('thinking')
    expect(deriveOrbState({ ...idle, responseVisible: true })).toBe('responding')
  })
})
