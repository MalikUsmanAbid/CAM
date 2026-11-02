export type OrbState = 'idle' | 'listening' | 'thinking' | 'responding' | 'offline'

// Listening requires a real, explicitly enabled microphone session. Mock chat is
// labeled in the UI and still drives request/response states from actual HTTP work.
export function deriveOrbState({ online, listening, busy, responseVisible }: {
  online: boolean; listening: boolean; busy: boolean; responseVisible: boolean
}): OrbState {
  if (!online) return 'offline'
  if (listening) return 'listening'
  if (busy) return 'thinking'
  if (responseVisible) return 'responding'
  return 'idle'
}
