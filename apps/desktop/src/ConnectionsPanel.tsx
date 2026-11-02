import { useEffect, useRef, useState } from 'react'
import { getSessionEpoch, workspaceRequest, type JsonRecord } from './api'
import { oauthRequest } from './api'
import OAuthPanel from './OAuthPanel'
import LinkedInPublisher from './LinkedInPublisher'
import MastodonPublisher from './MastodonPublisher'
import BlueskyPublisher from './BlueskyPublisher'
import ThreadsPublisher from './ThreadsPublisher'
import PinterestPublisher from './PinterestPublisher'
import ExtendedPublisher from './ExtendedPublisher'
import SkillLifecyclePanel from './SkillLifecyclePanel'

const extendedScopes: Record<string, string[]> = { facebook: ['pages_manage_posts', 'pages_read_engagement'], instagram: ['instagram_basic', 'instagram_content_publish', 'pages_read_engagement'], x: ['tweet.read', 'tweet.write', 'users.read'], youtube: ['https://www.googleapis.com/auth/youtube.upload'], tiktok: ['video.publish'] }

const scopes: Record<string, string> = { github: 'issues:write', google: 'https://www.googleapis.com/auth/gmail.send', microsoft: 'Mail.Send', linkedin: 'w_member_social', mastodon: 'write:statuses', bluesky: 'com.atproto.access' }
const calendarScopes: Record<string, string> = { google: 'https://www.googleapis.com/auth/calendar.events.owned', microsoft: 'Calendars.ReadWrite' }

