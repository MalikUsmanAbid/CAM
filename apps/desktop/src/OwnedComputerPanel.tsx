import { useEffect, useRef, useState } from 'react'
import { executeOwnedComputerTool, getSessionEpoch, getTools, type JsonRecord, type StudioResult } from './api'

type Action = Parameters<typeof executeOwnedComputerTool>[0]

export default function OwnedComputerPanel({ projectId, authenticated }: { projectId: string | null; authenticated: boolean }) {
  const [available, setAvailable] = useState(false)
  const [titles, setTitles] = useState('')
  const [attended, setAttended] = useState(false)
  const [sessionId, setSessionId] = useState('')
  const [controls, setControls] = useState<JsonRecord[]>([])
  const [observation, setObservation] = useState('')
  const [target, setTarget] = useState('')
  const [value, setValue] = useState('')
  const [result, setResult] = useState<StudioResult | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const mounted = useRef(true)
  const canExecute = available && authenticated && !projectId
  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    const epoch = getSessionEpoch()
    if (authenticated && !projectId) void getTools(controller.signal, null).then((tools) => {
      if (!controller.signal.aborted && epoch === getSessionEpoch()) setAvailable(tools.some((tool) => tool.name === 'computer_owned_open' && tool.enabled === true))
    }).catch(() => { if (!controller.signal.aborted) setAvailable(false) })
    return () => { mounted.current = false; controller.abort() }
  }, [authenticated, projectId])
  async function execute(name: Action, arguments_: JsonRecord) {
    if (!canExecute || busy) return
    const epoch = getSessionEpoch()
    setBusy(true); setError('')
    try {
      const output = await executeOwnedComputerTool(name, arguments_)
      if (!mounted.current || epoch !== getSessionEpoch()) return
      setResult(output)
      if (output.status === 'success') {
        if (typeof output.result?.session_id === 'string') setSessionId(output.result.session_id)
        if (name === 'computer_owned_observe' && typeof output.result?.text === 'string' && typeof output.result.observation_sha256 === 'string') {
          const entries: unknown = JSON.parse(output.result.text)
          if (!Array.isArray(entries) || !entries.every((item) => item && typeof item === 'object' && typeof item.target === 'string')) throw new Error('Invalid observation controls.')
          setControls(entries as JsonRecord[]); setObservation(output.result.observation_sha256); setTarget('')
        }
        if (['computer_owned_invoke', 'computer_owned_set_value', 'computer_owned_close'].includes(name)) {
          setObservation(''); setControls([]); setTarget(''); setValue('')
        }
      } else if (output.status !== 'approval_required') {
        setObservation(''); setControls([]); setTarget('')
        setError(output.detail || 'Operation was not verified. Reconcile application state before another action.')
      }
    } catch (reason) {
      if (mounted.current && epoch === getSessionEpoch()) { setError(reason instanceof Error ? reason.message : 'Computer operation failed.'); setObservation(''); setControls([]) }
    } finally { if (mounted.current && epoch === getSessionEpoch()) setBusy(false) }
  }
  function controlAction(name: Action, replacement = '') {
    void execute(name, { operation_id: crypto.randomUUID().replaceAll('-', ''), session_id: sessionId,
      target, observation_sha256: observation, value: replacement })
  }
  return <section className="surface"><h2>Attended computer control</h2>
    <p>Owned workers use exact window/process identities and observed control fingerprints. Global keys, coordinate clicks, password fields, elevated windows and cross-process controls are excluded.</p>
    {!authenticated && <p>Pair with the backend first.</p>}
    {projectId && <p>Select Personal scope to manage attended desktop sessions.</p>}
    {!available && <p role="status">Unavailable: owned-worker source exists, but Windows security acceptance has not enabled production execution.</p>}
    {error && <p role="alert">{error}</p>}
    <form onSubmit={(event) => { event.preventDefault(); void execute('computer_owned_open', { titles: titles.split('\n').map((title) => title.trim()).filter(Boolean), attended }) }}>
      <label>Exact visible window titles, one per line<textarea value={titles} maxLength={602} onChange={(event) => { setTitles(event.target.value); setAttended(false) }} required /></label>
      <label><input type="checkbox" checked={attended} onChange={(event) => setAttended(event.target.checked)} />I am present and selected these exact windows for this session.</label>
      <button className="button primary" disabled={!available || busy || !attended}>Request attended session approval</button>
    </form>
    <button className="button subtle" disabled={!available || busy} onClick={() => void execute('computer_owned_sessions', {})}>List my live owned sessions</button>
    <button className="button subtle" disabled={!available || busy} onClick={() => void execute('computer_owned_operations', {})}>Inspect recent UIA outcomes</button>
    <label>Owned session ID<input value={sessionId} pattern="[a-f0-9]{32}" onChange={(event) => { setSessionId(event.target.value); setObservation(''); setControls([]); setTarget('') }} /></label>
    <button className="button subtle" disabled={!available || busy || !/^[a-f0-9]{32}$/.test(sessionId)} onClick={() => void execute('computer_owned_observe', { session_id: sessionId })}>Observe selected windows</button>
    <button className="button subtle" disabled={!available || busy || !/^[a-f0-9]{32}$/.test(sessionId)} onClick={() => void execute('computer_owned_close', { session_id: sessionId })}>Close owned session</button>
    <label>Observed control<select value={target} onChange={(event) => setTarget(event.target.value)}><option value="">Select a fresh observed control</option>{controls.map((control) => <option key={String(control.target)} value={String(control.target)}>{String(control.type)} · {String(control.name || control.automation_id || control.target)}</option>)}</select></label>
    <label>Exact replacement text for non-password edit control<textarea value={value} maxLength={2000} onChange={(event) => setValue(event.target.value)} /></label>
    <button className="button subtle" disabled={!available || busy || !target || !observation} onClick={() => controlAction('computer_owned_locate')}>Inspect exact control</button>
    <button className="button primary" disabled={!available || busy || !target || !observation} onClick={() => controlAction('computer_owned_invoke')}>Request exact Invoke approval</button>
    <button className="button primary" disabled={!available || busy || !target || !observation || !value} onClick={() => controlAction('computer_owned_set_value', value)}>Request exact SetValue approval</button>
    <p className="helper-text">After approval, observe again. A timed-out or cancelled action may already have reached the target application; do not blindly retry it. Emergency stop remains in Execution Controls.</p>
    {result && <pre role="status" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(result, null, 2)}</pre>}
  </section>
}
