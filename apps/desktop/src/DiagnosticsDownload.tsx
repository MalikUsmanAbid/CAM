import { useEffect, useRef, useState } from 'react'
import { getSessionEpoch, workspaceRequest, type JsonRecord } from './api'
import { diagnosticReport } from './diagnostic-report'

export default function DiagnosticsDownload() {
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const active = useRef<AbortController | null>(null)
  useEffect(() => () => { active.current?.abort() }, [])
  async function save() {
    if (active.current) return
    const controller = new AbortController(), epoch = getSessionEpoch()
    active.current = controller
    setBusy(true); setError('')
    try {
      const snapshot = await workspaceRequest<JsonRecord>('/diagnostics', 'GET', undefined, null, controller.signal)
      if (controller.signal.aborted || epoch !== getSessionEpoch()) return
      const report = diagnosticReport(snapshot)
      const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' })
      const urlApi = URL, url = urlApi.createObjectURL(blob)
      try {
        const link = document.createElement('a')
        link.href = url; link.download = 'CAM-redacted-diagnostics.json'; link.click()
      } finally {
        window.setTimeout(() => urlApi.revokeObjectURL(url), 1000)
      }
    } catch (reason) {
      if (!controller.signal.aborted && epoch === getSessionEpoch()) setError(reason instanceof Error ? reason.message : 'Diagnostics are unavailable.')
    } finally {
      if (active.current === controller) active.current = null
      if (!controller.signal.aborted && epoch === getSessionEpoch()) setBusy(false)
    }
  }
  return <section aria-label="Redacted support report">
    <h3>Support report</h3>
    <p>Download basic application/runtime status. Prompts, project paths, record contents, credentials, endpoints and account details are excluded. This report does not run tests or verify the release.</p>
    {error && <p role="alert">{error}</p>}
    <button type="button" className="button subtle" disabled={busy} onClick={() => void save()}>Download redacted diagnostics</button>
  </section>
}
