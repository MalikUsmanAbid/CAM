import { useEffect, useRef, useState } from 'react'
import { getSessionEpoch, oauthRequest } from './api'

export default function OAuthPanel() {
  const [provider, setProvider] = useState('google')
  const [clientId, setClientId] = useState('')
  const [secretReference, setSecretReference] = useState('')
  const [label, setLabel] = useState('')
  const [access, setAccess] = useState('mail')
  const [consent, setConsent] = useState(false)
  const [authorization, setAuthorization] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  async function run(operation: () => Promise<void>) {
    const epoch = getSessionEpoch()
    setBusy(true); setStatus(null)
    try { await operation() }
    catch (reason) { if (alive.current && epoch === getSessionEpoch()) setStatus(reason instanceof Error ? reason.message : 'Account authorization did not complete.') }
    finally { if (alive.current && epoch === getSessionEpoch()) setBusy(false) }
  }
  return <section className="surface"><h2>Google / Microsoft sign-in</h2>
    <p>Configure your registered installed/public OAuth client and its loopback redirect. No client registration or provider account is created automatically. Authorization happens in the system browser, not an embedded credential form.</p>
    <label>OAuth provider<select value={provider} onChange={(event) => { setProvider(event.target.value); setAuthorization(null); setStatus(null) }}><option value="google">Google</option><option value="microsoft">Microsoft</option></select></label>
    <form onSubmit={(event) => {
      event.preventDefault()
      const epoch = getSessionEpoch()
      void run(async () => {
        await oauthRequest('/client', 'PUT', { provider, client_id: clientId, secret_reference: secretReference || null })
        if (alive.current && epoch === getSessionEpoch()) { setStatus('Client configuration saved. Start a fresh sign-in below.'); setAuthorization(null) }
      })
    }}>
      <label>Registered client ID<input value={clientId} maxLength={300} onChange={(event) => setClientId(event.target.value)} required /></label>
      <label>Client secret vault reference, if required<input value={secretReference} pattern="vault:[a-f0-9]{32}" onChange={(event) => setSecretReference(event.target.value)} /></label>
      <button className="button subtle" disabled={busy}>Save OAuth client</button>
    </form>
    <form onSubmit={(event) => {
      event.preventDefault()
      const epoch = getSessionEpoch()
      const mail = provider === 'google' ? 'https://www.googleapis.com/auth/gmail.send' : 'Mail.Send'
      const calendar = provider === 'google' ? 'https://www.googleapis.com/auth/calendar.events.owned' : 'Calendars.ReadWrite'
      const scopes = [...(access !== 'calendar' ? [mail] : []), ...(access !== 'mail' ? [calendar] : [])]
      void run(async () => {
        const result = await oauthRequest<{ authorization_url: string; redirect_uri: string }>('/start', 'POST', { provider, label, scopes, transmission_consent: consent })
        const url = new URL(result.authorization_url)
        if (url.protocol !== 'https:' || !['accounts.google.com', 'login.microsoftonline.com'].includes(url.hostname)) throw new Error('Invalid provider authorization URL.')
        if (alive.current && epoch === getSessionEpoch()) { setAuthorization(url.href); setStatus(`Register this exact loopback callback where required: ${result.redirect_uri}`) }
      })
    }}>
      <label>Sign-in account label<input value={label} maxLength={100} onChange={(event) => setLabel(event.target.value)} required /></label>
      <label>OAuth access<select value={access} onChange={(event) => { setAccess(event.target.value); setAuthorization(null) }}><option value="mail">Mail send only</option><option value="calendar">Calendar creation only</option><option value="both">Mail and calendar</option></select></label>
      <label><input type="checkbox" checked={consent} onChange={(event) => { setConsent(event.target.checked); if (!event.target.checked) setAuthorization(null) }} />I consent to connecting the selected provider and scopes.</label>
      <button className="button primary" disabled={busy || !consent}>Prepare sign-in link</button>
    </form>
    {authorization && <button className="button primary" onClick={() => {
      const bridge = (window as unknown as { __TAURI__?: { core: { invoke: (command: string, args: { url: string }) => Promise<unknown> } } }).__TAURI__
      if (bridge) void run(async () => { await bridge.core.invoke('open_oauth_url', { url: authorization }) })
      else window.open(authorization, '_blank', 'noopener,noreferrer')
    }}>Open provider sign-in</button>}
    <button className="button subtle" disabled={busy} onClick={() => {
      setAuthorization(null)
      const epoch = getSessionEpoch()
      void run(async () => { await oauthRequest('/cancel', 'POST'); if (alive.current && epoch === getSessionEpoch()) setStatus('Pending sign-in states cancelled. Already granted provider consent is managed at the provider.') })
    }}>Cancel pending sign-ins</button>
    {status && <p role="status">{status}</p>}
    <p className="helper-text">After provider consent, return here and refresh Connections. Expired or uncertain refresh attempts require a new sign-in. Refreshing an account invalidates its old action approvals.</p>
  </section>
}
