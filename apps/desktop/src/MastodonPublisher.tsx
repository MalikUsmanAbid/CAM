import { useState } from 'react'
import type { JsonRecord } from './api'

export default function MastodonPublisher({ account, onRequest, busy }: {
  account: JsonRecord; onRequest: (arguments_: JsonRecord) => Promise<void>; busy: boolean
}) {
  const [body, setBody] = useState('')
  const [visibility, setVisibility] = useState('private')
  const [warning, setWarning] = useState('')
  const [sensitive, setSensitive] = useState(false)
  const [consent, setConsent] = useState(false)
  return <form onSubmit={(event) => {
    event.preventDefault()
    if (!consent || busy) return
    const arguments_ = { operation_id: crypto.randomUUID().replaceAll('-', ''), account_id: account.account_id,
      account_revision: account.revision, service_base: account.service_base, body, visibility,
      confirm_visibility: true, sensitive, spoiler_text: warning }
    setConsent(false)
    void onRequest(arguments_)
  }}>
    <h4>Mastodon text post</h4><p>Saved instance: {String(account.service_base)}</p>
    <label>Exact Mastodon text<textarea value={body} maxLength={500} onChange={(event) => { setBody(event.target.value); setConsent(false) }} required /></label>
    <label>Mastodon visibility<select value={visibility} onChange={(event) => { setVisibility(event.target.value); setConsent(false) }}><option value="private">Followers only</option><option value="unlisted">Unlisted public</option><option value="public">Public</option></select></label>
    <label>Content warning, optional<input value={warning} maxLength={500} onChange={(event) => { setWarning(event.target.value); setConsent(false) }} /></label>
    <label><input type="checkbox" checked={sensitive} onChange={(event) => { setSensitive(event.target.checked); setConsent(false) }} />Mark as sensitive</label>
    <label><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />I reviewed this exact text, instance and audience for separate publication approval.</label>
    <button className="button primary" disabled={busy || !consent || !body.trim()}>Request Mastodon post approval</button>
    <p className="helper-text">No media, direct messages, scheduling or automatic retries. Instance rules may impose stricter limits. Provider acceptance is not proof of delivery to every follower.</p>
  </form>
}
