import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { getSessionEpoch, workspaceRequest, type JsonRecord } from './api'

export default function ExtendedPublisher({ account }: { account: JsonRecord }) {
  const provider = String(account.provider)
  const [text, setText] = useState(''), [title, setTitle] = useState(''), [target, setTarget] = useState('')
  const [media, setMedia] = useState(''), [hash, setHash] = useState(''), [alt, setAlt] = useState('')
  const [privacy, setPrivacy] = useState(''), [category, setCategory] = useState('')
  const [kids, setKids] = useState(false), [ai, setAi] = useState(false), [consent, setConsent] = useState(false)
  const [creator, setCreator] = useState<JsonRecord | null>(null), [creatorHash, setCreatorHash] = useState('')
  const [duration, setDuration] = useState(''), [budgetRevision, setBudgetRevision] = useState('0')
  const [unit, setUnit] = useState(''), [daily, setDaily] = useState(''), [charges, setCharges] = useState(false)
  const [result, setResult] = useState<unknown>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const generation = useRef(0)
  useEffect(() => () => { generation.current += 1 }, [])
  const binding = { account_id: account.account_id, account_revision: account.revision }
  async function execute(tool: string, arguments_: JsonRecord) {
    const current = ++generation.current, epoch = getSessionEpoch()
    setBusy(true); setError('')
    try {
      const response = await workspaceRequest<{ status: string; detail?: string; result?: JsonRecord }>('/accounts/execute', 'POST', { tool, arguments: arguments_ })
      if (current !== generation.current || epoch !== getSessionEpoch()) return
      setResult(response)
      if (response.status === 'success' && response.result) {
        if (tool === 'account_media_inspect') setHash(String(response.result.sha256))
        const budget = response.result.budget as JsonRecord | undefined
        if (budget) { setBudgetRevision(String(budget.revision)); if (budget.unit_micro_usd != null) setUnit(String(budget.unit_micro_usd)); if (budget.daily_micro_usd != null) setDaily(String(budget.daily_micro_usd)); setCharges(false) }
        if (response.result.budget_revision) setBudgetRevision(String(response.result.budget_revision))
        const operations = response.result.operations as JsonRecord[] | undefined
        const direct = response.result.receipt as JsonRecord | undefined
        const found = direct?.creator ? direct : operations?.map((item) => item.receipt as JsonRecord | null).find((item) => item?.creator)
        if (found?.creator && found.creator_contract_sha256) { setCreator(found.creator as JsonRecord); setCreatorHash(String(found.creator_contract_sha256)); setPrivacy(''); setConsent(false) }
      } else if (!['success', 'approval_required'].includes(response.status)) setError(response.detail ?? 'Publishing action failed. Inspect the durable receipt before retry.')
    } catch (reason) {
      if (current === generation.current && epoch === getSessionEpoch()) setError(reason instanceof Error ? reason.message : 'Publishing action failed.')
    } finally { if (current === generation.current && epoch === getSessionEpoch()) setBusy(false) }
  }
  function submit() {
    if (!consent || busy) return
    const arguments_: JsonRecord = { ...binding, operation_id: crypto.randomUUID().replaceAll('-', '') }
    if (provider === 'facebook') Object.assign(arguments_, { page_id: target, body: text, publish_publicly: true })
    if (provider === 'instagram') Object.assign(arguments_, { instagram_user_id: target, caption: text, image_url: media, alt_text: alt, publish_publicly: true })
    if (provider === 'x') Object.assign(arguments_, { body: text, budget_revision: Number(budgetRevision), maximum_micro_usd: Number(unit), publish_publicly: true })
    if (provider === 'youtube') Object.assign(arguments_, { path: media, media_sha256: hash, title, description: text, category_id: category, privacy, made_for_kids: kids, notify_subscribers: false, confirm_upload: true })
    if (provider === 'tiktok') Object.assign(arguments_, { body: text, video_url: media, duration_seconds: Number(duration), privacy, creator_contract_sha256: creatorHash,
      disable_comment: true, disable_duet: true, disable_stitch: true, ai_generated: ai, confirm_url_ownership: true, confirm_publish: true })
    setConsent(false)
    void execute(`account_${({ facebook: 'facebook_post', instagram: 'instagram_post', x: 'x_post', youtube: 'youtube_upload', tiktok: 'tiktok_post' } as Record<string, string>)[provider]}`, arguments_)
  }
  const change = (setter: (value: string) => void) => (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => { setter(event.target.value); setConsent(false) }
  return <section aria-label={`${provider} publishing`}>
    <h4>{provider.toUpperCase()} publication</h4>
    {error && <p role="alert">{error}</p>}
    <button className="button subtle" disabled={busy} onClick={() => void execute('account_publishing_status', binding)}>Inspect publishing receipts and budget</button>
    {provider === 'x' && <fieldset disabled={busy}><legend>Paid X request allowance</legend>
      <p>Disabled until separately approved. Enter conservative prices and limits in micro-USD (1 USD = 1,000,000). These values do not verify provider pricing.</p>
      <label>Current budget revision<input type="number" min="0" value={budgetRevision} onChange={(event) => { setBudgetRevision(event.target.value); setCharges(false); setConsent(false) }} /></label>
      <label>Maximum cost per request in micro-USD<input type="number" min="1" max="100000000" value={unit} onChange={(event) => { setUnit(event.target.value); setCharges(false); setConsent(false) }} /></label>
      <label>Daily allowance in micro-USD<input type="number" min="0" max="1000000000" value={daily} onChange={(event) => { setDaily(event.target.value); setCharges(false); setConsent(false) }} /></label>
      <label><input type="checkbox" checked={charges} onChange={(event) => setCharges(event.target.checked)} />I accept provider charges within this configured allowance for separate financial approval.</label>
      <button className="button subtle" disabled={!charges || !unit || !daily} onClick={() => { setCharges(false); void execute('account_x_budget', { ...binding, expected_revision: Number(budgetRevision), enabled: true, unit_micro_usd: Number(unit), daily_micro_usd: Number(daily), accept_charges: true }) }}>Request budget approval</button>
      <button className="button subtle" disabled={!unit} onClick={() => void execute('account_x_budget', { ...binding, expected_revision: Number(budgetRevision), enabled: false, unit_micro_usd: Number(unit), daily_micro_usd: 0, accept_charges: true })}>Request paid publishing disable</button>
    </fieldset>}
    {provider === 'tiktok' && <><button className="button subtle" disabled={busy} onClick={() => void execute('account_tiktok_creator', { ...binding, operation_id: crypto.randomUUID().replaceAll('-', '') })}>Request current creator settings</button>
      {creator && <p>Creator: {String(creator.creator_nickname)} (@{String(creator.creator_username)}) · maximum {String(creator.max_video_post_duration_sec)} seconds. Comments, Duets and Stitches are disabled for this export.</p>}</>}
    <form onSubmit={(event) => { event.preventDefault(); submit() }}>
      {['facebook', 'instagram'].includes(provider) && <label>{provider === 'facebook' ? 'Exact Page ID' : 'Exact Instagram business-user ID'}<input value={target} pattern="[0-9]{1,30}" onChange={change(setTarget)} required /></label>}
      {provider === 'youtube' && <><label>Video title<input value={title} maxLength={100} onChange={change(setTitle)} required /></label><label>YouTube category ID<input value={category} pattern="[0-9]{1,3}" onChange={change(setCategory)} required /></label></>}
      <label>{provider === 'youtube' ? 'Video description' : 'Exact post text'}<textarea value={text} maxLength={provider === 'x' ? 280 : provider === 'instagram' || provider === 'tiktok' ? 2200 : provider === 'youtube' ? 5000 : 12000} onChange={change(setText)} required={['facebook', 'x'].includes(provider)} /></label>
      {['instagram', 'youtube', 'tiktok'].includes(provider) && <label>{provider === 'youtube' ? 'Video path inside Personal workspace' : 'Exact public HTTPS media URL'}<input value={media} maxLength={2000} onChange={(event) => { setMedia(event.target.value); setHash(''); setConsent(false) }} required /></label>}
      {provider === 'instagram' && <label>Image alternative text<input value={alt} maxLength={500} onChange={change(setAlt)} required /></label>}
      {provider === 'youtube' && <><button type="button" className="button subtle" disabled={busy || !media} onClick={() => void execute('account_media_inspect', { path: media })}>Request video hash inspection</button><label>Inspected video SHA-256<input value={hash} pattern="[a-f0-9]{64}" onChange={change(setHash)} required /></label>
        <label>Video visibility<select value={privacy} onChange={change(setPrivacy)} required><option value="">Choose visibility</option>{['private', 'unlisted', 'public'].map((item) => <option key={item}>{item}</option>)}</select></label>
        <label><input type="checkbox" checked={kids} onChange={(event) => { setKids(event.target.checked); setConsent(false) }} />This video is made for kids.</label><p>Upload bound: 32 MiB. Subscriber notifications are off. Hash inspection does not verify codecs or video content.</p></>}
      {provider === 'tiktok' && <><label>TikTok visibility<select value={privacy} onChange={change(setPrivacy)} required><option value="">Choose from current creator options</option>{((creator?.privacy_level_options ?? []) as string[]).map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>Known video duration in seconds<input type="number" min="1" max={Number(creator?.max_video_post_duration_sec ?? 3600)} value={duration} onChange={change(setDuration)} required /></label>
        <label><input type="checkbox" checked={ai} onChange={(event) => { setAi(event.target.checked); setConsent(false) }} />This video is AI generated.</label></>}
      <label><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />I reviewed the exact account, content, destination and audience for separate publication approval.{['instagram', 'tiktok'].includes(provider) ? ' I own/control the media URL and approve the provider fetching it.' : ''}</label>
      <button className="button primary" disabled={busy || !consent || provider === 'tiktok' && !creatorHash}>Request exact publication approval</button>
    </form>
    <p className="helper-text">No login or automatic retries. After approval, inspect durable receipts. Acceptance does not prove completed processing, delivery or visibility.</p>
    {result !== null && <details><summary>Publication evidence</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(result, null, 2)}</pre></details>}
  </section>
}
