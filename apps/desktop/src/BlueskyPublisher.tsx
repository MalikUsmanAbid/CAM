import { useState } from 'react'
import type { JsonRecord } from './api'

import { newRecordKey } from './bluesky-record-key'

export default function BlueskyPublisher({ account, onRequest, busy }: { account: JsonRecord; onRequest: (arguments_: JsonRecord) => Promise<void>; busy: boolean }) {
  const [did, setDid] = useState('')
  const [body, setBody] = useState('')
  const [consent, setConsent] = useState(false)
  return <form onSubmit={(event) => {
    event.preventDefault()
    if (!consent || busy) return
    setConsent(false)
    void onRequest({ operation_id: crypto.randomUUID().replaceAll('-', ''), account_id: account.account_id,
      account_revision: account.revision, service_base: account.service_base, repository_did: did,
      record_key: newRecordKey(), created_at: new Date().toISOString(), body, publish_publicly: true })
  }}>
    <h4>Bluesky public text post</h4><p>Saved PDS: {String(account.service_base)}</p>
    <label>Exact Bluesky repository DID<input value={did} pattern="did:plc:[a-z2-7]{24}" onChange={(event) => { setDid(event.target.value); setConsent(false) }} required /></label>
    <label>Exact Bluesky text<textarea value={body} maxLength={300} onChange={(event) => { setBody(event.target.value); setConsent(false) }} required /></label>
    <label><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />I reviewed this exact public post, repository and PDS for separate publication approval.</label>
    <button className="button primary" disabled={busy || !consent || !body.trim()}>Request Bluesky post approval</button>
    <p className="helper-text">Access JWT must already exist in protected backend storage. No password login, media, rich-text facets or automatic refresh/retry. The approval includes the exact timestamp and record key.</p>
  </form>
}
