import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { configureCloudPolicy, getActionPolicy, getProjects, getSessionEpoch, getUsage, registerProject, updateActionPolicy } from './api'
import type { ActionPolicy, JsonRecord, RegisteredProject } from './api'
import NewProjectPanel from './NewProjectPanel'
import DesktopModelPanel from './DesktopModelPanel'
import ModelSetup from './ModelSetup'
import BackupPanel from './BackupPanel'

const categories = [['read_only', 'Read files and local information'], ['local_edit', 'Edit local files'], ['command', 'Run commands'], ['account_write', 'Change connected accounts'], ['publish', 'Send or publish'], ['financial', 'Financial actions'], ['destructive', 'Destructive actions']]

export default function FoundationPanel({ authenticated, onProjectsChanged }: { authenticated: boolean; onProjectsChanged?: (items: RegisteredProject[]) => void }) {
  const [projects, setProjects] = useState<RegisteredProject[]>([])
  const [usage, setUsage] = useState<JsonRecord | null>(null)
  const [policy, setPolicy] = useState<ActionPolicy | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState('')
  const [root, setRoot] = useState('')
  const [enabled, setEnabled] = useState(false)
  const [consent, setConsent] = useState(false)
  const [fields, setFields] = useState({ endpoint: '', model: '', daily: '', task: '', input: '', output: '' })
  useEffect(() => {
    if (!authenticated) return
    let active = true
    void Promise.all([getProjects(), getUsage(), getActionPolicy()]).then(([items, status, rules]) => {
      if (active) { setProjects(items); setUsage(status); if (typeof rules.revision === 'number') setPolicy(rules) }
    }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : 'Foundation status unavailable.') })
    return () => { active = false }
  }, [authenticated])
  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(null)
    try { await action() } catch (reason) { setError(reason instanceof Error ? reason.message : 'Request failed.') }
    finally { setBusy(false) }
  }
  const saveBudget = (event: FormEvent) => {
    event.preventDefault()
    const micro = (value: string) => Math.round(Number(value) * 1_000_000)
    void run(async () => { setUsage(await configureCloudPolicy({
      endpoint: fields.endpoint, model: fields.model, enabled, transmission_consent: consent,
      daily_micro_usd: micro(fields.daily), task_micro_usd: micro(fields.task),
      input_micro_usd_per_million_tokens: fields.input ? micro(fields.input) : null,
      output_micro_usd_per_million_tokens: fields.output ? micro(fields.output) : null,
    })) })
  }
  return <>
    <ModelSetup authenticated={authenticated} />
    <BackupPanel key={`${authenticated}:${getSessionEpoch()}`} authenticated={authenticated} />
    <details className="surface"><summary>Advanced provider and voice settings</summary><DesktopModelPanel authenticated={authenticated} /></details>
    <section className="surface"><h2>Action permissions</h2><p>Blocked categories cannot execute. All file edits and consequential actions still require an exact approval. Saving a policy invalidates existing approvals.</p>
      {policy ? <form onSubmit={(event) => { event.preventDefault(); void run(async () => { setPolicy(await updateActionPolicy({ blocked_categories: policy.blocked_categories, require_read_approval: policy.require_read_approval })) }) }}>
        <p>Policy revision: {policy.revision}</p>
        <label><input type="checkbox" checked={policy.require_read_approval} onChange={(event) => setPolicy({ ...policy, require_read_approval: event.target.checked })} />Require approval for read-only actions</label>
        <fieldset><legend>Block categories</legend>{categories.map(([category, label]) => <label key={category}><input type="checkbox" checked={policy.blocked_categories.includes(category)} onChange={(event) => setPolicy({ ...policy, blocked_categories: event.target.checked ? [...policy.blocked_categories, category] : policy.blocked_categories.filter((item) => item !== category) })} />{label}</label>)}</fieldset>
        <button className="button" disabled={!authenticated || busy}>Save action policy</button>
      </form> : <p>Pair the dashboard to load the current permissions.</p>}
    </section>
    <section className="surface"><h2>Registered projects</h2><p>Register an existing directory. Registration does not edit files or send their contents to a model.</p>
      {!authenticated && <p>Pair the dashboard before managing projects or model budgets.</p>}
      <form onSubmit={(event) => { event.preventDefault(); void run(async () => { await registerProject(name, root); const items = await getProjects(); setProjects(items); onProjectsChanged?.(items); setName(''); setRoot('') }) }}>
        <label>Project name<input value={name} onChange={(event) => setName(event.target.value)} maxLength={100} required /></label>
        <label>Existing directory<input value={root} onChange={(event) => setRoot(event.target.value)} maxLength={1000} required /></label>
        <button className="button" disabled={!authenticated || busy}>Register project</button>
      </form>
      <ul>{projects.map((project) => <li key={project.project_id}><strong>{project.name}</strong> — {project.root}</li>)}</ul>
      <NewProjectPanel authenticated={authenticated} onCreated={(items) => { setProjects(items); onProjectsChanged?.(items) }} />
    </section>
    <section className="surface"><h2>Cloud consent and budgets</h2><p>Cloud model calls are disabled by default. Prices and budgets must be configured for the exact endpoint and model. Local loopback models do not use this cloud budget.</p>
      <p>Reserved today: ${((Number(usage?.reserved_today_micro_usd) || 0) / 1_000_000).toFixed(6)}. Reservations are conservative estimates, including failed or uncertain requests; provider invoices may differ.</p>
      <form onSubmit={saveBudget}>
        <label>Provider endpoint<input type="url" value={fields.endpoint} onChange={(event) => setFields({ ...fields, endpoint: event.target.value })} required /></label>
        <label>Exact model<input value={fields.model} onChange={(event) => setFields({ ...fields, model: event.target.value })} required /></label>
        {([['daily', 'Daily limit (USD)'], ['task', 'Per-task limit (USD)'], ['input', 'Input price (USD per million tokens)'], ['output', 'Output price (USD per million tokens)']] as const).map(([key, label]) => <label key={key}>{label}<input type="number" min="0.000001" step="0.000001" max="1000" required={enabled} value={fields[key]} onChange={(event) => setFields({ ...fields, [key]: event.target.value })} /></label>)}
        <label><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />I consent to sending task context to this configured cloud provider.</label>
        <label><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />Enable cloud calls for this endpoint and model</label>
        <button className="button" disabled={!authenticated || busy || (enabled && !consent)}>Save model policy</button>
      </form>
      {Array.isArray(usage?.policies) && <p>{usage.policies.length} configured model policies. The strictest enabled limits apply across providers.</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  </>
}
