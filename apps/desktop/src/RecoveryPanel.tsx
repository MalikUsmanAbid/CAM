import { useEffect, useRef, useState } from 'react'
import { continueStudioTool, decideApproval, executeStudioTool } from './api'
import type { StudioResult } from './api'

type Change = { change_id: string; path: string; proposal_sha256: string; status: string; checkpoint_locations: string[] }
type Observation = { observation_sha256: string; mode: string; final_status: string | null; retained_checkpoint: string; diff: string }
type Kind = 'list' | 'inspect' | 'recover' | 'review'

export default function RecoveryPanel({ projectId, authenticated, editorBusy = false, getEditorRevision = () => 0, onReview }: { projectId: string; authenticated: boolean; editorBusy?: boolean; getEditorRevision?: () => number; onReview?: (result: StudioResult, editorRevision: number) => void }) {
  const [changes, setChanges] = useState<Change[]>([])
  const [selected, setSelected] = useState<Change | null>(null)
  const [observation, setObservation] = useState<Observation | null>(null)
  const [pending, setPending] = useState<{ result: StudioResult; kind: Kind; editorRevision: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [outcome, setOutcome] = useState<string | null>(null)
  const generation = useRef(0)
  useEffect(() => {
    const current = generation
    current.current++
    queueMicrotask(() => { setChanges([]); setSelected(null); setObservation(null); setPending(null); setError(null); setOutcome(null); setBusy(false) })
    return () => { current.current++ }
  }, [projectId, authenticated])

  const display = (result: StudioResult, kind: Kind, editorRevision: number) => {
    if (kind === 'list') setChanges((result.result?.changes ?? []) as Change[])
    if (kind === 'inspect') setObservation(result.result as unknown as Observation)
    if (kind === 'review') { setSelected(null); setObservation(null); onReview?.(result, editorRevision) }
    if (kind === 'recover') {
      const status = String(result.result?.status)
      setChanges((items) => items.map((item) => item.change_id === selected?.change_id ? { ...item, status } : item))
      setObservation(null); setSelected(null)
      setOutcome(`Recovery verified. Checkpoint state: ${status}.`)
    }
  }
  const request = async (kind: Kind, change = selected) => {
    if (!authenticated || editorBusy || (kind !== 'list' && !change)) return
    const current = ++generation.current
    const editorRevision = getEditorRevision()
    setBusy(true); setError(null); setPending(null); setOutcome(null)
    if (kind === 'inspect') { setSelected(change); setObservation(null) }
    if (kind === 'list') { setSelected(null); setObservation(null) }
    const args = change ? { change_id: change.change_id, path: change.path, proposal_sha256: change.proposal_sha256 } : {}
    try {
      const result = await executeStudioTool(kind === 'list' ? 'studio_changes' : kind === 'inspect' ? 'studio_inspect_recovery' : kind === 'review' ? 'studio_inspect_change' : 'studio_recover', kind === 'list' ? {} : kind === 'recover' ? { ...args, observation_sha256: observation?.observation_sha256 } : args, projectId)
      if (current !== generation.current) return
      if (kind === 'review' && editorRevision !== getEditorRevision()) throw new Error('Editor changed; request checkpoint review again.')
      if (result.status === 'approval_required') { setPending({ result, kind, editorRevision }); return }
      if (result.status !== 'success') throw new Error(result.detail)
      display(result, kind, editorRevision)
    } catch (reason) { if (current === generation.current) setError(reason instanceof Error ? reason.message : 'Recovery request failed.') }
    finally { if (current === generation.current) setBusy(false) }
  }
  const decide = async (allow: boolean) => {
    if (!pending?.result.approval || !authenticated || editorBusy) return
    const id = pending.result.approval.request_id
    const current = ++generation.current
    setBusy(true); setError(null)
    try {
      if (pending.kind === 'review' && pending.editorRevision !== getEditorRevision()) throw new Error('Editor changed; request checkpoint review again.')
      await decideApproval(id, allow, projectId)
      if (current !== generation.current) return
      if (pending.kind === 'review' && pending.editorRevision !== getEditorRevision()) throw new Error('Editor changed; request checkpoint review again.')
      if (!allow) { setPending(null); setOutcome('Recovery action rejected.'); return }
      const result = await continueStudioTool(id, projectId)
      if (current !== generation.current) return
      if (pending.kind === 'review' && pending.editorRevision !== getEditorRevision()) throw new Error('Editor changed; request checkpoint review again.')
      if (result.status !== 'success') throw new Error(result.detail)
      display(result, pending.kind, pending.editorRevision); setPending(null)
    } catch (reason) { if (current === generation.current) { setError(reason instanceof Error ? reason.message : 'Recovery failed; inspect again.'); setPending(null); setObservation(null) } }
    finally { if (current === generation.current) setBusy(false) }
  }
  return <section className="surface"><h2>Checkpoints and recovery</h2>
    <p>Original versions remain at the locations below when an operation retires them. They accumulate on disk and are not automatically purged. Interrupted changes are inspected before a fresh recovery approval. Later user files are preserved; unsupported states require manual recovery.</p>
    <button className="button subtle" disabled={busy || editorBusy || !authenticated} onClick={() => void request('list')}>Load checkpoint history</button>
    {error && <p role="alert">{error}</p>}
    {outcome && <p role="status">{outcome}</p>}
    <ul>{changes.map((change) => <li key={change.change_id}><p>{change.path}: {change.status}</p><p>Possible retained locations: {change.checkpoint_locations.join(', ')}</p>{onReview && (change.status === 'proposed' || change.status === 'applied') && <button className="button subtle" disabled={busy || editorBusy || !authenticated} onClick={() => void request('review', change)}>Review {change.path} checkpoint {change.change_id.slice(0, 8)}</button>}{(change.status === 'applying' || change.status === 'rolling_back' || change.status.startsWith('recovery_')) && <button className="button subtle" disabled={busy || editorBusy || !authenticated} onClick={() => void request('inspect', change)}>Inspect interrupted {change.path}</button>}</li>)}</ul>
    {observation && <div><p>Recovery action: {observation.mode}. Resulting checkpoint state: {observation.final_status ?? 'unsupported'}.</p><p>Retained location: {observation.retained_checkpoint}</p><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}><code>{observation.diff}</code></pre><button className="button" disabled={busy || !authenticated || observation.mode === 'unsupported' || Boolean(pending)} onClick={() => void request('recover')}>Request recovery approval</button></div>}
    {pending && <div><p>{pending.kind === 'recover' ? 'Exact recovery approval required' : 'Checkpoint read approval required'}. Project: {projectId}. Policy revision: {String(pending.result.approval?.preview.policy_revision)}.</p><button className="button" disabled={busy} onClick={() => void decide(true)}>{pending.kind === 'recover' ? 'Approve exact recovery' : 'Approve checkpoint read'}</button><button className="button subtle" disabled={busy} onClick={() => void decide(false)}>Reject checkpoint action</button></div>}
  </section>
}
