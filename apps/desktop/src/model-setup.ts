export type ModelConfiguration = {
  provider: 'mock' | 'ollama' | 'gemini' | 'anthropic' | 'openai_compatible'
  model: string; endpoint: string | null; credential_reference: string | null
  voice_enabled: boolean; voice_endpoint: string | null
}
export type Company = 'gemini' | 'anthropic' | 'nvidia'
export const companies = {
  gemini: { name: 'Google Gemini', description: 'Use models from your Google AI Studio account.', endpoint: 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions', keys: 'https://aistudio.google.com/apikey' },
  anthropic: { name: 'Anthropic Claude', description: 'Use Claude with a key scoped to your workspace.', endpoint: 'https://api.anthropic.com/v1/messages', keys: 'https://platform.claude.com/settings/keys' },
  nvidia: { name: 'NVIDIA', description: 'Use compatible hosted chat models from NVIDIA Build.', endpoint: 'https://integrate.api.nvidia.com/v1/chat/completions', keys: 'https://build.nvidia.com/settings' },
} as const
export const defaultModelConfiguration: ModelConfiguration = { provider: 'mock', model: 'jarvis-dev', endpoint: null, credential_reference: null, voice_enabled: false, voice_endpoint: null }
export type DesktopBridge = { core: { invoke: <T>(command: string, args?: Record<string, unknown>) => Promise<T> } }
export function desktopBridge() { return (window as unknown as { __TAURI__?: DesktopBridge }).__TAURI__ }
export function configurationCompany(value: ModelConfiguration): Company | null {
  if (value.provider === 'gemini' || value.provider === 'anthropic') return value.provider
  return value.provider === 'openai_compatible' && value.endpoint === companies.nvidia.endpoint ? 'nvidia' : null
}
