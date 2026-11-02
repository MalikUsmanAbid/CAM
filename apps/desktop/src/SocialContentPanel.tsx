import { useEffect, useRef, useState } from 'react'
import { getSessionEpoch, workspaceRequest } from './api'

const platforms = ['facebook', 'instagram', 'linkedin', 'x', 'youtube', 'tiktok', 'pinterest', 'threads', 'mastodon', 'bluesky']

export default function SocialContentPanel({ projectId }: { projectId: string | null }) {
  const [title, setTitle] = useState('')
  const [variants, setVariants] = useState<Record<string, string>>({ linkedin: '' })
  const [sources, setSources] = useState('')
  const [output, setOutput] = useState<unknown>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  async function execute(catalog = false) {
    const epoch = getSessionEpoch()
    setBusy(true); setError('')
    try {
      const result = await workspaceRequest('/knowledge/execute', 'POST', catalog ? { tool: 'social_catalog', arguments: {} } : {
        tool: 'social_prepare', arguments: { title,
          variants: Object.entries(variants).map(([platform, text]) => ({ platform, text, media_notes: '' })),
          sources: sources.split(',').map((value) => value.trim()).filter(Boolean), previous_id: null },
      }, projectId)
      if (mounted.current && epoch === getSessionEpoch()) setOutput(result)
    } catch (reason) {
      if (mounted.current && epoch === getSessionEpoch()) setError(reason instanceof Error ? reason.message : 'Campaign request failed.')
    } finally { if (mounted.current && epoch === getSessionEpoch()) setBusy(false) }
  }
  if (!projectId) return <section className="surface"><h3>Multi-platform content</h3><p>Select a registered project to keep campaign drafts and sources scoped together.</p></section>
  return <section className="surface"><h3>Multi-platform campaign drafts</h3>
    <p>Write a separate variant for each selected platform. Draft storage and export never publish, upload media or spend a paid allowance.</p>
    {error && <p role="alert">{error}</p>}
    <button className="button subtle" disabled={busy} onClick={() => void execute(true)}>Show actual platform capabilities</button>
    <form onSubmit={(event) => { event.preventDefault(); void execute() }}>
      <label>Campaign title<input value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} required /></label>
      <fieldset><legend>Platforms to draft for</legend>{platforms.map((platform) => <label key={platform}><input type="checkbox" checked={platform in variants} onChange={(event) => setVariants((current) => {
        if (event.target.checked) return { ...current, [platform]: '' }
        const next = { ...current }; delete next[platform]; return next
      })} />{platform}</label>)}</fieldset>
      {Object.entries(variants).map(([platform, text]) => <label key={platform}>{platform} draft<textarea value={text} maxLength={12000} onChange={(event) => setVariants({ ...variants, [platform]: event.target.value })} required /></label>)}
      <label>Campaign source document IDs<input value={sources} maxLength={700} onChange={(event) => setSources(event.target.value)} /></label>
      <button className="button primary" disabled={busy || !Object.keys(variants).length || Object.values(variants).some((value) => !value.trim())}>Request campaign storage approval</button>
    </form>
    <p className="helper-text">After approval, the versioned content artifact appears in Knowledge & Creation. Inspect its hash and export as JSON. Provider character/media rules still need adapter validation; identical drafts do not imply platform-ready content.</p>
    {output !== null && <pre role="status" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(output, null, 2)}</pre>}
  </section>
}