export default function ConnectionsPanel({ projectId, authenticated }: { projectId: string | null; authenticated: boolean }) {
  const [items, setItems] = useState<JsonRecord[]>([])
  const [provider, setProvider] = useState('github')
  const [access, setAccess] = useState('mail')
  const [starts, setStarts] = useState('')
  const [ends, setEnds] = useState('')
  const [invite, setInvite] = useState(false)
  const [label, setLabel] = useState('')
  const [reference, setReference] = useState('')
  const [serviceBase, setServiceBase] = useState('')
  const [consent, setConsent] = useState(false)
  const [manifest, setManifest] = useState('')
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [target, setTarget] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<unknown>(null)
  const mounted = useRef(true)
  async function refresh(signal?: AbortSignal) {
    if (projectId) return workspaceRequest<JsonRecord[]>('/skill-manifests', 'GET', undefined, projectId, signal)
    return (await workspaceRequest<{ accounts: JsonRecord[] }>('/accounts', 'GET', undefined, null, signal)).accounts
  }
  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    const epoch = getSessionEpoch()
    if (authenticated) void refresh(controller.signal).then((value) => {
      if (!controller.signal.aborted && epoch === getSessionEpoch()) setItems(value)
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Connections are unavailable.')
    })
    return () => { mounted.current = false; controller.abort() }
    // Parent remounts on every project switch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authenticated, projectId])
  async function run(operation: () => Promise<unknown>) {
    const epoch = getSessionEpoch()
    setBusy(true); setError(null)
    try {
      const response = await operation()
      if (!mounted.current || epoch !== getSessionEpoch()) return
      setResult(response)
      const records = await refresh()
      if (mounted.current && epoch === getSessionEpoch()) setItems(records)
    } catch (reason) {
      if (mounted.current && epoch === getSessionEpoch()) setError(reason instanceof Error ? reason.message : 'Connection operation failed.')
    } finally { if (mounted.current && epoch === getSessionEpoch()) setBusy(false) }
  }
  if (!authenticated) return <section className="surface"><p>Pair with the backend first.</p></section>
  return <div className="page-stack"><section className="surface"><h2>{projectId ? 'Project skill manifests' : 'Connected accounts'}</h2>
    {error && <p role="alert">{error}</p>}
    {projectId ? <form onSubmit={(event) => {
      event.preventDefault()
      setConsent(false)
      void run(() => workspaceRequest('/skill-manifests', 'POST', { manifest: JSON.parse(manifest), consent }, projectId))
    }}>
      <p>Declarative skills reference existing project tools. They cannot install executable hooks, enable missing tools or override permissions. New versions are installed disabled.</p>
      <label>Manifest JSON<textarea value={manifest} maxLength={8000} onChange={(event) => { setManifest(event.target.value); setConsent(false) }} required /></label>
      <label><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />I reviewed this manifest and consent to installing this version.</label>
      <button className="button primary" disabled={busy || !consent}>Install disabled</button>
    </form> : <form onSubmit={(event) => {
      event.preventDefault()
      const selectedScopes = extendedScopes[provider] ?? (provider === 'pinterest' ? ['boards:read', 'boards:write', 'pins:read', 'pins:write'] : provider === 'threads' ? ['threads_basic', 'threads_content_publish'] : ['github', 'linkedin', 'mastodon', 'bluesky'].includes(provider) ? [scopes[provider]] : [
        ...(access !== 'calendar' ? [scopes[provider]] : []), ...(access !== 'mail' ? [calendarScopes[provider]] : []),
      ])
      void run(() => workspaceRequest('/accounts', 'POST', { provider, label, secret_reference: reference, scopes: selectedScopes, transmission_consent: consent, ...(['mastodon', 'bluesky'].includes(provider) ? { service_base: serviceBase } : {}) }))
    }}>
      <p>Configure protected token references here, or use Google/Microsoft OAuth below. A connection records configuration; it does not prove live account access.</p>
      <label>Provider<select value={provider} onChange={(event) => { setProvider(event.target.value); setConsent(false); setServiceBase('') }}><option value="github">GitHub issues</option><option value="google">Google Gmail</option><option value="microsoft">Microsoft mail</option><option value="linkedin">LinkedIn member posts</option><option value="mastodon">Mastodon statuses</option><option value="bluesky">Bluesky public text posts</option><option value="threads">Threads public text posts</option><option value="pinterest">Pinterest image pins</option>{Object.keys(extendedScopes).map((name) => <option key={name} value={name}>{name.toUpperCase()}</option>)}</select></label>
      {extendedScopes[provider] && <p>Requested access: {extendedScopes[provider].join(', ')}. Configure an existing protected access token; this does not perform login or enable paid X usage.</p>}
      {provider === 'bluesky' && <label>Exact Bluesky PDS origin<input type="url" value={serviceBase} maxLength={300} onChange={(event) => { setServiceBase(event.target.value); setConsent(false) }} placeholder="https://your-pds.example" required /></label>}
      {provider === 'pinterest' && <p>Requested provider access: boards read/write and pins read/write, as listed by the current create-Pin API contract. No advertising actions are implemented.</p>}
      {provider === 'mastodon' && <label>Exact Mastodon instance origin<input type="url" value={serviceBase} maxLength={300} onChange={(event) => { setServiceBase(event.target.value); setConsent(false) }} placeholder="https://your.instance" required /></label>}
      {['google', 'microsoft'].includes(provider) && <label>Requested account access<select value={access} onChange={(event) => setAccess(event.target.value)}><option value="mail">Mail send only</option><option value="calendar">Calendar creation only</option><option value="both">Mail and calendar</option></select></label>}
      <label>Account label<input value={label} maxLength={100} onChange={(event) => setLabel(event.target.value)} required /></label>
      <label>Existing backend-protected credential reference<input value={reference} pattern="vault:[a-f0-9]{32}" onChange={(event) => setReference(event.target.value)} required /></label>
      <label><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />I consent to separately approved payloads through this account with the selected access.</label>
      <button className="button primary" disabled={busy || !consent}>Connect configured token</button>
    </form>}
  </section>
    {!projectId && <><button className="button subtle" disabled={busy} onClick={() => void run(async () => ({ status: 'Connections refreshed' }))}>Refresh connections</button><OAuthPanel /></>}
    {items.map((item) => <section className="surface" key={String(item.skill_id ?? item.account_id)}>
      <h3>{String(item.label ?? item.skill_id)}</h3>
      <p>{String(item.provider ?? item.version)} · {String(item.status ?? (item.enabled ? 'enabled' : 'disabled'))}</p>
      {projectId ? <>
        <button className="button subtle" disabled={busy} onClick={() => void run(() => workspaceRequest(`/skill-manifests/${encodeURIComponent(String(item.skill_id))}`, 'PUT', { enabled: !item.enabled, expected_version: item.version, manifest_sha256: item.manifest_sha256 }, projectId))}>{item.enabled ? 'Disable' : 'Enable installed skill'}</button>
        <SkillLifecyclePanel key={`${String(item.skill_id)}:${String(item.manifest_sha256)}`} projectId={projectId} skillId={String(item.skill_id)} onChanged={() => { void run(async () => ({ status: 'Skill inventory changed' })) }} />
      </> : <>
        <button className="button subtle" disabled={busy} onClick={() => void run(() => workspaceRequest('/accounts/execute', 'POST', { tool: 'account_publishing_status', arguments: { account_id: item.account_id, account_revision: item.revision } }))}>Inspect recorded account requests</button>
        {item.status === 'connected' && <>
          {item.oauth_state != null && <button className="button subtle" disabled={busy} onClick={() => void run(() => oauthRequest(`/accounts/${String(item.account_id)}/refresh`, 'POST'))}>Refresh OAuth credential</button>}
          <button className="button subtle" disabled={busy} onClick={() => void run(() => workspaceRequest(`/accounts/${String(item.account_id)}`, 'DELETE'))}>Revoke connection</button>
          {item.provider === 'linkedin' && <LinkedInPublisher account={item} onRequest={(arguments_) => run(() => workspaceRequest('/accounts/execute', 'POST', { tool: 'account_linkedin_post', arguments: arguments_ }))} busy={busy} />}
          {item.provider === 'mastodon' && <MastodonPublisher account={item} onRequest={(arguments_) => run(() => workspaceRequest('/accounts/execute', 'POST', { tool: 'account_mastodon_post', arguments: arguments_ }))} busy={busy} />}
          {item.provider === 'bluesky' && <BlueskyPublisher account={item} onRequest={(arguments_) => run(() => workspaceRequest('/accounts/execute', 'POST', { tool: 'account_bluesky_post', arguments: arguments_ }))} busy={busy} />}
          {item.provider === 'threads' && <ThreadsPublisher account={item} onRequest={(arguments_) => run(() => workspaceRequest('/accounts/execute', 'POST', { tool: 'account_threads_post', arguments: arguments_ }))} busy={busy} />}
          {item.provider === 'pinterest' && <PinterestPublisher account={item} onRequest={(arguments_) => run(() => workspaceRequest('/accounts/execute', 'POST', { tool: 'account_pinterest_pin', arguments: arguments_ }))} busy={busy} />}
          {extendedScopes[String(item.provider)] && <ExtendedPublisher key={`${String(item.account_id)}:${String(item.revision)}`} account={item} />}
          {['github', 'google', 'microsoft'].includes(String(item.provider)) && <form onSubmit={(event) => {
            event.preventDefault()
            const common = { operation_id: crypto.randomUUID().replaceAll('-', ''), account_id: item.account_id, account_revision: item.revision, body }
            const issue = item.provider === 'github'
            void run(() => workspaceRequest('/accounts/execute', 'POST', {
              tool: issue ? 'account_github_issue' : 'account_send_email',
              arguments: issue ? { ...common, title: subject, repository: target } : { ...common, subject, recipients: target.split(',').map((value) => value.trim()).filter(Boolean) },
            }))
          }}>
            <label>{item.provider === 'github' ? 'Repository owner/name' : 'Exact recipients, comma separated'}<input value={target} maxLength={4000} onChange={(event) => setTarget(event.target.value)} required /></label>
            <label>{item.provider === 'github' ? 'Issue title' : 'Subject'}<input value={subject} maxLength={200} onChange={(event) => setSubject(event.target.value)} required /></label>
            <label>Exact content<textarea value={body} maxLength={12000} onChange={(event) => setBody(event.target.value)} required /></label>
            <button className="button primary" disabled={busy}>Request exact-payload approval</button>
          </form>}
          {['google', 'microsoft'].includes(String(item.provider)) && <form onSubmit={(event) => {
            event.preventDefault()
            const start = new Date(starts), end = new Date(ends)
            if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) { setError('Choose valid event times.'); return }
            void run(() => workspaceRequest('/accounts/execute', 'POST', {
              tool: 'account_calendar_create', arguments: {
                operation_id: crypto.randomUUID().replaceAll('-', ''), account_id: item.account_id, account_revision: item.revision,
                title: subject, body, start: start.toISOString(), end: end.toISOString(),
                attendees: target.split(',').map((value) => value.trim()).filter(Boolean), send_invitations: invite,
              },
            }))
          }}>
            <h4>Create calendar event</h4>
            <label>Event title<input value={subject} maxLength={200} onChange={(event) => setSubject(event.target.value)} required /></label>
            <label>Event description<textarea value={body} maxLength={12000} onChange={(event) => setBody(event.target.value)} /></label>
            <label>Start in local time<input type="datetime-local" value={starts} onChange={(event) => setStarts(event.target.value)} required /></label>
            <label>End in local time<input type="datetime-local" value={ends} onChange={(event) => setEnds(event.target.value)} required /></label>
            <label>Attendees, optional comma-separated emails<input value={target} maxLength={4000} onChange={(event) => setTarget(event.target.value)} /></label>
            <label><input type="checkbox" checked={invite} onChange={(event) => setInvite(event.target.checked)} />I approve sending invitations to these exact attendees.</label>
            <button className="button primary" disabled={busy}>Request event approval</button>
            <p className="helper-text">Requires calendar access. No guests are added without invitation consent. Provider calendar rules may send their own notifications.</p>
          </form>}
        </>}
      </>}
    </section>)}
    {result !== null && <section className="surface"><h3>Result</h3><pre role="status" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(result, null, 2)}</pre><p>Review consequential actions in Approvals. Provider acceptance does not guarantee delivery. Reconcile uncertain outcomes before requesting another operation.</p></section>}
  </div>
}
