import { useEffect, useRef, useState } from 'react'
import { continueStudioTool, decideApproval, executeStudioTool } from './api'
import type { StudioResult } from './api'
import CommandPanel from './CommandPanel'
import RecoveryPanel from './RecoveryPanel'

type Entry = { path: string; name: string; directory: boolean }
export default function CodingStudio({ projectId, authenticated }: { projectId: string | null; authenticated: boolean }) {
  const [directory, setDirectory] = useState('')
  const [entries, setEntries] = useState<Entry[]>([])
  const [file, setFile] = useState<{ path: string; content: string; sha256: string; exists?: boolean } | null>(null)
  const [newPath, setNewPath] = useState('')
  const [aiRequest, setAIRequest] = useState('')
  const [contextPaths, setContextPaths] = useState('')
  const checkpointBase = useRef<typeof file>(null)
  const [draft, setDraft] = useState('')
  const [proposal, setProposal] = useState<{ change_id: string; path: string; proposal_sha256: string; diff: string; before_exists?: boolean; applied?: boolean } | null>(null)
  const [outcome, setOutcome] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [truncated, setTruncated] = useState(false)
  const [pending, setPending] = useState<{ result: StudioResult; kind: 'list' | 'read' | 'propose' | 'apply' | 'rollback'; target: string } | null>(null)
  const generation = useRef(0)
  useEffect(() => {
    const generationRef = generation
    const current = ++generationRef.current
    if (!projectId || !authenticated) return
    const controller = new AbortController()
    queueMicrotask(() => {
      if (current !== generationRef.current) return
      setEntries([]); setFile(null); setError(null); setTruncated(false); setPending(null); setProposal(null); setOutcome(null); setDraft(''); setAIRequest(''); setContextPaths(''); setBusy(true)
    })
    void executeStudioTool('studio_list', { path: directory }, projectId, controller.signal).then((result) => {
      if (current !== generation.current) return
      if (result.status === 'approval_required') { setPending({ result, kind: 'list', target: directory || '/' }); return }
      if (result.status !== 'success') throw new Error(result.detail)
      setEntries((result.result?.entries ?? []) as Entry[])
      setTruncated(Boolean(result.result?.truncated))
    }).catch((reason: unknown) => { if (current === generation.current && !controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Project listing failed.') })
      .finally(() => { if (current === generation.current) setBusy(false) })
    return () => { generationRef.current++; controller.abort() }
  }, [projectId, authenticated, directory])
  const open = async (entry: Entry) => {
    if (entry.directory) { setDirectory(entry.path); return }
    if (!projectId) return
    const current = ++generation.current
    setBusy(true); setError(null); setFile(null); setPending(null); setProposal(null); setOutcome(null); setDraft(''); setAIRequest('')
    try {
      const result = await executeStudioTool('studio_read', { path: entry.path }, projectId)
      if (current !== generation.current) return
      if (result.status === 'approval_required') { setPending({ result, kind: 'read', target: entry.path }); return }
      if (result.status !== 'success') throw new Error(result.detail)
      displayResult(result, 'read')
    } catch (reason) { if (current === generation.current) setError(reason instanceof Error ? reason.message : 'Project read failed.') }
    finally { if (current === generation.current) setBusy(false) }
  }
  const displayResult = (result: StudioResult, kind: 'list' | 'read' | 'propose' | 'apply' | 'rollback') => {
    if (kind === 'list') { setEntries((result.result?.entries ?? []) as Entry[]); setTruncated(Boolean(result.result?.truncated)) }
    if (kind === 'read') { checkpointBase.current = null; const content = String(result.result?.content); setFile({ path: String(result.result?.path), content, sha256: String(result.result?.sha256), exists: true }); setDraft(content) }
    if (kind === 'propose') { checkpointBase.current = file; if (typeof result.result?.content === 'string') setDraft(result.result.content); setProposal(result.result as unknown as NonNullable<typeof proposal>) }
    if (kind === 'apply') { setFile((value) => value ? { ...value, exists: true, content: draft, sha256: String(result.result?.sha256) } : null); setProposal((value) => value ? { ...value, applied: true } : null); setOutcome(`Applied and verified. SHA-256: ${String(result.result?.sha256)}`) }
    if (kind === 'rollback') { setDraft(checkpointBase.current?.content ?? ''); setFile(proposal?.before_exists === false ? null : checkpointBase.current); setProposal(null); setOutcome('Checkpoint rollback verified.') }
  }
  const patchAction = async (kind: 'propose' | 'apply' | 'rollback') => {
    if (!projectId || !file || (kind !== 'propose' && !proposal)) return
    const current = ++generation.current
    setBusy(true); setError(null); setPending(null); setOutcome(null)
    try {
      const arguments_ = kind === 'propose' ? (file.exists === false ? { path: file.path, content: draft } : { path: file.path, before_sha256: file.sha256, content: draft }) : { change_id: proposal!.change_id, path: proposal!.path, proposal_sha256: proposal!.proposal_sha256 }
      const result = await executeStudioTool(kind === 'propose' ? (file.exists === false ? 'studio_propose_create' : 'studio_propose') : kind === 'apply' ? 'studio_apply' : proposal?.before_exists === false ? 'studio_rollback_create' : 'studio_rollback', arguments_, projectId)
      if (current !== generation.current) return
      if (result.status === 'approval_required') { setPending({ result, kind, target: file.path }); return }
      if (result.status !== 'success') throw new Error(result.detail)
      displayResult(result, kind)
    } catch (reason) { if (current === generation.current) setError(reason instanceof Error ? reason.message : 'Patch action failed.') }
    finally { if (current === generation.current) setBusy(false) }
  }
  const approveRead = async () => {
    const id = pending?.result.approval?.request_id
    if (!id || !projectId || !pending) return
    const current = ++generation.current
    setBusy(true); setError(null)
    try {
      await decideApproval(id, true, projectId)
      if (current !== generation.current) return
      const result = await continueStudioTool(id, projectId)
      if (current !== generation.current) return
      if (result.status !== 'success') throw new Error(result.detail)
      displayResult(result, pending.kind)
      setPending(null)
    } catch (reason) { if (current === generation.current) setError(reason instanceof Error ? reason.message : 'Approved read failed.') }
    finally { if (current === generation.current) setBusy(false) }
  }
  const askAI = async () => {
    if (!projectId || !file || file.exists === false || !aiRequest.trim()) return
    const current = ++generation.current
    setBusy(true); setError(null); setPending(null); setProposal(null); setOutcome(null)
    try {
      const result = await executeStudioTool('studio_ai_propose', { path: file.path, before_sha256: file.sha256, request: aiRequest.trim(), context_paths: contextPaths.split('\n').map((path) => path.trim()).filter(Boolean) }, projectId)
      if (current !== generation.current) return
      if (result.status === 'approval_required') { setPending({ result, kind: 'propose', target: file.path }); return }
      if (result.status !== 'success') throw new Error(result.detail)
      displayResult(result, 'propose')
    } catch (reason) { if (current === generation.current) setError(reason instanceof Error ? reason.message : 'Model proposal failed.') }
    finally { if (current === generation.current) setBusy(false) }
  }
  if (!projectId) return <section className="surface"><h2>Select a project</h2><p>Register a directory in Settings, then select it in the workspace selector.</p></section>
  return <div className="page-stack">
    <CommandPanel key={projectId} projectId={projectId} authenticated={authenticated} />
    <RecoveryPanel key={'recovery-' + projectId} projectId={projectId} authenticated={authenticated} editorBusy={busy} getEditorRevision={() => generation.current} onReview={(result, editorRevision) => {
      if (editorRevision !== generation.current) return
      generation.current++
      const data = result.result!
      const base = { path: String(data.path), content: String(data.before_content), sha256: String(data.before_sha256), exists: data.before_exists !== false }
      const applied = data.status === 'applied'
      checkpointBase.current = base
      setFile(applied ? { ...base, content: String(data.content), sha256: String(data.after_sha256), exists: true } : base)
      setDraft(String(data.content)); setAIRequest(''); setProposal({ change_id: String(data.change_id), path: String(data.path), proposal_sha256: String(data.proposal_sha256), diff: String(data.diff), before_exists: data.before_exists !== false, applied }); setPending(null); setError(null); setOutcome(null); setBusy(false)
    }} />
    <section className="surface"><h2>Project explorer</h2><p>Ignored files, linked paths and potential credentials are excluded. UTF-8 files support exact approved patches and selective rollback. New-file changes require an absent path in an existing directory. Rollback of a created file needs separate destructive approval. Original versions stay in managed checkpoints; they are not automatically purged.</p>
      <label>New file path<input aria-label="New file path" value={newPath} disabled={busy} onChange={(event) => setNewPath(event.target.value)} placeholder="Relative project path" /></label>
      <button className="button subtle" disabled={busy || !authenticated || !newPath.trim()} onClick={() => { generation.current++; setFile({ path: newPath.trim(), content: '', sha256: '', exists: false }); setDraft(''); setProposal(null); setPending(null); setOutcome(null); setError(null) }}>Draft new file</button>
      <p>Directory: {directory || '/'}</p>
      {directory && <button className="button subtle" disabled={busy} onClick={() => setDirectory(directory.split('/').slice(0, -1).join('/'))}>Parent directory</button>}
      {!authenticated && <p>Pair the dashboard to inspect a project.</p>}
      {busy && <p role="status">Reading project…</p>}
      {error && <p role="alert">{error}</p>}
      {pending && <div><p>{pending.kind === 'apply' ? 'Apply exact reviewed patch' : pending.kind === 'rollback' ? 'Restore this checkpoint only' : 'Read approval required'} for {pending.target}. Project: {projectId}. Policy revision: {String(pending.result.approval?.preview.policy_revision)}.</p><button className="button" disabled={busy} onClick={() => void approveRead()}>{pending.kind === 'apply' ? 'Approve exact patch' : pending.kind === 'rollback' ? 'Approve checkpoint rollback' : 'Approve this read'}</button></div>}
      {truncated && <p>Directory scan reached its limit. Some entries are omitted.</p>}
      <ul>{entries.map((entry) => <li key={entry.path}><button className="button subtle" disabled={busy} onClick={() => void open(entry)}>{entry.directory ? 'Directory: ' : 'File: '}{entry.name}</button></li>)}</ul>
    </section>
    {file && <section className="surface"><h2>{file.path}</h2><label>Proposed file content<textarea aria-label="Proposed file content" value={draft} disabled={busy || Boolean(proposal?.applied)} onChange={(event) => { generation.current++; setDraft(event.target.value); setProposal(null); setPending(null); setOutcome(null) }} maxLength={64000} rows={14} style={{ width: '100%', fontFamily: 'monospace' }} /></label>
      <p>AI proposals use this file (up to 8 KB), bounded inherited AGENTS.md instructions, your explicit additional files and verified historical Git status. For Git projects, run the separately approved Git status preset first. Cloud transmission requires configured consent and budget. Mock mode cannot generate a patch.</p>
      <label>Additional context paths<textarea aria-label="Additional context paths" value={contextPaths} disabled={busy || Boolean(proposal?.applied)} maxLength={2000} rows={2} placeholder="Up to four relative text paths, one per line" onChange={(event) => { generation.current++; setContextPaths(event.target.value); setPending(null); setProposal(null) }} /></label>
      <label>AI coding request<textarea aria-label="AI coding request" value={aiRequest} disabled={busy || Boolean(proposal?.applied)} maxLength={2000} rows={2} onChange={(event) => { generation.current++; setAIRequest(event.target.value) }} /></label>
      <button className="button subtle" disabled={busy || !authenticated || file.exists === false || Boolean(proposal?.applied) || !aiRequest.trim()} onClick={() => void askAI()}>Ask AI for reviewed patch</button>
      <button className="button" disabled={busy || draft === file.content || Boolean(proposal?.applied)} onClick={() => void patchAction('propose')}>Review proposed patch</button>
      {proposal?.applied && <button className="button subtle" disabled={busy || Boolean(pending)} onClick={() => { generation.current++; setProposal(null); setOutcome(null) }}>Continue editing applied file</button>}
      {proposal && <div><h3>Exact diff</h3><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}><code>{proposal.diff}</code></pre><p>Proposal SHA-256: {proposal.proposal_sha256}</p><button className="button" disabled={busy || Boolean(pending)} onClick={() => void patchAction(proposal.applied ? 'rollback' : 'apply')}>{proposal.applied ? 'Request checkpoint rollback' : 'Request apply approval'}</button></div>}
    </section>}
    {outcome && <p role="status">{outcome}</p>}
  </div>
}
