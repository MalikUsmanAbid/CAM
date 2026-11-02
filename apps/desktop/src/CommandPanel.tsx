import { useEffect, useRef, useState } from 'react'
import { continueStudioTool, decideApproval, executeStudioTool } from './api'
import type { StudioResult } from './api'

type Prepared = { run_id: string; contract_sha256: string; command: Record<string, unknown> }

export default function CommandPanel({ projectId, authenticated }: { projectId: string; authenticated: boolean }) {
  const [executable, setExecutable] = useState('')
  const [argumentsText, setArgumentsText] = useState('[]')
  const [cwd, setCwd] = useState('.')
  const [prepared, setPrepared] = useState<Prepared | null>(null)
  const [pending, setPending] = useState<{ result: StudioResult; preparing: boolean } | null>(null)
  const [outcome, setOutcome] = useState<StudioResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const generation = useRef(0)
  useEffect(() => () => { generation.current++ }, [])

  const clearProposal = () => { generation.current++; setPrepared(null); setPending(null); setOutcome(null); setError(null); setBusy(false) }
  const display = (result: StudioResult, preparing: boolean) => {
    if (result.status === 'approval_required') { setPending({ result, preparing }); return }
    if (preparing) {
      if (result.status !== 'success') throw new Error(result.detail)
      setPrepared(result.result as unknown as Prepared)
    } else {
      setOutcome(result)
      setPrepared(null)
    }
  }
  const request = async (preparing: boolean, gitOperation?: 'status' | 'diff' | 'log') => {
    const current = ++generation.current
    setBusy(true); setError(null); setPending(null); setOutcome(null)
    try {
      const args = gitOperation ? [] : JSON.parse(argumentsText) as unknown
      if (!Array.isArray(args) || args.some((arg) => typeof arg !== 'string')) throw new Error('Arguments must be a JSON array of strings.')
      const result = gitOperation
        ? await executeStudioTool('studio_prepare_git', { operation: gitOperation }, projectId)
        : preparing
        ? await executeStudioTool('studio_prepare_command', { executable, arguments: args, cwd, timeout_seconds: 60, output_limit: 64000 }, projectId)
        : await executeStudioTool('studio_run_command', { run_id: prepared!.run_id, contract_sha256: prepared!.contract_sha256 }, projectId)
      if (current !== generation.current) return
      display(result, preparing)
    } catch (reason) { if (current === generation.current) setError(reason instanceof Error ? reason.message : 'Command request failed.') }
    finally { if (current === generation.current) setBusy(false) }
  }
  const approve = async () => {
    const grant = pending?.result.approval?.request_id
    if (!grant || !pending) return
    const preparing = pending.preparing
    const current = ++generation.current
    setBusy(true); setError(null)
    try {
      await decideApproval(grant, true, projectId)
      if (current !== generation.current) return
      const result = await continueStudioTool(grant, projectId)
      if (current !== generation.current) return
      setPending(null)
      display(result, preparing)
    } catch (reason) { if (current === generation.current) setError(reason instanceof Error ? reason.message : 'Approved command failed.') }
    finally { if (current === generation.current) setBusy(false) }
  }
  const reject = async () => {
    const grant = pending?.result.approval?.request_id
    if (!grant) return
    const current = ++generation.current
    setBusy(true); setError(null)
    try {
      await decideApproval(grant, false, projectId)
      if (current !== generation.current) return
      setPending(null); setPrepared(null)
      setOutcome({ status: 'denied', detail: 'Approval rejected. No command was dispatched.', result: null })
    } catch (reason) { if (current === generation.current) setError(reason instanceof Error ? reason.message : 'Approval rejection failed.') }
    finally { if (current === generation.current) setBusy(false) }
  }
  return <section className="surface studio-commands">
    <h2>Test and build commands</h2>
    <p>Inspect a local Git repository with a separately approved command. Linked worktrees are currently unsupported.</p>
    <div>{(['status', 'diff', 'log'] as const).map((operation) => <button key={operation} className="button subtle" disabled={busy || !authenticated} onClick={() => void request(true, operation)}>Review Git {operation}</button>)}</div>
    <p>Approved code can access your user files and network. Review the executable, arguments and project directory before running it. Commands have a 60-second limit and bounded output.</p>
    <label>Executable path<input aria-label="Command executable path" value={executable} disabled={busy} onChange={(event) => { clearProposal(); setExecutable(event.target.value) }} placeholder="Absolute path to an .exe" /></label>
    <label>Arguments<textarea aria-label="Command arguments JSON" value={argumentsText} disabled={busy} onChange={(event) => { clearProposal(); setArgumentsText(event.target.value) }} /></label>
    <label>Project directory<input aria-label="Command project directory" value={cwd} disabled={busy} onChange={(event) => { clearProposal(); setCwd(event.target.value) }} /></label>
    <button className="button subtle" disabled={busy || !authenticated || !executable} onClick={() => void request(true)}>Review command</button>
    {prepared && <div><h3>Prepared command</h3><pre>{JSON.stringify(prepared.command, null, 2)}</pre><button className="button" disabled={busy} onClick={() => void request(false)}>Request command approval</button></div>}
    {pending && <div><h3>{pending.preparing ? 'Command preparation read approval' : 'Exact command approval'}</h3><pre>{JSON.stringify(pending.result.approval?.preview, null, 2)}</pre><button className="button" disabled={busy} onClick={() => void approve()}>{pending.preparing ? 'Approve command preparation read' : 'Approve and run exact command'}</button><button className="button subtle" disabled={busy} onClick={() => void reject()}>Reject command approval</button></div>}
    {busy && <p role="status">Waiting for command operation…</p>}
    {error && <p role="alert">{error}</p>}
    {outcome && <div><p role="status">Command outcome: {outcome.status}. Exit code: {String(outcome.result?.exit_code ?? 'unavailable')}.</p><p>{outcome.detail}</p><pre aria-label="Command output">{String(outcome.result?.output ?? '')}</pre></div>}
  </section>
}
