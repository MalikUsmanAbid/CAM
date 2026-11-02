import { useState } from 'react'
import type { JsonRecord } from './api'

export default function LinkedInPublisher({ account, onRequest, busy }: {
  account: JsonRecord; onRequest: (arguments_: JsonRecord) => Promise<void>; busy: boolean
}) {
  const [author, setAuthor] = useState('')
  const [body, setBody] = useState('')
  const [consent, setConsent] = useState(false)
  return <form onSubmit={(event) => {
    event.preventDefault()
    if (!consent || busy) return
    const arguments_ = { operation_id: crypto.randomUUID().replaceAll('-', ''), account_id: account.account_id,
      account_revision: account.revision, author, body, publish_publicly: true }
    setConsent(false)
    void onRequest(arguments_)
  }}>
    <h4>LinkedIn public member post</h4>
    <label>Exact LinkedIn member URN<input value={author} pattern="urn:li:person:[A-Za-z0-9_-]{1,100}" onChange={(event) => { setAuthor(event.target.value); setConsent(false) }} required /></label>
    <label>Exact public post<textarea value={body} maxLength={3000} onChange={(event) => { setBody(event.target.value); setConsent(false) }} required /></label>
    <label><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />I want this exact text publicly posted by this connected member after approval.</label>
    <button className="button primary" disabled={busy || !consent || !body.trim()}>Request public post approval</button>
    <p className="helper-text">Requires a protected member token with w_member_social and provider app access. This requests an exact approval; it does not publish immediately. Organization posts, media and LinkedIn OAuth setup are separate capabilities.</p>
  </form>
}
