import { useState } from 'react'
import type { JsonRecord } from './api'

export default function PinterestPublisher({ account, onRequest, busy }: { account: JsonRecord; onRequest: (arguments_: JsonRecord) => Promise<void>; busy: boolean }) {
  const [board, setBoard] = useState('')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [alternative, setAlternative] = useState('')
  const [image, setImage] = useState('')
  const [consent, setConsent] = useState(false)
  return <form onSubmit={(event) => {
    event.preventDefault()
    if (!consent || busy) return
    setConsent(false)
    void onRequest({ operation_id: crypto.randomUUID().replaceAll('-', ''), account_id: account.account_id,
      account_revision: account.revision, board_id: board, title, description, alt_text: alternative, image_url: image, publish_to_board: true })
  }}>
    <h4>Pinterest image pin</h4>
    <label>Exact Pinterest board ID<input value={board} pattern="[0-9]{1,30}" onChange={(event) => { setBoard(event.target.value); setConsent(false) }} required /></label>
    <label>Exact pin title<input value={title} maxLength={100} onChange={(event) => { setTitle(event.target.value); setConsent(false) }} required /></label>
    <label>Pin description<textarea value={description} maxLength={800} onChange={(event) => { setDescription(event.target.value); setConsent(false) }} /></label>
    <label>Image alternative text<textarea value={alternative} maxLength={500} onChange={(event) => { setAlternative(event.target.value); setConsent(false) }} required /></label>
    <label>Exact public HTTPS image URL<input type="url" value={image} maxLength={2000} onChange={(event) => { setImage(event.target.value); setConsent(false) }} required /></label>
    <label><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />I approve this exact board, content and image URL for separate pin-publication approval, including Pinterest fetching the image.</label>
    <button className="button primary" disabled={busy || !consent || !title.trim() || !alternative.trim()}>Request Pinterest pin approval</button>
    <p className="helper-text">The existing board determines visibility. Pinterest fetches the image externally; CAM does not preview, upload, pin its bytes or verify image ownership. No ads, video, scheduling or automatic retries.</p>
  </form>
}
