import { useEffect, useRef, useState } from 'react'
import { continueStudioTool, decideApproval, getSessionEpoch, workspaceRequest, type JsonRecord, type StudioResult } from './api'

type Pending = { tool: string; response: StudioResult; append: boolean }
const label = (value: unknown) => typeof value === 'string' ? value : String(value ?? '')

export default function LibraryBrowser({ projectId }: { projectId: string }) {
  const [documents, setDocuments] = useState<JsonRecord[]>([])
  const [artifacts, setArtifacts] = useState<JsonRecord[]>([])
  const [documentCursor, setDocumentCursor] = useState<string | null>(null)
  const [artifactCursor, setArtifactCursor] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [document, setDocument] = useState<JsonRecord | null>(null)
  const [artifact, setArtifact] = useState<JsonRecord | null>(null)
  const [history, setHistory] = useState<JsonRecord[]>([])
  const [next, setNext] = useState<string | null>(null)
  const [consent, setConsent] = useState(false)
  const [pending, setPending] = useState<Pending | null>(null)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const generation = useRef(0)
  const locked = useRef(false)
  useEffect(() => () => { generation.current += 1 }, [])

  function accept(tool: string, response: StudioResult, append = false) {
    setPending(null)
    if (response.status === 'rejected') { setNotice('Library action rejected.'); return }
    if (response.status === 'approval_required' && response.approval) {
      setPending({ tool, response, append }); return
    }
    if (response.status !== 'success' || !response.result) {
      setError(response.detail || 'Library action did not complete.'); return
    }
    const result = response.result
    if (tool === 'knowledge_documents') setDocuments(result.documents as JsonRecord[])
    if (tool === 'library_artifacts') setArtifacts(result.artifacts as JsonRecord[])
    if (tool === 'knowledge_document_page') {
      const page = result.documents as JsonRecord[]
      setDocuments((old) => append ? [...old, ...page.filter((item) => !old.some((prior) => prior.document_id === item.document_id))].slice(-200) : page)
      setDocumentCursor(typeof result.next_cursor === 'string' ? result.next_cursor : null)
    }
    if (tool === 'library_artifact_page') {
      const page = result.artifacts as JsonRecord[]
      setArtifacts((old) => append ? [...old, ...page.filter((item) => !old.some((prior) => prior.artifact_id === item.artifact_id))].slice(-200) : page)
      setArtifactCursor(typeof result.next_cursor === 'string' ? result.next_cursor : null)
    }
    if (tool === 'knowledge_document') { setDocument(result); setConsent(false) }
    if (tool === 'library_artifact') { setArtifact(result); setHistory([]); setNext(null) }
    if (tool === 'library_history') {
      setHistory(result.versions as JsonRecord[])
      setNext(typeof result.next_artifact_id === 'string' ? result.next_artifact_id : null)
    }
    if (tool === 'knowledge_forget') {
      setDocuments((items) => items.filter((item) => item.document_id !== result.document_id))
      setDocument(null); setConsent(false)
      setNotice('Snapshot removed from active knowledge. Original files, artifact copies/attribution and backups remain.')
    }
  }

  async function perform(operation: () => Promise<StudioResult>, tool: string, append = false) {
    if (locked.current) return
    locked.current = true
    const current = ++generation.current, epoch = getSessionEpoch()
    setBusy(true); setError(''); setNotice('')
    try {
      const response = await operation()
      if (current === generation.current && epoch === getSessionEpoch()) accept(tool, response, append)
    } catch (reason) {
      if (current === generation.current && epoch === getSessionEpoch()) setError(reason instanceof Error ? reason.message : 'Library is unavailable.')
    } finally {
      locked.current = false
      if (current === generation.current && epoch === getSessionEpoch()) setBusy(false)
    }
  }
  function execute(tool: string, arguments_: JsonRecord) {
    void perform(() => workspaceRequest<StudioResult>('/knowledge/execute', 'POST', { tool, arguments: arguments_ }, projectId), tool, typeof arguments_.cursor === 'string')
  }
  function page(tool: string, cursor?: string) {
    execute(tool, { ...(query.trim() ? { query: query.trim() } : {}), ...(cursor ? { cursor } : {}) })
  }
  function decide(allow: boolean) {
    if (!pending?.response.approval) return
    const id = pending.response.approval.request_id, tool = pending.tool, epoch = getSessionEpoch()
    void perform(async () => {
      await decideApproval(id, allow, projectId)
      if (epoch !== getSessionEpoch()) throw new Error('Session changed; pair again.')
      if (!allow) return { tool_name: tool, status: 'rejected', detail: 'Library action rejected.', result: null }
      return continueStudioTool(id, projectId)
    }, tool, pending.append)
  }

  const disabled = busy || pending !== null
  return <section className="surface" aria-label="Saved project library">
    <h3>Saved sources and versions</h3>
    <p>Browse saved snapshots and immutable artifact versions in this project. Fifty entries per page; only the last 200 loaded entries in each list stay on screen.</p>
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <label>Filter saved library text<input value={query} maxLength={200} disabled={disabled} onChange={(event) => { setQuery(event.target.value); setDocumentCursor(null); setArtifactCursor(null) }} /></label>
    <p>Load a list to apply this literal title/content filter. Clear the filter and reload to browse everything.</p>
    <button type="button" className="button subtle" disabled={disabled} onClick={() => page('knowledge_document_page')}>Load saved sources</button>
    <button type="button" className="button subtle" disabled={disabled} onClick={() => page('library_artifact_page')}>Load artifact versions</button>
    {documents.length > 0 && <ul>{documents.map((item) => <li key={label(item.document_id)}>
      <button type="button" className="button subtle" disabled={disabled} onClick={() => execute('knowledge_document', { document_id: item.document_id })}>{label(item.title)}</button>
      <span> · {label(item.source_path)}</span>
    </li>)}</ul>}
    {documentCursor && <button type="button" className="button subtle" disabled={disabled} onClick={() => page('knowledge_document_page', documentCursor)}>Load more saved sources</button>}
    {document && <section aria-label="Selected source snapshot">
      <h4>{label(document.title)}</h4><p>{label(document.source_path)}</p>
      <p>Snapshot hash: <code>{label(document.source_sha)}</code></p>
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{label(document.content)}</pre>
      <p>Forgetting removes this imported snapshot from active retrieval. The original file, existing artifacts, attribution, audit records and backups remain. This is not secure disk erasure.</p>
      <label><input type="checkbox" checked={consent} disabled={disabled} onChange={(event) => setConsent(event.target.checked)} />I want to forget this exact imported snapshot.</label>
      <button type="button" className="button subtle" disabled={disabled || !consent} onClick={() => execute('knowledge_forget', { document_id: document.document_id, source_sha256: document.source_sha, confirm_forget: true })}>Request snapshot removal approval</button>
    </section>}
    {artifacts.length > 0 && <ul>{artifacts.map((item) => <li key={label(item.artifact_id)}>
      <button type="button" className="button subtle" disabled={disabled} onClick={() => execute('library_artifact', { artifact_id: item.artifact_id })}>{label(item.title)} · version {label(item.version)}</button>
    </li>)}</ul>}
    {artifact && <section aria-label="Selected artifact version">
      <h4>{label(artifact.title)} · version {label(artifact.version)}</h4>
      <p>Version ID: <code>{label(artifact.artifact_id)}</code></p>
      <p>Content hash: <code>{label(artifact.sha256)}</code></p>
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{label(artifact.content)}</pre>
      <p>{label(artifact.verification)}</p>
      <button type="button" className="button subtle" disabled={disabled} onClick={() => execute('library_history', { artifact_id: artifact.artifact_id })}>Inspect earlier versions</button>
    </section>}
    {artifactCursor && <button type="button" className="button subtle" disabled={disabled} onClick={() => page('library_artifact_page', artifactCursor)}>Load more artifact versions</button>}
    {history.length > 0 && <ol>{history.map((item) => <li key={label(item.artifact_id)}>
      <button type="button" className="button subtle" disabled={disabled} onClick={() => execute('library_artifact', { artifact_id: item.artifact_id })}>{label(item.title)} · version {label(item.version)}</button>
      <span> · {label(item.created_at)}</span>
    </li>)}</ol>}
    {next && <button type="button" className="button subtle" disabled={disabled} onClick={() => execute('library_history', { artifact_id: next })}>Load older version page</button>}
    {pending && <section aria-label="Library approval">
      <h4>Review this exact library action</h4>
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(pending.response.approval?.preview, null, 2)}</pre>
      <button type="button" className="button" disabled={busy} onClick={() => decide(true)}>Approve library action</button>
      <button type="button" className="button subtle" disabled={busy} onClick={() => decide(false)}>Reject library action</button>
    </section>}
  </section>
}
