import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { getSessionEpoch, workspaceRequest, type JsonRecord } from './api'
import AgentDraftPanel from './AgentDraftPanel'
import SocialContentPanel from './SocialContentPanel'
import LibraryBrowser from './LibraryBrowser'
import RemindersPanel from './RemindersPanel'
import WorkRecordsPanel from './WorkRecordsPanel'
import DiagnosticsDownload from './DiagnosticsDownload'

export type WorkspaceSection = 'agents' | 'library' | 'decisions' | 'work' | 'safety'
type Props = { section: WorkspaceSection; projectId: string | null; authenticated: boolean }
const text = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value ?? null, null, 2)

export default function WorkspacePanel({ section, projectId, authenticated }: Props) {
  const [items, setItems] = useState<JsonRecord[]>([])
  const [secondary, setSecondary] = useState<JsonRecord[]>([])
  const [output, setOutput] = useState<unknown>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [role, setRole] = useState('coordinator')
  const [path, setPath] = useState('')
  const [hash, setHash] = useState('')
  const [dependencies, setDependencies] = useState('')
  const [due, setDue] = useState('')
  const [interval, setInterval] = useState('')
  const [query, setQuery] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [importFormat, setImportFormat] = useState('text')
  const [artifactId, setArtifactId] = useState('')
  const [previousArtifact, setPreviousArtifact] = useState('')
  const [exportFormat, setExportFormat] = useState('txt')
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const mounted = useRef(true)
  const actionGeneration = useRef(0)
  const loadGeneration = useRef(0)
  const current = useRef({ section, projectId })
  useLayoutEffect(() => {
    current.current = { section, projectId }
  }, [section, projectId])

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const requiresProject = section === 'agents' || section === 'library'
  async function load(signal?: AbortSignal) {
    const generation = ++loadGeneration.current
    const epoch = getSessionEpoch()
    const initial = { section, projectId }
    let primary: JsonRecord[] = []
    let extra: JsonRecord[] = []
    if (section === 'agents') {
      primary = await workspaceRequest<JsonRecord[]>('/agents', 'GET', undefined, projectId, signal)
      extra = await workspaceRequest<JsonRecord[]>('/agent-batches', 'GET', undefined, projectId, signal)
    }
    if (section === 'decisions') {
      primary = await workspaceRequest<JsonRecord[]>('/decisions', 'GET', undefined, projectId, signal)
      const response = await workspaceRequest<{ items: JsonRecord[] }>('/notifications', 'GET', undefined, projectId, signal)
      extra = response.items
    }
    if (section === 'safety') primary = [await workspaceRequest<JsonRecord>('/control/status', 'GET', undefined, null, signal)]
    if (!signal?.aborted && mounted.current && generation === loadGeneration.current && epoch === getSessionEpoch()
      && current.current.projectId === initial.projectId && current.current.section === initial.section) {
      setItems(primary); setSecondary(extra)
    }
  }

  useEffect(() => {
    const controller = new AbortController()
    if (authenticated && (!requiresProject || projectId)) {
      void load(controller.signal).catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Workspace is unavailable.')
      })
    }
    return () => { controller.abort() }
    // The parent keys this panel by both section and project identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authenticated, projectId, section])

  async function act(operation: () => Promise<unknown>) {
    const generation = ++actionGeneration.current
    const epoch = getSessionEpoch()
    setBusy(true); setError(null)
    try {
      const result = await operation()
      if (!mounted.current || generation !== actionGeneration.current || epoch !== getSessionEpoch()) return
      setOutput(result)
      await load()
    } catch (reason) {
      if (mounted.current && generation === actionGeneration.current && epoch === getSessionEpoch()) setError(reason instanceof Error ? reason.message : 'Workspace operation failed.')
    } finally {
      if (mounted.current && generation === actionGeneration.current && epoch === getSessionEpoch()) setBusy(false)
    }
  }

  const knowledge = (tool: string, arguments_: JsonRecord) => workspaceRequest('/knowledge/execute', 'POST', { tool, arguments: arguments_ }, projectId)
  if (!authenticated) return <section className="surface"><p>Pair with the backend first.</p></section>
  if (requiresProject && !projectId) return <section className="surface"><p>Select a registered project to open this workspace.</p></section>

  return <div className="page-stack">
    {error && <p role="alert" className="notice-card">{error}</p>}
    <section className="surface">
      <h2>{{ agents: 'Multi-Agent Workspace', library: 'Knowledge & Creation Library', decisions: 'Questions, Decisions & Reminders', work: 'Business & Content Records', safety: 'Execution Controls' }[section]}</h2>
      <p className="helper-text">{projectId ? 'Selected project scope' : 'Personal scope'} · Implementation awaiting combined verification.</p>
      {section !== 'library' && <button className="button subtle" disabled={busy} onClick={() => void act(() => load())}>Refresh</button>}

      {section === 'agents' && <form onSubmit={(event) => {
        event.preventDefault()
        const arguments_ = role === 'coder' ? { path, before_sha256: hash, request: content, context_paths: [] } : { message: content }
        void act(() => workspaceRequest('/agents', 'POST', { role, arguments: arguments_, dependencies: dependencies.split(',').map((value) => value.trim()).filter(Boolean) }, projectId))
      }}>
        <label>Role<select value={role} onChange={(event) => setRole(event.target.value)}>{['coordinator', 'coder', 'researcher', 'reviewer'].map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>Request<textarea value={content} maxLength={2000} onChange={(event) => setContent(event.target.value)} required /></label>
        {role === 'coder' && <><label>Inspected file path<input value={path} onChange={(event) => setPath(event.target.value)} required /></label><label>Inspected file SHA-256<input value={hash} pattern="[a-f0-9]{64}" onChange={(event) => setHash(event.target.value)} required /></label></>}
        <label>Dependency job IDs, comma separated<input value={dependencies} onChange={(event) => setDependencies(event.target.value)} maxLength={300} /></label>
        <button className="button primary" disabled={busy || !content.trim()}>Queue role job</button>
        {role === 'coder' && <button type="button" className="button subtle" disabled={busy || !content.trim() || !path || !/^[a-f0-9]{64}$/.test(hash)} onClick={() => void act(() => workspaceRequest('/agent-batches', 'POST', { path, before_sha256: hash, request: content, context_paths: [] }, projectId))}>Create coordinated proposal workflow</button>}
        <p className="helper-text">Run jobs explicitly. Coding output is a proposed patch; applying it and running commands require separate approval. Research roles do not imply live web search.</p>
      </form>}

      {section === 'library' && <>
        <form onSubmit={(event) => { event.preventDefault(); void act(() => knowledge('knowledge_search', { query })) }}>
          <label>Search imported text<input value={query} maxLength={200} onChange={(event) => setQuery(event.target.value)} required /></label>
          <button className="button primary" disabled={busy}>Search</button>
          <button type="button" className="button subtle" disabled={busy} onClick={() => void act(() => knowledge('knowledge_documents', {}))}>List sources</button>
          <button type="button" className="button subtle" disabled={busy} onClick={() => void act(() => knowledge('library_artifacts', {}))}>List artifacts</button>
        </form>
        <form onSubmit={(event) => { event.preventDefault(); void act(() => knowledge(importFormat === 'docx' ? 'knowledge_import_docx' : 'knowledge_import', { path, title, source_sha256: hash })) }}>
          <h3>Import inspected document</h3>
          <label>Source format<select value={importFormat} onChange={(event) => setImportFormat(event.target.value)}><option value="text">Plain text</option><option value="docx">DOCX text extraction</option></select></label>
          <label>Title<input value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} required /></label>
          <label>Project-relative source<input value={path} maxLength={500} onChange={(event) => setPath(event.target.value)} required /></label>
          <label>Source SHA-256<input value={hash} pattern="[a-f0-9]{64}" onChange={(event) => setHash(event.target.value)} required /></label>
          <button className="button primary" disabled={busy}>Request import approval</button>
        </form>
        <form onSubmit={(event) => { event.preventDefault(); void act(() => knowledge('research_fetch', { url: sourceUrl, title })) }}>
          <h3>Retrieve source snapshot</h3>
          <label>Source title<input value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} required /></label>
          <label>Exact public HTTPS URL<input type="url" value={sourceUrl} maxLength={2000} onChange={(event) => setSourceUrl(event.target.value)} required /></label>
          <button className="button primary" disabled={busy}>Request retrieval approval</button>
          <p className="helper-text">One public HTTPS source per approval. Queries, redirects and nested resources are excluded. Retrieved text is untrusted source material.</p>
        </form>
        <form onSubmit={(event) => { event.preventDefault(); void act(() => knowledge('library_create', { title, kind: 'document', content, sources: dependencies.split(',').map((value) => value.trim()).filter(Boolean), previous_id: previousArtifact || null })) }}>
          <h3>Create text artifact</h3>
          <label>Artifact title<input value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} required /></label>
          <label>Content<textarea value={content} maxLength={32000} onChange={(event) => setContent(event.target.value)} required /></label>
          <label>Source document IDs, comma separated<input value={dependencies} maxLength={700} onChange={(event) => setDependencies(event.target.value)} /></label>
          <label>Previous version ID, optional<input value={previousArtifact} pattern="[a-f0-9]{32}" onChange={(event) => setPreviousArtifact(event.target.value)} /></label>
          <button className="button primary" disabled={busy}>Request artifact approval</button>
        </form>
        <form onSubmit={(event) => { event.preventDefault(); void act(() => knowledge('library_export', { artifact_id: artifactId, source_sha256: hash, format: exportFormat })) }}>
          <h3>Read or export artifact</h3>
          <label>Artifact ID<input value={artifactId} pattern="[a-f0-9]{32}" onChange={(event) => setArtifactId(event.target.value)} required /></label>
          <button type="button" className="button subtle" disabled={busy || !/^[a-f0-9]{32}$/.test(artifactId)} onClick={() => void act(() => knowledge('library_artifact', { artifact_id: artifactId }))}>Inspect artifact and hash</button>
          <label>Inspected content SHA-256<input value={hash} pattern="[a-f0-9]{64}" onChange={(event) => setHash(event.target.value)} required /></label>
          <label>Export format<select value={exportFormat} onChange={(event) => setExportFormat(event.target.value)}>{['txt', 'md', 'docx', 'json'].map((value) => <option key={value}>{value}</option>)}</select></label>
          <button className="button primary" disabled={busy}>Prepare export</button>
        </form>
        <p className="helper-text">Approvals appear in the existing Approvals screen. Imported content is a dated snapshot; citations do not prove factual correctness.</p>
      </>}


      {section === 'decisions' && <>
        <button className="button subtle" disabled={busy} onClick={() => {
          const start = new Date(); start.setHours(0, 0, 0, 0)
          const end = new Date(start); end.setDate(end.getDate() + 1)
          void act(() => workspaceRequest('/briefing', 'POST', { start: start.toISOString(), end: end.toISOString() }, projectId))
        }}>Show today’s briefing</button>
        <form onSubmit={(event) => { event.preventDefault(); void act(() => workspaceRequest('/decisions', 'POST', { question: content }, projectId)) }}>
          <label>Question<textarea value={content} maxLength={2000} onChange={(event) => setContent(event.target.value)} required /></label>
          <button className="button primary" disabled={busy}>Add question</button>
        </form>
        <form onSubmit={(event) => {
          event.preventDefault()
          const timestamp = new Date(due)
          if (Number.isNaN(timestamp.getTime())) { setError('Choose a valid reminder time.'); return }
          void act(() => workspaceRequest('/reminders', 'POST', { title, kind: 'reminder', due_at: timestamp.toISOString(), interval_seconds: interval ? Number(interval) : null }, projectId))
        }}>
          <label>Reminder<input value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} required /></label>
          <label>Local date and time<input type="datetime-local" value={due} onChange={(event) => setDue(event.target.value)} required /></label>
          <label>Repeat interval in seconds, optional<input type="number" min={60} max={31536000} value={interval} onChange={(event) => setInterval(event.target.value)} /></label>
          <button className="button primary" disabled={busy}>Schedule reminder</button>
        </form>
      </>}

      {section === 'safety' && <>
        <p>Stop blocks new tool/model dispatch and requests cooperative cancellation. Already accepted external effects may remain.</p>
        <button className="button primary" disabled={busy} onClick={() => void act(() => workspaceRequest('/control/stop', 'POST'))}>Emergency stop</button>
        <button className="button subtle" disabled={busy} onClick={() => void act(() => workspaceRequest('/control/reset', 'POST'))}>Reset stop without restarting jobs</button>
        <DiagnosticsDownload />
      </>}
    </section>

    {section === 'agents' && projectId && <AgentDraftPanel key={projectId} projectId={projectId} />}
    {section === 'work' && <SocialContentPanel key={projectId ?? 'personal'} projectId={projectId} />}
    {items.map((item) => <section className="surface" key={text(item.job_id ?? item.record_id ?? item.decision_id ?? 'control')}>
      <strong>{text(item.title ?? item.question ?? item.role ?? 'Status')}</strong>
      <p>{text(item.status ?? '')}</p>
      {section === 'agents' && <>
        <code>{text(item.job_id)}</code>
        {['queued', 'interrupted'].includes(text(item.status)) && <button className="button primary" disabled={busy} onClick={() => void act(() => workspaceRequest(`/agents/${text(item.job_id)}/run`, 'POST', undefined, projectId))}>Run job</button>}
        {item.status === 'waiting_for_approval' && <button className="button primary" disabled={busy} onClick={() => void act(() => workspaceRequest(`/agents/${text(item.job_id)}/continue`, 'POST', undefined, projectId))}>Continue after approval</button>}
        {['queued', 'interrupted', 'running', 'waiting_for_approval'].includes(text(item.status)) && <button className="button subtle" onClick={() => void act(() => workspaceRequest(`/agents/${text(item.job_id)}/cancel`, 'POST', undefined, projectId))}>Cancel job</button>}
        <details><summary>Execution evidence</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(item.result ?? item.input, null, 2)}</pre></details>
      </>}
      {section === 'decisions' && item.status === 'pending' && <form onSubmit={(event) => { event.preventDefault(); void act(() => workspaceRequest(`/decisions/${text(item.decision_id)}/answer`, 'POST', { answer: answers[text(item.decision_id)] ?? '' }, projectId)) }}><label>Answer<input value={answers[text(item.decision_id)] ?? ''} maxLength={4000} onChange={(event) => setAnswers({ ...answers, [text(item.decision_id)]: event.target.value })} required /></label><button className="button primary" disabled={busy}>Record answer</button><button type="button" className="button subtle" disabled={busy || !item.question_sha256} onClick={() => void act(() => workspaceRequest(`/decisions/${text(item.decision_id)}/dismiss`, 'POST', { expected_question_sha256: item.question_sha256 }, projectId))}>Dismiss question without running task</button></form>}
      {section === 'safety' && <pre>{JSON.stringify(item, null, 2)}</pre>}
    </section>)}
    {section === 'agents' && secondary.map((item) => <section className="surface" key={text(item.batch_id)}>
      <h3>Coordinated proposal workflow</h3><code>{text(item.batch_id)}</code><p>{text(item.status)}</p>
      {['queued', 'paused', 'interrupted'].includes(text(item.status)) && <button className="button primary" disabled={busy} onClick={() => void act(() => workspaceRequest(`/agent-batches/${text(item.batch_id)}/run`, 'POST', undefined, projectId))}>Run or resume role graph</button>}
      {['queued', 'paused', 'interrupted', 'running'].includes(text(item.status)) && <button className="button subtle" onClick={() => void act(() => workspaceRequest(`/agent-batches/${text(item.batch_id)}/cancel`, 'POST', undefined, projectId))}>Cancel workflow</button>}
      <p className="helper-text">Coordinator and researcher feed the coder; the reviewer follows its proposal. Approvals pause the graph. Completion does not apply files or establish passing tests.</p>
    </section>)}
    {section === 'library' && projectId && <LibraryBrowser key={projectId} projectId={projectId} />}
    {section === 'decisions' && <RemindersPanel key={projectId ?? 'personal'} projectId={projectId} />}
    {section === 'work' && <WorkRecordsPanel key={projectId ?? 'personal'} projectId={projectId} />}
    {section === 'decisions' && secondary.map((item) => <section className="surface" key={text(item.occurrence_id)}><strong>{text(item.title)}</strong><p>{text(item.status)} · {new Date(text(item.due_at)).toLocaleString()}</p>{['pending', 'waiting_for_review'].includes(text(item.status)) && <button className="button subtle" disabled={busy} onClick={() => void act(() => workspaceRequest(`/notifications/${text(item.occurrence_id)}/acknowledge`, 'POST', undefined, projectId))}>Acknowledge</button>}</section>)}
    {output !== null && <section className="surface"><h3>Result</h3><ArtifactDownload output={output} /><pre role="status" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(output, null, 2)}</pre></section>}
  </div>
}

