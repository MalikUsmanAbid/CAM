import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { initializeDesktopSession } from './api'
import { desktopBridge } from './model-setup'
import ModelSetup from './ModelSetup'

export default function DesktopWelcome({ children }: { children: ReactNode }) {
  const bridge = desktopBridge()
  const [stage, setStage] = useState<'loading' | 'setup' | 'saved' | 'done'>(bridge ? 'loading' : 'done')
  const [existing, setExisting] = useState(false)
  const [connected, setConnected] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    let active = true
    if (bridge) void initializeDesktopSession().then(() => bridge.core.invoke<{ completed: boolean; existing_configuration: boolean }>('desktop_setup_status')).then(value => {
      if (typeof value.completed !== 'boolean' || typeof value.existing_configuration !== 'boolean') throw new Error('Invalid setup state')
      if (active) { setConnected(true); setExisting(value.existing_configuration); setStage(value.completed ? 'done' : 'setup') }
    }).catch(() => { if (active) { setError('Setup could not load. You can continue without changing your saved settings.'); setStage('setup') } })
    return () => { active = false }
  }, [bridge])
  useEffect(() => { heading.current?.focus() }, [stage])
  if (stage === 'done' || !bridge) return children
  const finish = async () => {
    setBusy(true); setError(null)
    try { await bridge.core.invoke('finish_desktop_setup'); setStage('done') }
    catch { setError('Could not remember this choice. Continue without changing settings, or try again.') }
    finally { setBusy(false) }
  }
  return <main className="welcome-page">
    <header className="welcome-brand"><span className="welcome-mark">C</span><strong>CAM</strong><span>Your personal workspace</span></header>
    <div className="welcome-content"><p className="eyebrow">WELCOME</p>
      <h1 ref={heading} tabIndex={-1}>{stage === 'loading' ? 'Getting your workspace ready…' : stage === 'saved' ? 'Your AI setup is saved' : 'Make CAM yours'}</h1>
      {stage === 'loading' ? <p role="status">Connecting to your private desktop session.</p> : <>
        <p>{existing ? 'Your existing settings are safe. Keep them and continue, or choose a new AI below.' : 'Choose the AI you want to use. You can also explore CAM first and connect later.'}</p>
        <ol className="setup-progress" aria-label="Setup progress"><li>1. Company</li><li>2. Key & model</li><li aria-current={stage === 'saved' ? 'step' : undefined}>3. Finish</li></ol>
        {error && <p role="alert" className="setup-error">{error}</p>}
        {stage === 'setup' && <ModelSetup authenticated={connected} onBusyChange={setBusy} onSaved={() => { setBusy(false); setStage('saved') }} />}
        {stage === 'saved' && <section className="surface"><h2>One restart before using your new model</h2><p>Open CAM now, then choose Exit CAM from the Windows tray and reopen. Your saved selection will load. Paid requests require explicit cloud consent and limits in Settings.</p></section>}
        <div className="welcome-actions"><button className="button primary" disabled={busy} onClick={() => void finish()}>{stage === 'saved' ? 'Open CAM' : existing ? 'Keep my settings and continue' : 'Set up later'}</button>
          {error && <button className="button subtle" disabled={busy} onClick={() => setStage('done')}>Continue without changing settings</button>}
          <p>No files are removed. You can reopen AI setup from Settings at any time.</p></div>
      </>}
    </div>
  </main>
}
