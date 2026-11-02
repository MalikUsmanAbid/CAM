import { useEffect, useRef, useState } from 'react'
import { discardBackup, getBackupStatus, getSessionEpoch, prepareBackup, readEncryptedBackup, type BackupReady, type BackupStatus } from './api'

export default function BackupPanel({ authenticated }: { authenticated: boolean }) {
  const [status, setStatus] = useState<BackupStatus | null>(null)
  const [passphrase, setPassphrase] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const mounted = useRef(true)
  const download = useRef<AbortController | null>(null)
  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    const epoch = getSessionEpoch()
    if (authenticated) void getBackupStatus(controller.signal).then((value) => {
      if (!controller.signal.aborted && epoch === getSessionEpoch()) setStatus(value)
    }).catch(() => { if (!controller.signal.aborted) setError('Backup status is unavailable.') })
    return () => { mounted.current = false; controller.abort(); download.current?.abort() }
  }, [authenticated])
  async function run(operation: () => Promise<void>) {
    if (!authenticated) return
    const epoch = getSessionEpoch()
    setBusy(true); setError('')
    try {
      await operation()
      const next = await getBackupStatus()
      if (mounted.current && epoch === getSessionEpoch()) setStatus(next)
    } catch (reason) {
      if (mounted.current && epoch === getSessionEpoch()) setError(reason instanceof Error ? reason.message : 'Backup operation failed.')
    } finally { if (mounted.current && epoch === getSessionEpoch()) setBusy(false) }
  }
  async function save(item: BackupReady) {
    const epoch = getSessionEpoch()
    const controller = new AbortController()
    download.current = controller
    try {
      const blob = await readEncryptedBackup(item, controller.signal)
      if (!mounted.current || epoch !== getSessionEpoch() || controller.signal.aborted) return
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a'); link.href = url; link.download = `CAM-${item.download_id}.encrypted`
      link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } finally { if (download.current === controller) download.current = null }
  }
  return <section className="surface"><h2>Encrypted database backup</h2>
    <p>Export database records and knowledge snapshots after exact approval. Working files, protected credentials and model weights are excluded. Keep the recovery passphrase separately; losing it makes the encrypted backup unreadable.</p>
    {!status?.available && <p role="status">Unavailable until optional portable-encryption support is installed and configured. No plaintext fallback is provided.</p>}
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    <form onSubmit={(event) => {
      event.preventDefault()
      if (passphrase !== confirmation) { setError('Recovery passphrases do not match.'); return }
      const secret = passphrase
      const epoch = getSessionEpoch()
      setPassphrase(''); setConfirmation('')
      void run(async () => {
        const result = await prepareBackup(secret)
        if (mounted.current && epoch === getSessionEpoch()) setMessage(result.status === 'approval_required' ? 'Review backup_export in Approvals, then refresh this list.' : result.detail)
      })
    }}>
      <label>Separate recovery passphrase<input type="password" autoComplete="new-password" value={passphrase} minLength={16} maxLength={256} onChange={(event) => setPassphrase(event.target.value)} required /></label>
      <label>Confirm recovery passphrase<input type="password" autoComplete="new-password" value={confirmation} minLength={16} maxLength={256} onChange={(event) => setConfirmation(event.target.value)} required /></label>
      <button className="button primary" disabled={!authenticated || busy || !status?.available}>Request encrypted export approval</button>
    </form>
    <button className="button subtle" disabled={!authenticated || busy} onClick={() => void run(async () => undefined)}>Refresh owned exports</button>
    {status?.pending?.map((item) => <div key={item.capture_id}><p>Pending approval secret · expires in {item.expires_in_seconds}s</p><button className="button subtle" disabled={busy} onClick={() => void run(async () => { await discardBackup(item.capture_id) })}>Discard pending secret</button></div>)}
    {status?.ready?.map((item) => <div key={item.download_id}><p>Encrypted export · {item.bytes} bytes · expires in {item.expires_in_seconds}s</p><code style={{ overflowWrap: 'anywhere' }}>{item.sha256}</code>
      <button className="button primary" disabled={busy} onClick={() => void run(() => save(item))}>Verify checksum and download</button>
      <button className="button subtle" disabled={busy} onClick={() => void run(async () => { await discardBackup(item.download_id) })}>Discard encrypted export</button>
    </div>)}
    <p className="helper-text">Passphrases and prepared downloads expire after five minutes and are never stored in browser storage or approval records. Python/browser memory cannot guarantee secret zeroization. Restore/cloud transfer remain separate explicit maintenance steps; this does not replace or migrate the running database.</p>
  </section>
}
