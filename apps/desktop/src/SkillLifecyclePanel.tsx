import { useEffect, useRef, useState } from 'react'
import { continueStudioTool, decideApproval, getSessionEpoch, workspaceRequest, type JsonRecord, type StudioResult } from './api'

export default function SkillLifecyclePanel({ projectId, skillId, onChanged }: { projectId: string; skillId: string; onChanged: () => void }) {
  const [manifest, setManifest] = useState<JsonRecord | null>(null)
  const [pending, setPending] = useState<{ tool: string; result: StudioResult } | null>(null)
  const [consent, setConsent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0), lock = useRef(false)
  useEffect(() => () => { generation.current += 1 }, [])

  async function run(tool: string, operation: () => Promise<StudioResult>) {
    if (lock.current) return
    lock.current = true
    const version = ++generation.current, epoch = getSessionEpoch()
    setBusy(true); setError('')
    try {
      const result = await operation()
      if (version !== generation.current || epoch !== getSessionEpoch()) return
      setPending(null)
      if (result.status === 'approval_required' && result.approval) setPending({ tool, result })
      else if (result.status === 'success' && result.result) {
        if (tool === 'skill_inspect') { setManifest(result.result); setConsent(false) }
        if (tool === 'skill_uninstall') { setManifest(null); setConsent(false); onChanged() }
      } else if (result.status !== 'rejected') setError(result.detail || 'Skill action did not complete.')
    } catch (reason) {
      if (version === generation.current && epoch === getSessionEpoch()) setError(reason instanceof Error ? reason.message : 'Skill action failed.')
    } finally {
      lock.current = false
      if (version === generation.current && epoch === getSessionEpoch()) setBusy(false)
    }
  }
  function execute(tool: string, arguments_: JsonRecord) {
    void run(tool, () => workspaceRequest<StudioResult>('/knowledge/execute', 'POST', { tool, arguments: arguments_ }, projectId))
  }
  function decide(allowed: boolean) {
    if (!pending?.result.approval) return
    const identifier = pending.result.approval.request_id, tool = pending.tool, epoch = getSessionEpoch()
    void run(tool, async () => {
      await decideApproval(identifier, allowed, projectId)
      if (epoch !== getSessionEpoch()) throw new Error('Session changed; pair again.')
      return allowed ? continueStudioTool(identifier, projectId) : { status: 'rejected', detail: '', result: null }
    })
  }
  const disabled = busy || pending !== null
  return <section aria-label={`Installed skill ${skillId}`}>
    {error && <p role="alert">{error}</p>}
    <button type="button" className="button subtle" disabled={disabled} onClick={() => execute('skill_inspect', { skill_id: skillId })}>Inspect installed manifest</button>
    {manifest && <>
      <p>Version {String(manifest.version)} · hash <code>{String(manifest.manifest_sha256)}</code></p>
      <details><summary>Installed guidance and tools</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(manifest.manifest, null, 2)}</pre></details>
      <p>Uninstall removes this project's installed guidance. User files, task history, tools and external account permissions remain.</p>
      <label><input type="checkbox" checked={consent} disabled={disabled} onChange={(event) => setConsent(event.target.checked)} />I reviewed this version and want to uninstall it.</label>
      <button type="button" className="button subtle" disabled={disabled || !consent} onClick={() => execute('skill_uninstall', {
        skill_id: skillId, expected_version: manifest.version, manifest_sha256: manifest.manifest_sha256, confirm_uninstall: true,
      })}>Request exact uninstall approval</button>
    </>}
    {pending && <section aria-label="Skill action approval">
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(pending.result.approval?.preview, null, 2)}</pre>
      <button type="button" className="button" disabled={busy} onClick={() => decide(true)}>Approve skill action</button>
      <button type="button" className="button subtle" disabled={busy} onClick={() => decide(false)}>Reject skill action</button>
    </section>}
  </section>
}
