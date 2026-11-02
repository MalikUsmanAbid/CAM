import { useEffect, useState } from 'react'
import { revokeModelCredential, storeModelCredential } from './api'

type Provider = 'mock' | 'ollama' | 'gemini' | 'anthropic' | 'openai_compatible'
type Configuration = { provider: Provider; model: string; endpoint: string | null; credential_reference: string | null; voice_enabled: boolean; voice_endpoint: string | null }
type Bridge = { core: { invoke: <T>(command: string, args?: Record<string, unknown>) => Promise<T> } }
const endpoints: Record<Provider, string | null> = {
  mock: null, ollama: 'http://127.0.0.1:11434/v1',
  gemini: 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions',
  anthropic: 'https://api.anthropic.com/v1/messages', openai_compatible: '',
}

export default function DesktopModelPanel({ authenticated }: { authenticated: boolean }) {
  const bridge = (window as unknown as { __TAURI__?: Bridge }).__TAURI__
  const [configuration, setConfiguration] = useState<Configuration>({ provider: 'mock', model: 'jarvis-dev', endpoint: null, credential_reference: null, voice_enabled: false, voice_endpoint: null })
  const [secret, setSecret] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let active = true
    if (bridge) void bridge.core.invoke<Configuration>('local_model_configuration').then((value) => {
      if (active) setConfiguration({ ...value, credential_reference: value.credential_reference ?? null })
    }).catch(() => { if (active) setError('Desktop model settings are unavailable.') }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [bridge])
  if (!bridge) return null
  const cloud = !['mock', 'ollama'].includes(configuration.provider)
  return <section className="surface"><h2>Desktop model providers</h2>
    <p className="helper-text">Select a local model, Google Gemini, Anthropic Claude or another compatible API. No automatic cloud fallback, connection test or download.</p>
    {error && <p role="alert">{error}</p>}
    <form onSubmit={(event) => {
      event.preventDefault(); setBusy(true); setError(null); setSaved(false)
      void (async () => {
        try {
          let next = configuration
          if (cloud && secret.trim()) {
            const result = await storeModelCredential(secret)
            next = { ...configuration, credential_reference: result.reference }
            setConfiguration(next); setSecret('')
          }
          await bridge.core.invoke('save_local_model_configuration', { configuration: next })
          setSaved(true)
        } catch { setError('Cannot save model settings. Check the endpoint, model ID and authenticated session. A stored protected key is retained for retry.') }
        finally { setBusy(false) }
      })()
    }}>
      <fieldset disabled={loading || busy || !authenticated}>
        <legend>Model connection</legend>
        <label>Provider<select value={configuration.provider} onChange={(event) => {
          const provider = event.target.value as Provider
          setConfiguration({ ...configuration, provider, endpoint: endpoints[provider], credential_reference: null,
            model: provider === 'mock' ? 'jarvis-dev' : provider === 'ollama' ? 'usman-jarvis-coder-cpu:latest' : '' })
          setSecret(''); setSaved(false)
        }}><option value="mock">Mock — development responses</option><option value="ollama">Local Ollama</option><option value="gemini">Google Gemini</option><option value="anthropic">Anthropic Claude</option><option value="openai_compatible">Other compatible cloud API</option></select></label>
        <label>Model<input value={configuration.model} placeholder="Exact model ID from your provider" maxLength={200} onChange={(event) => { setConfiguration({ ...configuration, model: event.target.value }); setSaved(false) }} required /></label>
        {configuration.provider !== 'mock' && <label>Model endpoint<input value={configuration.endpoint ?? ''} maxLength={500} readOnly={configuration.provider === 'gemini' || configuration.provider === 'anthropic'} onChange={(event) => { setConfiguration({ ...configuration, endpoint: event.target.value, credential_reference: null }); setSecret(''); setSaved(false) }} required /></label>}
        {cloud && <>
          <label>API key (optional for now)<input type="password" autoComplete="off" maxLength={16384} value={secret} onChange={(event) => setSecret(event.target.value)} /></label>
          <p>{configuration.credential_reference ? 'Protected key reference configured. The key is never returned by settings.' : 'No API key configured. You may save now; cloud calls remain unavailable.'}</p>
          {configuration.credential_reference && <button type="button" className="button subtle" onClick={() => {
            const reference = configuration.credential_reference
            if (!reference) return
            setBusy(true); setError(null)
            void revokeModelCredential(reference).then(() => { setConfiguration({ ...configuration, credential_reference: null }); setSecret(''); setSaved(false) })
              .catch(() => setError('Cannot revoke the protected key.')).finally(() => setBusy(false))
          }}>Revoke protected key</button>}
          <p className="helper-text">Keys are encrypted by Windows for this user. Only a vault reference is saved in settings. Before cloud calls, configure the exact endpoint/model under Cloud consent and budgets below, including transmission consent, current prices and positive limits. Saving a key does not enable spending. Provider accounts may require billing.</p>
        </>}
        <label><input type="checkbox" checked={configuration.voice_enabled} onChange={(event) => setConfiguration({ ...configuration, voice_enabled: event.target.checked })} />Enable push-to-talk through my configured local transcription service</label>
        {configuration.voice_enabled && <label>Local transcription endpoint<input value={configuration.voice_endpoint ?? ''} maxLength={500} onChange={(event) => setConfiguration({ ...configuration, voice_endpoint: event.target.value })} required /></label>}
        <p className="helper-text">Local Ollama and voice services must already be running. Provider selection applies to chat and coding proposals; the task planner retains its separate configuration.</p>
        <button className="button primary">Save for next desktop launch</button>
      </fieldset>
    </form>
    {saved && <p role="status">Settings saved. Choose Exit CAM from the tray and reopen. Missing keys, revoked keys or missing cloud consent/budgets keep requests blocked.</p>}
  </section>
}
