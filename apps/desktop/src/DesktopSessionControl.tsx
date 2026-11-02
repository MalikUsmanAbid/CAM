import { useEffect, useRef, useState } from 'react'
import { renewDesktopSession } from './api'

export default function DesktopSessionControl({ onRenewed }: { onRenewed: () => void }) {
  const native = Boolean((window as unknown as { __TAURI__?: unknown }).__TAURI__)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  if (!native) return null
  return <section className="surface"><h3>Private desktop session</h3>
    <p className="helper-text">Renew an expired session without restarting. Old action approvals and pending sign-in states need fresh authorization.</p>
    <button className="button subtle" disabled={busy} onClick={() => {
      setBusy(true); setMessage(null)
      void renewDesktopSession().then(() => {
        if (mounted.current) { setMessage('Private session renewed. Request fresh approvals for pending actions.'); onRenewed() }
      }).catch(() => { if (mounted.current) setMessage('Renewal failed. Restart the desktop application to reconnect.') })
        .finally(() => { if (mounted.current) setBusy(false) })
    }}>Renew private session</button>
    {message && <p role="status">{message}</p>}
  </section>
}
