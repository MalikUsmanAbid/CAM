import { useEffect, useRef, useState } from 'react'
import { discoverModels, getSessionEpoch, storeModelCredential } from './api'
import { companies, configurationCompany, defaultModelConfiguration, desktopBridge } from './model-setup'
import type { Company, ModelConfiguration } from './model-setup'

export default function ModelSetup({ authenticated, onSaved, onBusyChange }: {
  authenticated: boolean; onSaved?: () => void; onBusyChange?: (busy: boolean) => void
}) {
  const bridge = desktopBridge()
  const [configuration, setConfiguration] = useState<ModelConfiguration | null>(null)
  const [company, setCompany] = useState<Company | null>(null)
  const [secret, setSecret] = useState('')
  const [models, setModels] = useState<{ id: string; name: string }[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [saved, setSaved] = useState(false)
  const active = useRef(true)
  useEffect(() => {
    active.current = true
    if (bridge) void bridge.core.invoke<ModelConfiguration>('local_model_configuration').then(value => {
      if (active.current) { setConfiguration({ ...defaultModelConfiguration, ...value }); setCompany(configurationCompany(value)) }
    }).catch(() => { if (active.current) setError('Your current settings could not be loaded. Reopen AI setup; nothing has been replaced.') })
    return () => { active.current = false }
  }, [bridge])
  if (!bridge) return <p>Open the installed desktop app to configure its model.</p>
  const working = (value: boolean) => { setBusy(value); onBusyChange?.(value) }
  const credential = async () => {
    if (!configuration) throw new Error('Settings are not loaded.')
    if (secret.trim()) {
      const result = await storeModelCredential(secret.trim())
      if (!active.current) throw new Error('Setup closed.')
      setConfiguration(current => current ? { ...current, credential_reference: result.reference } : current)
      setSecret('')
      return result.reference
    }
    if (!configuration.credential_reference) throw new Error('Add your API key first, or choose Set up later.')
    return configuration.credential_reference
  }
  const run = async (action: (epoch: number) => Promise<void>) => {
    working(true); setError(null); setSaved(false)
    try { await action(getSessionEpoch()) }
    catch (problem) { if (active.current) setError(problem instanceof Error ? problem.message : 'Setup could not finish. Retry when your session is connected.') }
    finally { if (active.current) working(false) }
  }
  return <section className="surface model-setup" aria-labelledby="ai-setup-title">
    <h2 id="ai-setup-title">Choose your AI</h2>
    <p>Choose a company, add your key, then pick a model. You can keep your current setup or do this later.</p>
    {!configuration && !error && <p role="status">Loading your saved settings…</p>}
    {error && <p className="setup-error" role="alert">{error}</p>}
    <fieldset disabled={!configuration || !authenticated || busy}>
      <legend>1. Choose a company</legend>
      <div className="provider-choices">{(Object.keys(companies) as Company[]).map(id => <button type="button" key={id}
        className={`provider-choice ${company === id ? 'chosen' : ''}`} aria-pressed={company === id}
        onClick={() => {
          if (id === company) return
          setCompany(id); setModels([]); setSecret(''); setNotice(''); setSaved(false); setError(null)
          setConfiguration(current => current ? { ...current, provider: id === 'nvidia' ? 'openai_compatible' : id, endpoint: companies[id].endpoint, model: '', credential_reference: null } : current)
        }}><strong>{companies[id].name}</strong><span>{companies[id].description}</span></button>)}</div>
      {company && configuration && <>
        <h3>2. Add your key and choose a model</h3>
        <p><button type="button" className="button subtle" onClick={() => {
          void bridge.core.invoke('open_model_key_page', { provider: company }).catch(() => setError(`Open ${companies[company].keys} in your browser to create a key.`))
        }}>Get a {companies[company].name} API key</button></p>
        <label htmlFor="setup-api-key">API key<input id="setup-api-key" type="password" autoComplete="off" value={secret} maxLength={16384}
          placeholder={configuration.credential_reference ? 'A protected key is already saved' : 'Paste your API key here'} onChange={event => { setSecret(event.target.value); setSaved(false); setModels([]) }} /></label>
        <p className="helper-text">Your key is protected by Windows. Loading models contacts this provider with your key; it sends no project files or chat messages and runs no AI prompt.</p>
        <button type="button" className="button" onClick={() => void run(async epoch => {
          const reference = await credential()
          if (getSessionEpoch() !== epoch) throw new Error('Your session changed. Reopen setup before continuing.')
          const result = await discoverModels(company, reference)
          if (!active.current || getSessionEpoch() !== epoch) return
          setModels(result.models); setNotice(result.models.length ? `${result.models.length} models listed${result.partial ? ' (more may be available)' : ''}. Choose a chat model; availability for coding is checked when used.` : 'No models were returned. Check account access or use Advanced settings.')
        })}>{busy ? 'Please wait…' : 'Load available models'}</button>
        <label htmlFor="setup-model">Model<select id="setup-model" value={configuration.model} onChange={event => { setConfiguration({ ...configuration, model: event.target.value }); setSaved(false) }}>
          <option value="">Choose a model</option>
          {configuration.model && !models.some(item => item.id === configuration.model) && <option value={configuration.model}>{configuration.model} — current selection</option>}
          {models.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>
        {notice && <p role="status">{notice}</p>}
        <details><summary>Advanced settings</summary>
          <label>Model ID<input maxLength={200} value={configuration.model} onChange={event => { setConfiguration({ ...configuration, model: event.target.value }); setSaved(false) }} /></label>
          <p>Use an exact ID from your provider if its model list is unavailable.</p>
          <label>Model endpoint<input value={configuration.endpoint ?? ''} readOnly /></label>
        </details>
        <div className="setup-spending"><strong>Cloud spending stays off by default</strong><p>Saving a key does not change spending permissions. Explicit transmission consent and model prices/limits are managed in Settings → Cloud consent and budgets. Existing policies are preserved. Listing a model is not proof that it can answer.</p></div>
        <button type="button" className="button primary" disabled={!configuration.model || (!secret.trim() && !configuration.credential_reference)} onClick={() => void run(async epoch => {
          const reference = await credential()
          if (!active.current || getSessionEpoch() !== epoch) throw new Error('Your session changed. Reopen setup before saving.')
          try { await bridge.core.invoke('save_local_model_configuration', { configuration: { ...configuration, credential_reference: reference } }) }
          catch { throw new Error('Could not finish saving. Reopen AI setup to check your settings; your protected key can be reused.') }
          if (active.current) { setSaved(true); working(false); onSaved?.() }
        })}>Save AI setup</button>
      </>}
    </fieldset>
    {saved && <p role="status">AI setup saved. Fully exit CAM from the tray and reopen to use this selection. Cloud calls still need an enabled spending policy.</p>}
  </section>
}
