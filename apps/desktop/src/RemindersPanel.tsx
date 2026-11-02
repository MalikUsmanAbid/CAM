import { useEffect, useRef, useState } from 'react'
import { getSessionEpoch, workspaceRequest, type JsonRecord } from './api'

const text = (value: unknown) => String(value ?? '')

export default function RemindersPanel({ projectId }: { projectId: string | null }) {
  const [items, setItems] = useState<JsonRecord[]>([])
  const [selected, setSelected] = useState<JsonRecord | null>(null)
  const [due, setDue] = useState('')
  const [interval, setInterval] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const generation = useRef(0), lock = useRef(false)
  useEffect(() => () => { generation.current += 1 }, [])

  async function act(operation?: () => Promise<unknown>) {
    if (lock.current) return
    lock.current = true
    const current = ++generation.current, epoch = getSessionEpoch()
    setBusy(true); setError(''); setNotice('')
    try {
      if (operation) await operation()
      if (current !== generation.current || epoch !== getSessionEpoch()) return
      const result = await workspaceRequest<JsonRecord[]>('/reminders', 'GET', undefined, projectId)
      if (current !== generation.current || epoch !== getSessionEpoch()) return
      setItems(result); setSelected(null)
      if (operation) setNotice('Reminder updated. No task or external action was executed.')
    } catch (reason) {
      if (current === generation.current && epoch === getSessionEpoch()) setError(reason instanceof Error ? reason.message : 'Reminder action failed. Refresh its current state before retrying.')
    } finally {
      lock.current = false
      if (current === generation.current && epoch === getSessionEpoch()) setBusy(false)
    }
  }
  function edit(item: JsonRecord) {
    const instant = new Date(text(item.next_due))
    const local = new Date(instant.getTime() - instant.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
    setSelected(item); setDue(local); setInterval(item.interval_seconds == null ? '' : text(item.interval_seconds)); setError('')
  }
  function save() {
    if (!selected) return
    const instant = new Date(due)
    if (!Number.isFinite(instant.getTime())) { setError('Choose a valid local date and time.'); return }
    void act(() => workspaceRequest(`/reminders/${encodeURIComponent(text(selected.schedule_id))}`, 'PUT', {
      expected_contract_sha256: selected.contract_sha256, due_at: instant.toISOString(), interval_seconds: interval ? Number(interval) : null,
    }, projectId))
  }
  return <section className="surface" aria-label="Saved reminder schedules">
    <h3>Manage saved reminders</h3>
    <p>Change the next time and repeat interval, or cancel a schedule and its pending notifications. Existing occurrence history remains; task-review reminders never run tasks.</p>
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <button type="button" className="button subtle" disabled={busy} onClick={() => void act()}>Show schedules</button>
    {items.map((item) => <section key={text(item.schedule_id)}>
      <h4>{text(item.title)}</h4>
      <p>{text(item.status)} · {new Date(text(item.next_due)).toLocaleString()} · {text(item.kind)}</p>
      <p>{item.interval_seconds == null ? 'One occurrence' : `Repeats every ${text(item.interval_seconds)} seconds`}</p>
      <button type="button" className="button subtle" disabled={busy || item.status !== 'scheduled'} onClick={() => edit(item)}>Change time for {text(item.title)}</button>
      <button type="button" className="button subtle" disabled={busy || item.status === 'cancelled'} onClick={() => void act(() => workspaceRequest(`/reminders/${encodeURIComponent(text(item.schedule_id))}/cancel`, 'POST', {
        expected_contract_sha256: item.contract_sha256,
      }, projectId))}>Cancel {text(item.title)} and pending notifications</button>
    </section>)}
    {selected && <form onSubmit={(event) => { event.preventDefault(); save() }}>
      <h4>Reschedule {text(selected.title)}</h4>
      <label>New local date and time<input type="datetime-local" value={due} required disabled={busy} onChange={(event) => setDue(event.target.value)} /></label>
      <label>New repeat interval in seconds, optional<input type="number" min={60} max={31536000} value={interval} disabled={busy} onChange={(event) => setInterval(event.target.value)} /></label>
      <p>The original title, task binding and old notifications remain. A scheduler tick or another edit makes this form stale; refresh and inspect again.</p>
      <button className="button primary" disabled={busy}>Save reminder timing</button>
      <button type="button" className="button subtle" disabled={busy} onClick={() => setSelected(null)}>Discard timing edit</button>
    </form>}
  </section>
}
