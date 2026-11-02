import { useEffect, useRef, useState } from 'react'
import { getSessionEpoch, workspaceRequest, type JsonRecord } from './api'

const kinds = { lead: 'Lead', proposal: 'Proposal', order: 'Order', follow_up: 'Follow-up', content_draft: 'Content draft' }
const statuses = ['draft', 'active', 'done', 'archived']
type RecordDraft = { title: string; content: string; kind: string; status: string }
type RecordPage = { items: JsonRecord[]; next_cursor: string | null }
const empty = (): RecordDraft => ({ title: '', content: '', kind: 'lead', status: 'draft' })
const text = (value: unknown) => String(value ?? '')

export default function WorkRecordsPanel({ projectId }: { projectId: string | null }) {
  const [items, setItems] = useState<JsonRecord[]>([])
  const [kind, setKind] = useState(''), [status, setStatus] = useState(''), [query, setQuery] = useState('')
  const [cursor, setCursor] = useState<string | null>(null)
  const [applied, setApplied] = useState('')
  const [draft, setDraft] = useState<RecordDraft>(empty)
  const [editing, setEditing] = useState<{ id: string; revision: number } | null>(null)
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('')
  const generation = useRef(0), locked = useRef(false)
  useEffect(() => () => { generation.current += 1 }, [])
  const filterKey = JSON.stringify({ kind, status, query: query.trim() })
  const draftDirty = Boolean(draft.title || draft.content || draft.kind !== 'lead' || draft.status !== 'draft')

  function endpoint(next?: string | null) {
    const parameters = new URLSearchParams()
    if (kind) parameters.set('kind', kind)
    if (status) parameters.set('status', status)
    if (query.trim()) parameters.set('query', query.trim())
    if (next) parameters.set('cursor', next)
    const encoded = parameters.toString()
    return `/records/page${encoded ? `?${encoded}` : ''}`
  }
  async function act(operation?: () => Promise<unknown>, next?: string | null) {
    if (locked.current) return
    locked.current = true
    const current = ++generation.current, epoch = getSessionEpoch(), selectedFilters = filterKey
    setBusy(true); setError(''); setNotice('')
    try {
      if (operation) await operation()
      if (current !== generation.current || epoch !== getSessionEpoch()) return
      if (operation) { setEditing(null); setDraft(empty()); setNotice('Record saved. No message was sent or published.') }
      const page = await workspaceRequest<RecordPage>(endpoint(next), 'GET', undefined, projectId)
      if (current !== generation.current || epoch !== getSessionEpoch()) return
      setItems((previous) => next ? [...previous, ...page.items.filter((item) => !previous.some((existing) => existing.record_id === item.record_id))].slice(-200) : page.items)
      setCursor(page.next_cursor); setApplied(selectedFilters)
    } catch (reason) {
      if (current === generation.current && epoch === getSessionEpoch()) setError(reason instanceof Error ? reason.message : 'Work records are unavailable. Refresh before retrying.')
    } finally {
      locked.current = false
      if (current === generation.current && epoch === getSessionEpoch()) setBusy(false)
    }
  }
  function edit(item: JsonRecord) {
    setEditing({ id: text(item.record_id), revision: Number(item.revision) })
    setDraft({ title: text(item.title), content: text(item.content), kind: text(item.kind), status: text(item.status) })
    setError(''); setNotice('')
  }
  function save() {
    const selected = editing, value = { ...draft }
    void act(() => workspaceRequest(selected ? `/records/${encodeURIComponent(selected.id)}` : '/records', selected ? 'PUT' : 'POST',
      selected ? { ...value, expected_revision: selected.revision } : value, projectId))
  }
  function changeStatus(item: JsonRecord, selected: string) {
    void act(() => workspaceRequest(`/records/${encodeURIComponent(text(item.record_id))}`, 'PUT', {
      title: item.title, content: item.content, kind: item.kind, status: selected, expected_revision: item.revision,
    }, projectId))
  }
  return <section className="surface" aria-label="Business and work records">
    <h3>Business and work records</h3>
    <p>Track leads, proposals, orders, follow-ups and content drafts. These are local records; saving does not send, publish or create an external order.</p>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <form onSubmit={(event) => { event.preventDefault(); void act() }}>
      <label>Filter record type<select value={kind} disabled={busy} onChange={(event) => setKind(event.target.value)}><option value="">All types</option>{Object.entries(kinds).map(([value, title]) => <option key={value} value={value}>{title}</option>)}</select></label>
      <label>Filter record status<select value={status} disabled={busy} onChange={(event) => setStatus(event.target.value)}><option value="">All statuses</option>{statuses.map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>Search saved records<input value={query} maxLength={200} disabled={busy} onChange={(event) => setQuery(event.target.value)} /></label>
      <button className="button subtle" disabled={busy}>Apply filters and refresh</button>
    </form>
    {applied && applied !== filterKey && <p>Filters changed. Apply them to refresh the results.</p>}
    <form onSubmit={(event) => { event.preventDefault(); save() }}>
      <h4>{editing ? 'Edit saved record' : 'Create record'}</h4>
      <label>Record type<select value={draft.kind} disabled={busy} onChange={(event) => setDraft({ ...draft, kind: event.target.value })}>{Object.entries(kinds).map(([value, title]) => <option key={value} value={value}>{title}</option>)}</select></label>
      <label>Record title<input value={draft.title} minLength={1} maxLength={200} required disabled={busy} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label>
      <label>Record details<textarea value={draft.content} maxLength={32000} disabled={busy} onChange={(event) => setDraft({ ...draft, content: event.target.value })} /></label>
      <label>Record status<select value={draft.status} disabled={busy} onChange={(event) => setDraft({ ...draft, status: event.target.value })}>{statuses.map((value) => <option key={value}>{value}</option>)}</select></label>
      {editing && <p>Editing observed revision {editing.revision}. A newer edit will be preserved and this save will refuse.</p>}
      <button className="button primary" disabled={busy}>{editing ? 'Save record changes' : 'Save new record'}</button>
      {(editing || draftDirty) && <button type="button" className="button subtle" disabled={busy} onClick={() => { setEditing(null); setDraft(empty()) }}>{editing ? 'Discard record edit' : 'Clear new record draft'}</button>}
    </form>
    {items.map((item) => <article key={text(item.record_id)}>
      <h4>{text(item.title)}</h4><p>{text(item.kind)} · {text(item.status)} · revision {text(item.revision)}</p>
      <p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{text(item.content)}</p>
      <button type="button" className="button subtle" disabled={busy || editing !== null || draftDirty} onClick={() => edit(item)}>Edit {text(item.title)}</button>
      <button type="button" className="button subtle" disabled={busy || editing !== null || draftDirty} onClick={() => changeStatus(item, item.status === 'done' ? 'active' : 'done')}>{item.status === 'done' ? 'Reopen' : 'Mark done'}: {text(item.title)}</button>
      <button type="button" className="button subtle" disabled={busy || editing !== null || draftDirty} onClick={() => changeStatus(item, item.status === 'archived' ? 'draft' : 'archived')}>{item.status === 'archived' ? 'Restore' : 'Archive'}: {text(item.title)}</button>
    </article>)}
    {cursor && <button type="button" className="button subtle" disabled={busy || applied !== filterKey} onClick={() => void act(undefined, cursor)}>Load more records</button>}
    <p className="helper-text">Fifty records per page; only the last 200 loaded records stay on screen. New edits can reorder results between pages; refresh for recent changes. No records are permanently deleted here.</p>
  </section>
}
