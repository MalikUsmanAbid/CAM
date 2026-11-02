import { useEffect, useRef, useState } from 'react'
import { getSessionEpoch, workspaceRequest, type JsonRecord } from './api'

type Copy = { materialization_id: string; contract_sha256: string; status?: string }
type Run = { run_id: string; contract_sha256: string }

export default function DraftCommandPanel({ projectId, workspaceId, revision }: { projectId: string; workspaceId: string; revision: number }) {
  const [copy, setCopy] = useState<Copy | null>(null)
  const [identifier, setIdentifier] = useState('')
  const [checksum, setChecksum] = useState('')
  const [run, setRun] = useState<Run | null>(null)
  const [executable, setExecutable] = useState('')
  const [argumentsText, setArgumentsText] = useState('[]')
  const [result, setResult] = useState<unknown>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const generation = useRef(0)
  useEffect(() => () => { generation.current += 1 }, [])

  async function act(tool: string, arguments_: JsonRecord) {
    const current = ++generation.current
    const epoch = getSessionEpoch()
    setBusy(true); setError('')
    try {
      const response = await workspaceRequest<{ status: string; detail?: string; result?: JsonRecord }>('/knowledge/execute', 'POST', { tool, arguments: arguments_ }, projectId)
      if (current !== generation.current || epoch !== getSessionEpoch()) return
      setResult(response)
      if (response.status === 'success' && response.result) {
        const output = response.result
        if (tool === 'draft_copy_prepare' || tool === 'draft_copy_inspect' || tool === 'draft_copy_create') {
          const next = output as Copy
          setCopy(next); setIdentifier(next.materialization_id); setChecksum(next.contract_sha256); setRun(null)
        }
        if (tool === 'draft_copy_command') setRun(output as Run)
      } else if (!['success', 'approval_required'].includes(response.status)) setError(response.detail ?? 'Managed workspace operation failed.')
    } catch (reason) {
      if (current === generation.current && epoch === getSessionEpoch()) setError(reason instanceof Error ? reason.message : 'Managed workspace operation failed.')
    } finally {
      if (current === generation.current && epoch === getSessionEpoch()) setBusy(false)
    }
  }
  function prepareCommand() {
    try {
      const arguments_: unknown = JSON.parse(argumentsText)
      if (!Array.isArray(arguments_) || !arguments_.every((item) => typeof item === 'string')) throw new Error('Arguments must be a JSON array of strings.')
      if (copy) void act('draft_copy_command', { materialization_id: copy.materialization_id, contract_sha256: copy.contract_sha256, executable, arguments: arguments_, cwd: '.', timeout_seconds: 60, output_limit: 32000 })
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Invalid command arguments.') }
  }
  const binding = copy ? { materialization_id: copy.materialization_id, contract_sha256: copy.contract_sha256 } : null
  return <section aria-label="Managed draft commands">
    <h4>Managed file workspace and commands</h4>
    <p className="helper-text">Copy only selected snapshot files into a new project subdirectory after approval. This is not a sandbox: approved code can access user files and the network. No dependencies or Git metadata are copied.</p>
    {error && <p role="alert">{error}</p>}
    <button type="button" className="button subtle" disabled={busy} onClick={() => void act('draft_copy_prepare', { workspace_id: workspaceId, expected_revision: revision })}>Prepare managed file copy</button>
    <button type="button" className="button subtle" disabled={busy} onClick={() => void act('draft_copy_list', {})}>List managed copies</button>
    <form onSubmit={(event) => { event.preventDefault(); void act('draft_copy_inspect', { materialization_id: identifier, contract_sha256: checksum }) }}>
      <label>Managed copy ID<input required pattern="[a-f0-9]{32}" value={identifier} onChange={(event) => { setIdentifier(event.target.value); setCopy(null); setRun(null) }} /></label>
      <label>Managed copy contract hash<input required pattern="[a-f0-9]{64}" value={checksum} onChange={(event) => { setChecksum(event.target.value); setCopy(null); setRun(null) }} /></label>
      <button className="button subtle" disabled={busy}>Inspect copy and command history</button>
    </form>
    {copy && binding && <>
      <p>Copy status: {copy.status}</p>
      <button type="button" className="button primary" disabled={busy || copy.status !== 'prepared'} onClick={() => void act('draft_copy_create', binding)}>Request file-copy approval</button>
      <p className="helper-text">After an approval completes, inspect again. Changed draft revisions require a new copy. Interrupted directories stay available for manual recovery.</p>
      <label>Command executable (absolute .exe path)<input value={executable} maxLength={500} onChange={(event) => { setExecutable(event.target.value); setRun(null) }} /></label>
      <label>Command arguments (JSON string array)<textarea value={argumentsText} maxLength={12000} onChange={(event) => { setArgumentsText(event.target.value); setRun(null) }} /></label>
      <button type="button" className="button subtle" disabled={busy || copy.status !== 'ready' || !executable.trim()} onClick={prepareCommand}>Prepare exact managed command</button>
      {run && <button type="button" className="button primary" disabled={busy} onClick={() => void act('draft_copy_run', { ...binding, run_id: run.run_id, run_contract_sha256: run.contract_sha256 })}>Request command approval</button>}
      <button type="button" className="button subtle" disabled={busy || copy.status !== 'ready'} onClick={() => void act('draft_copy_collect', binding)}>Collect selected output as merge proposals</button>
      <p className="helper-text">Collection prepares diffs only. Each source apply needs separate approval. Current output may differ from the bytes used by an earlier command.</p>
    </>}
    {result !== null && <details><summary>Managed copy evidence</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(result, null, 2)}</pre></details>}
  </section>
}
