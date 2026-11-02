import { useEffect, useRef, useState } from 'react'
import { getSessionEpoch, workspaceRequest, type JsonRecord } from './api'
import DraftCommandPanel from './DraftCommandPanel'

type DraftFile = { path: string; base_sha: string; sha256: string; content: string; changed: boolean }
type Snapshot = { workspace_id: string; revision: number; status: string; files: DraftFile[] }

export default function AgentDraftPanel({ projectId }: { projectId: string }) {
  const [path, setPath] = useState('')
  const [hash, setHash] = useState('')
  const [snapshotFiles, setSnapshotFiles] = useState<{ path: string; source_sha256: string }[]>([])
  const [workspaceId, setWorkspaceId] = useState('')
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [selectedPath, setSelectedPath] = useState('')
  const [content, setContent] = useState('')
  const [request, setRequest] = useState('')
  const [result, setResult] = useState<unknown>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const mounted = useRef(true)
  const generation = useRef(0)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; generation.current += 1 } }, [])
  const selected = snapshot?.files.find((item) => item.path === selectedPath)
  function addSnapshotSource() {
    if (!path.trim() || !/^[a-f0-9]{64}$/.test(hash)) { setError('Enter an inspected path and its exact SHA-256 hash.'); return }
    if (snapshotFiles.some((item) => item.path.replaceAll('\\', '/').toLowerCase() === path.replaceAll('\\', '/').toLowerCase())) { setError('This source is already selected.'); return }
    if (snapshotFiles.length >= 4) { setError('A snapshot supports at most four selected files.'); return }
    setSnapshotFiles((items) => [...items, { path, source_sha256: hash }]); setPath(''); setHash(''); setError('')
  }
  function createSnapshot() {
    const files = [...snapshotFiles]
    if (path || hash) {
      if (!path.trim() || !/^[a-f0-9]{64}$/.test(hash)) { setError('Complete the current path and hash, or clear both fields.'); return }
      files.push({ path, source_sha256: hash })
    }
    if (!files.length || files.length > 4) { setError('Select between one and four inspected files.'); return }
    void act('draft_create', { files })
  }
  async function act(tool: string, arguments_: JsonRecord, queueJob = false) {
    const current = ++generation.current
    const epoch = getSessionEpoch()
    setBusy(true); setError('')
    try {
      const output = await workspaceRequest<{ status: string; detail?: string; result?: unknown }>(queueJob ? '/agents' : '/knowledge/execute', 'POST', queueJob ? { role: 'coder', arguments: arguments_, dependencies: [] } : { tool, arguments: arguments_ }, projectId)
      if (!mounted.current || current !== generation.current || epoch !== getSessionEpoch()) return
      setResult(output)
      if (output.status === 'success' && output.result && typeof output.result === 'object' && 'files' in output.result) {
        const next = output.result as Snapshot
        setSnapshot(next); setWorkspaceId(next.workspace_id)
        const file = next.files.find((item) => item.path === selectedPath) ?? next.files[0]
        setSelectedPath(file?.path ?? ''); setContent(file?.content ?? '')
      } else if (!['success', 'approval_required', 'queued'].includes(output.status)) setError(output.detail ?? 'Draft operation was denied.')
    } catch (reason) {
      if (mounted.current && current === generation.current && epoch === getSessionEpoch()) setError(reason instanceof Error ? reason.message : 'Draft operation failed.')
    } finally {
      if (mounted.current && current === generation.current && epoch === getSessionEpoch()) setBusy(false)
    }
  }
  return <section className="surface">
    <h3>Isolated draft workspace</h3>
    <p className="helper-text">Text snapshots stay in the private database. Draft edits do not write project files or run commands. After approval, inspect the workspace again to load its latest revision.</p>
    {error && <p role="alert">{error}</p>}
    <form onSubmit={(event) => { event.preventDefault(); createSnapshot() }}>
      <label>Snapshot source path<input value={path} maxLength={500} onChange={(event) => setPath(event.target.value)} required={!snapshotFiles.length} /></label>
      <label>Inspected source hash<input value={hash} pattern="[a-f0-9]{64}" onChange={(event) => setHash(event.target.value)} required={!snapshotFiles.length} /></label>
      <button type="button" className="button subtle" disabled={busy || snapshotFiles.length >= 4} onClick={addSnapshotSource}>Add source to snapshot</button>
      {snapshotFiles.length > 0 && <ul>{snapshotFiles.map((item, index) => <li key={`${item.path}:${item.source_sha256}`}>{item.path} <button type="button" disabled={busy} onClick={() => setSnapshotFiles((items) => items.filter((_, position) => position !== index))}>Remove {item.path}</button></li>)}</ul>}
      <button className="button primary" disabled={busy}>Request isolated snapshot</button>
    </form>
    <form onSubmit={(event) => { event.preventDefault(); void act('draft_inspect', { workspace_id: workspaceId }) }}>
      <label>Draft workspace ID<input value={workspaceId} pattern="[a-f0-9]{32}" onChange={(event) => { setWorkspaceId(event.target.value); setSnapshot(null) }} required /></label>
      <button className="button subtle" disabled={busy}>Inspect draft workspace</button>
      <button type="button" className="button subtle" disabled={busy} onClick={() => void act('draft_list', {})}>List draft workspaces</button>
    </form>
    {snapshot && <>
      <p>Revision {snapshot.revision} · {snapshot.status}</p>
      <label>Snapshot file<select value={selectedPath} onChange={(event) => { const file = snapshot.files.find((item) => item.path === event.target.value); setSelectedPath(event.target.value); setContent(file?.content ?? '') }}>{snapshot.files.map((file) => <option key={file.path} value={file.path}>{file.path}{file.changed ? ' (changed)' : ''}</option>)}</select></label>
      <label>Draft text<textarea value={content} maxLength={20000} onChange={(event) => setContent(event.target.value)} disabled={busy || snapshot.status !== 'open'} /></label>
      <button className="button primary" disabled={busy || !selected || content === selected.content || snapshot.status !== 'open'} onClick={() => void act('draft_edit', { workspace_id: snapshot.workspace_id, expected_revision: snapshot.revision, path: selectedPath, before_sha256: selected!.sha256, content })}>Request draft edit</button>
      <label>Model correction request<textarea value={request} maxLength={2000} onChange={(event) => setRequest(event.target.value)} /></label>
      <button className="button subtle" disabled={busy || !selected || !request.trim() || snapshot.status !== 'open'} onClick={() => void act('draft_ai_edit', { workspace_id: snapshot.workspace_id, expected_revision: snapshot.revision, path: selectedPath, before_sha256: selected!.sha256, request })}>Request model draft edit</button>
      <button className="button subtle" disabled={busy || !selected || !request.trim() || snapshot.status !== 'open'} onClick={() => void act('draft_ai_edit', { workspace_id: snapshot.workspace_id, expected_revision: snapshot.revision, path: selectedPath, before_sha256: selected!.sha256, request }, true)}>Queue coder in isolated draft</button>
      <button className="button primary" disabled={busy || snapshot.status !== 'open' || !snapshot.files.some((file) => file.changed)} onClick={() => void act('draft_merge', { workspace_id: snapshot.workspace_id, expected_revision: snapshot.revision })}>Prepare reviewed merge proposals</button>
      <button className="button subtle" disabled={busy || snapshot.status !== 'open'} onClick={() => void act('draft_close', { workspace_id: snapshot.workspace_id, expected_revision: snapshot.revision })}>Request close draft</button>
      <p className="helper-text">Merge proposals appear in Coding Studio checkpoints. Each exact diff still needs separate apply approval. A changed user file blocks merge preparation.</p>
      <DraftCommandPanel key={`${projectId}:${snapshot.workspace_id}:${snapshot.revision}`} projectId={projectId} workspaceId={snapshot.workspace_id} revision={snapshot.revision} />
    </>}
    {result !== null && <details open><summary>Draft result and proposal evidence</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(result, null, 2)}</pre></details>}
  </section>
}
