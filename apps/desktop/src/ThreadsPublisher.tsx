import { useState } from 'react'
import type { JsonRecord } from './api'

export default function ThreadsPublisher({ account, onRequest, busy }: { account: JsonRecord; onRequest: (arguments_: JsonRecord) => Promise<void>; busy: boolean }) {
  const [body, setBody] = useState('')
  const [consent, setConsent] = useState(false)
  return <form onSubmit={(event) => {
    event.preventDefault()
    if (!consent || busy) return
    setConsent(false)
    void onRequest({ operation_id: crypto.randomUUID().replaceAll('-', ''), account_id: account.account_id,
      account_revision: account.revision, body, publish_publicly: true })
  }}>
    <h4>Threads public text post</h4>
    <label>Exact Threads text<textarea value={body} maxLength={500} onChange={(event) => { setBody(event.target.value); setConsent(false) }} required /></label>
    <label><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />I reviewed this exact public post with replies open to everyone for separate publication approval.</label>
    <button className="button primary" disabled={busy || !consent || !body.trim()}>Request Threads post approval</button>
    <p className="helper-text">Uses the official text auto-publish request. URLs may generate a provider link preview. No media, scheduling, automatic retries or account login. HTTP acceptance alone is not live verification.</p>
  </form>
}