export function ArtifactDownload({ output }: { output: unknown }) {
  const [error, setError] = useState('')
  if (!output || typeof output !== 'object' || !('result' in output)
    || !('status' in output) || output.status !== 'success'
    || !('tool_name' in output) || output.tool_name !== 'library_export') return null
  const result = output.result as JsonRecord | null
  if (!result || typeof result.filename !== 'string' || !/^artifact-[a-f0-9]{32}\.(txt|md|docx|json)$/.test(result.filename)
    || typeof result.content_base64 !== 'string' || result.content_base64.length > 100000
    || typeof result.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(result.sha256)) return null
  async function download() {
    try {
      const epoch = getSessionEpoch()
      const data = Uint8Array.from(atob(result!.content_base64 as string), (value) => value.charCodeAt(0))
      const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', data))
      if (Array.from(digest, (value) => value.toString(16).padStart(2, '0')).join('') !== result!.sha256) throw new Error('Export checksum mismatch.')
      if (epoch !== getSessionEpoch()) return
      const url = URL.createObjectURL(new Blob([data], { type: 'application/octet-stream' }))
      const link = document.createElement('a'); link.href = url; link.download = result!.filename as string
      link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch { setError('Export could not be downloaded or verified.') }
  }
  return <><button className="button primary" onClick={() => void download()}>Download verified export</button>{error && <p role="alert">{error}</p>}</>
}
