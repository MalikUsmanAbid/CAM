import { useEffect, useRef, useState } from 'react'
import { getSessionEpoch, workspaceRequest, type JsonRecord } from './api'

type Notifications = {
  isPermissionGranted: () => Promise<boolean>
  requestPermission: () => Promise<string>
  sendNotification: (value: { title: string; body: string }) => void | Promise<void>
}

export default function DesktopReminderNotifications({ projectId }: { projectId: string | null }) {
  const plugin = (window as unknown as { __TAURI__?: { notification?: Notifications } }).__TAURI__?.notification
  const [enabled, setEnabled] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const permissionGeneration = useRef(0)
  useEffect(() => () => { permissionGeneration.current += 1 }, [projectId])
  useEffect(() => {
    if (!enabled || !plugin) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    const epoch = getSessionEpoch()
    const storageKey = `jarvis-reminder-notices:${projectId ?? 'personal'}`
    const seen = new Set<string>()
    try {
      const stored: unknown = JSON.parse(sessionStorage.getItem(storageKey) ?? '[]')
      if (Array.isArray(stored)) for (const value of stored.slice(-200)) {
        if (typeof value === 'string' && /^[a-f0-9]{32}$/.test(value)) seen.add(value)
      }
    } catch { /* Only opaque deduplication IDs are cached; storage is optional. */ }
    async function poll() {
      try {
        const result = await workspaceRequest<{ items: JsonRecord[] }>('/notifications', 'GET', undefined, projectId, controller.signal)
        if (controller.signal.aborted || getSessionEpoch() !== epoch) return
        const unseen = result.items.filter((item) => ['pending', 'waiting_for_review'].includes(String(item.status)) && /^[a-f0-9]{32}$/.test(String(item.occurrence_id)) && !seen.has(String(item.occurrence_id))).slice(0, 100)
        if (unseen.length) {
          if (controller.signal.aborted || getSessionEpoch() !== epoch) return
          // Keep private reminder titles and question contents out of OS toasts.
          await plugin!.sendNotification({ title: 'CAM', body: 'A reminder is waiting in your Decisions inbox.' })
          for (const item of unseen) seen.add(String(item.occurrence_id))
          while (seen.size > 200) seen.delete(seen.values().next().value!)
          try { sessionStorage.setItem(storageKey, JSON.stringify([...seen])) } catch { /* Optional cache. */ }
        }
      } catch {
        if (!controller.signal.aborted) { setError('Desktop notification delivery is unavailable; use the inbox.'); setEnabled(false) }
      }
      if (!controller.signal.aborted && getSessionEpoch() === epoch) timer = setTimeout(() => { void poll() }, 15000)
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [enabled, plugin, projectId])
  if (!plugin) return null
  return <div>
    <button className="button subtle" onClick={() => {
      if (enabled) { permissionGeneration.current += 1; setEnabled(false); return }
      setError(null)
      const generation = ++permissionGeneration.current
      const epoch = getSessionEpoch()
      void (async () => {
        const granted = await plugin.isPermissionGranted() || await plugin.requestPermission() === 'granted'
        if (generation !== permissionGeneration.current || epoch !== getSessionEpoch()) return
        if (granted) setEnabled(true)
        else setError('Desktop notification permission was not granted.')
      })().catch(() => { if (generation === permissionGeneration.current && epoch === getSessionEpoch()) setError('Desktop notification permission is unavailable.') })
    }}>{enabled ? 'Turn off desktop notices' : 'Enable desktop notices for selected scope'}</button>
    <p className="helper-text">Opt-in notices run while the application is open, including its tray window. Exit stops delivery. Hidden-window timing depends on WebView2; this is not a Windows background service.</p>
    {error && <p role="status">{error}</p>}
  </div>
}
