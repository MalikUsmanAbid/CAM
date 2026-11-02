import { useEffect, useRef, useState } from 'react'
import { continueNewProject, decideApproval, getProjects, prepareNewProject } from './api'
import type { RegisteredProject, StudioResult } from './api'

export default function NewProjectPanel({ authenticated, onCreated }: { authenticated: boolean; onCreated: (items: RegisteredProject[]) => void }) {
  const [name, setName] = useState('')
  const [path, setPath] = useState('')
  const [pending, setPending] = useState<StudioResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const generation = useRef(0)
  useEffect(() => {
    const reference = generation
    reference.current++
    queueMicrotask(() => { setPending(null); setBusy(false); setMessage(null) })
    return () => { reference.current++ }
  }, [authenticated])
  const review = async () => {
    if (!authenticated || !name.trim() || !path.trim()) return
    const current = ++generation.current
    setBusy(true); setMessage(null); setPending(null)
    try {
      const result = await prepareNewProject(name.trim(), path.trim())
      if (current !== generation.current) return
      if (result.status !== 'approval_required') throw new Error(result.detail)
      setPending(result)
    } catch (reason) { if (current === generation.current) setMessage(reason instanceof Error ? reason.message : 'Project preparation failed.') }
    finally { if (current === generation.current) setBusy(false) }
  }
  const decide = async (allow: boolean) => {
    if (!authenticated || !pending?.approval) return
    const identifier = pending.approval.request_id
    const current = ++generation.current
    setBusy(true); setMessage(null)
    try {
      await decideApproval(identifier, allow)
      if (current !== generation.current) return
      if (!allow) { setPending(null); setMessage('Project creation rejected.'); return }
      const result = await continueNewProject(identifier)
      if (current !== generation.current) return
      if (result.status !== 'success') throw new Error(result.detail)
      setPending(null); setName(''); setPath(''); setMessage('Empty project created. Select it in the workspace selector.')
      const items = await getProjects()
      if (current === generation.current) onCreated(items)
    } catch (reason) { if (current === generation.current) { setPending(null); setMessage(reason instanceof Error ? reason.message : 'Project creation was not verified. Inspect the destination before trying again.') } }
    finally { if (current === generation.current) setBusy(false) }
  }
  const scope = pending?.approval?.preview.execution_scope as { destination?: string } | undefined
  return <div><h3>New empty project</h3><p>Create an absent folder inside CAM's personal workspace. Its parent must already exist. Creation does not install dependencies or generate code.</p>
    <label>New project name<input aria-label="New project name" value={name} maxLength={100} disabled={busy} onChange={(event) => { generation.current++; setName(event.target.value); setPending(null) }} /></label>
    <label>New project folder<input aria-label="New project folder" value={path} maxLength={500} disabled={busy} placeholder="Relative workspace folder, for example my-app" onChange={(event) => { generation.current++; setPath(event.target.value); setPending(null) }} /></label>
    <button className="button subtle" disabled={busy || !authenticated || !name.trim() || !path.trim()} onClick={() => void review()}>Review new project</button>
    {pending && <div><p>Create {name} at {scope?.destination}. Policy revision: {String(pending.approval?.preview.policy_revision)}.</p><button className="button" disabled={busy} onClick={() => void decide(true)}>Approve empty project creation</button><button className="button subtle" disabled={busy} onClick={() => void decide(false)}>Reject project creation</button></div>}
    {message && <p role="status">{message}</p>}
  </div>
}
