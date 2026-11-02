import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import {
  API_BASE_URL, approveMcpServer, cancelSchedule, cancelTask, continueTool, createMemory, createMcpServer, createSchedule,
  createTask, createWorkflow, decideApproval, deleteMemory, deleteMcpServer, disableMcpServer,
  enableMcpServer, getActivity, getAiStatus, getApprovals, getCapabilities, getHealth,
  getMemories, getMcpServers, getSchedules, getSkills, getTaskSteps, getTasks, getTools,
  getWorkflows, getProjects, resumeTask, searchMemories, sendChat, transitionWorkflow, updateMemory,
  type ActivityItem, type ApprovalItem, type HealthStatus, type JsonRecord, type MemoryItem,
  type TaskStatus, type TaskStep, type RegisteredProject,
} from './api'
import AIOrb from './AIOrb'
import FoundationPanel from './FoundationPanel'
import CodingStudio from './CodingStudio'
import WorkspacePanel, { ArtifactDownload } from './WorkspacePanel'
import VoicePanel from './VoicePanel'
import ConnectionsPanel from './ConnectionsPanel'
import DesktopSessionControl from './DesktopSessionControl'
import DesktopReminderNotifications from './DesktopReminderNotifications'
import OwnedComputerPanel from './OwnedComputerPanel'
import { getSessionEpoch, hasSession, initializeDesktopSession, pairBackend, readProgressEvents } from './api'
import Icon from './Icon'
import { deriveOrbState } from './orb-state'
import './styles.css'
import './immersive.css'
import './easy-ui.css'

type View = 'overview' | 'studio' | 'chat' | 'tasks' | 'approvals' | 'memory' | 'tools' | 'capabilities' | 'mcp' | 'workflows' | 'schedules' | 'activity' | 'settings' | 'browser' | 'computer' | 'voice' | 'agents' | 'library' | 'decisions' | 'work' | 'safety' | 'connections'
type Message = { role: 'user' | 'assistant'; text: string }
type NavItem = { id: View; label: string; icon: string; count?: number }

const initialMessages: Message[] = [{ role: 'assistant', text: 'Hello Usman. I’m ready to help. You can chat with me or start a task that uses your local tools.' }]
const activeStatuses = new Set(['queued', 'running', 'waiting_for_approval', 'waiting_for_capability'])
const workflowTransitions: Record<string, string[]> = {
  pending: ['running', 'waiting_for_approval', 'waiting_for_capability', 'paused', 'failed', 'cancelled'],
  running: ['waiting_for_approval', 'waiting_for_capability', 'paused', 'completed', 'failed', 'cancelled'],
  waiting_for_approval: ['running', 'failed', 'cancelled'],
  waiting_for_capability: ['running', 'failed', 'cancelled'],
  paused: ['running', 'cancelled'],
}

const navigation: { heading: string; items: NavItem[] }[] = [
  { heading: 'BUILD', items: [{ id: 'studio', label: 'Coding Studio', icon: '⌘' }, { id: 'agents', label: 'Agent Workspace', icon: '◇' }] },
  { heading: 'PERSONAL WORK', items: [{ id: 'library', label: 'Knowledge & Creation', icon: '⌕' }, { id: 'decisions', label: 'Decisions & Reminders', icon: '◷' }, { id: 'work', label: 'Business & Content', icon: '▤' }, { id: 'safety', label: 'Execution Controls', icon: '✓' }, { id: 'connections', label: 'Skills & Accounts', icon: '◇' }] },
  { heading: 'WORKSPACE', items: [{ id: 'overview', label: 'Overview', icon: '◈' }, { id: 'chat', label: 'Chat', icon: '◌' }, { id: 'tasks', label: 'Tasks', icon: '▤' }, { id: 'approvals', label: 'Approvals', icon: '✓' }] },
  { heading: 'KNOWLEDGE', items: [{ id: 'memory', label: 'Memory', icon: '⌕' }, { id: 'tools', label: 'Tools', icon: '⌘' }, { id: 'capabilities', label: 'Skills & capabilities', icon: '◇' }] },
  { heading: 'OPERATIONS', items: [{ id: 'mcp', label: 'MCP servers', icon: '⌁' }, { id: 'workflows', label: 'Workflows', icon: '⇢' }, { id: 'schedules', label: 'Schedules', icon: '◷' }, { id: 'activity', label: 'Activity log', icon: '≋' }] },
  { heading: 'INTEGRATIONS', items: [{ id: 'browser', label: 'Browser', icon: '⌑' }, { id: 'computer', label: 'Computer control', icon: '▣' }, { id: 'voice', label: 'Voice assistant', icon: '♩' }] },
]

const pageInfo: Record<View, { title: string; subtitle: string }> = {
  studio: { title: 'Coding Studio', subtitle: 'Inspect bounded context from your selected project.' },
  overview: { title: 'Overview', subtitle: 'Your assistant workspace at a glance.' },
  chat: { title: 'Chat', subtitle: 'Talk with CAM or turn a request into a governed task.' },
  tasks: { title: 'Tasks', subtitle: 'Plan, follow, cancel, or resume your assistant’s work.' },
  approvals: { title: 'Approvals', subtitle: 'Review each requested action before it runs.' },
  memory: { title: 'Memory', subtitle: 'Save and search notes stored in your local database.' },
  tools: { title: 'Tools', subtitle: 'See the actions available to the planner and their risk levels.' },
  capabilities: { title: 'Skills & capabilities', subtitle: 'Browse the assistant’s registered skills and capabilities.' },
  mcp: { title: 'MCP servers', subtitle: 'Configure servers explicitly. Connections stay disabled until approved.' },
  workflows: { title: 'Workflows', subtitle: 'Track workflow state; this screen does not execute external work.' },
  schedules: { title: 'Schedules', subtitle: 'Manage schedule records. Background task execution is not enabled.' },
  activity: { title: 'Activity log', subtitle: 'Review recent tool events and outcomes.' },
  settings: { title: 'System settings', subtitle: 'Check provider availability and local feature status.' },
  browser: { title: 'Browser', subtitle: 'Browser automation availability and security status.' },
  computer: { title: 'Computer control', subtitle: 'Windows desktop automation availability.' },
  voice: { title: 'Voice assistant', subtitle: 'Voice input and output availability.' },
  agents: { title: 'Agent Workspace', subtitle: 'Project roles, dependencies, proposals and execution evidence.' },
  library: { title: 'Knowledge & Creation', subtitle: 'Bounded source snapshots and versioned text artifacts.' },
  decisions: { title: 'Decisions & Reminders', subtitle: 'Answer scoped questions and manage durable reminders.' },
  work: { title: 'Business & Content', subtitle: 'Leads, orders, follow-ups and content drafts.' },
  safety: { title: 'Execution Controls', subtitle: 'Stop execution and inspect current boundaries.' },
  connections: { title: 'Skills & Accounts', subtitle: 'Project skill manifests and Personal account connections.' },
}

const statusLabel = (value: unknown) => String(value ?? 'unknown').replaceAll('_', ' ')
export default function App() {
  const [view, setView] = useState<View>('overview')
  const [navOpen, setNavOpen] = useState(false)
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    try { return localStorage.getItem('jarvis-theme') === 'light' ? 'light' : 'dark' }
    catch { return 'dark' }
  })
  const [messages, setMessages] = useState<Message[]>(initialMessages)
  const [draft, setDraft] = useState('')
  const [responseVisible, setResponseVisible] = useState(false)
  const panelRef = useRef<HTMLElement>(null)
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [pairingCode, setPairingCode] = useState('')
  const [pairingBusy, setPairingBusy] = useState(false)
  const progressCursor = useRef(0)
  const authenticated = hasSession()
  const [projects, setProjects] = useState<RegisteredProject[]>([])
  const [projectId, setProjectId] = useState<string | null>(null)
  const projectRef = useRef<string | null>(null)
  const [aiStatus, setAiStatus] = useState<JsonRecord | null>(null)
  const [lastChatProvider, setLastChatProvider] = useState<string | null>(null)
  const [connectionError, setConnectionError] = useState<string | null>(null)
  const [pageError, setPageError] = useState<string | null>(null)
  const [isSending, setIsSending] = useState(false)
  const [activity, setActivity] = useState<ActivityItem[]>([])
  const [approvals, setApprovals] = useState<ApprovalItem[]>([])
  const [approvedExport, setApprovedExport] = useState<{ scope: string | null; epoch: number; output: unknown } | null>(null)
  const [tasks, setTasks] = useState<TaskStatus[]>([])
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const selectedTaskRef = useRef<string | null>(null)
  const [taskSteps, setTaskSteps] = useState<TaskStep[]>([])
  const [approvalTasks, setApprovalTasks] = useState<Record<string, string>>({})
  const [memories, setMemories] = useState<MemoryItem[]>([])
  const [memoryDraft, setMemoryDraft] = useState('')
  const [memoryQuery, setMemoryQuery] = useState('')
  const [memoryEdit, setMemoryEdit] = useState<Record<number, string>>({})
  const [tools, setTools] = useState<JsonRecord[]>([])
  const [capabilities, setCapabilities] = useState<JsonRecord[]>([])
  const [skills, setSkills] = useState<JsonRecord[]>([])
  const [mcpServers, setMcpServers] = useState<JsonRecord[]>([])
  const [workflows, setWorkflows] = useState<JsonRecord[]>([])
  const [schedules, setSchedules] = useState<JsonRecord[]>([])
  const [taskDraft, setTaskDraft] = useState('')
  const [mcpDraft, setMcpDraft] = useState({ server_id: '', name: '', endpoint: '', secret_reference: '' })
  const [workflowDraft, setWorkflowDraft] = useState('')
  const refreshGeneration = useRef(0)
  const refreshController = useRef<AbortController | null>(null)
  const [scheduleDraft, setScheduleDraft] = useState({ task_id: '', schedule_type: 'one_time', next_run: '', retry_limit: '0' })

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('jarvis-theme', theme) } catch { /* Theme still works for this session. */ }
  }, [theme])

  const refreshWorkspace = useCallback(async (cancelSignal?: AbortSignal, background = false) => {
    const generation = ++refreshGeneration.current
    try {
      await initializeDesktopSession()
    } catch {
      if (!cancelSignal?.aborted && generation === refreshGeneration.current) {
        setConnectionError('Desktop backend startup failed. Restart the desktop application after checking its configuration.')
      }
      return
    }
    if (cancelSignal?.aborted || generation !== refreshGeneration.current) return
    refreshController.current?.abort()
    const requestController = new AbortController()
    refreshController.current = requestController
    const deadline = AbortSignal.timeout(8000)
    const signal = AbortSignal.any([requestController.signal, deadline, ...(cancelSignal ? [cancelSignal] : [])])
    const [healthResult, aiResult, activityResult, approvalsResult, tasksResult, memoriesResult, toolsResult, capabilitiesResult, skillsResult, mcpResult, workflowsResult, schedulesResult, projectResult] = await Promise.all([
      getHealth(signal).catch(() => null), getAiStatus(signal).catch(() => null), getActivity(signal, projectId).catch(() => []),
      getApprovals(signal, projectId).catch(() => []), getTasks(signal, projectId).catch(() => []), getMemories('', signal, projectId).catch(() => []),
      getTools(signal, projectId).catch(() => []), getCapabilities(signal, projectId).catch(() => []), getSkills(signal, projectId).catch(() => []),
      getMcpServers(signal).catch(() => []), getWorkflows(signal).catch(() => []), getSchedules(signal).catch(() => []),
      getProjects(signal).catch(() => []),
    ])
    if (cancelSignal?.aborted || generation !== refreshGeneration.current) return
    if (!healthResult) {
      setHealth(null)
      setAiStatus(null)
      setConnectionError(`Backend unavailable at ${API_BASE_URL}. Start the CAM API on port 8000 and open the dashboard on port 5173, then retry.`)
      return
    }
    const waiting = tasksResult.filter((item) => item.status === 'waiting_for_approval')
    const stepSets = await Promise.all(waiting.map(async (item) => [item.task_id, await getTaskSteps(item.task_id, signal, projectId).catch(() => [])] as const))
    const links: Record<string, string> = {}
    for (const [taskId, steps] of stepSets) for (const step of steps) if (step.approval_id && step.status === 'approval_required') links[step.approval_id] = taskId
    if (cancelSignal?.aborted || generation !== refreshGeneration.current) return
    const previousSelection = selectedTaskRef.current
    const selected = tasksResult.find((item) => item.task_id === selectedTaskRef.current) ?? tasksResult[0]
    const steps = selected ? await getTaskSteps(selected.task_id, signal, projectId).catch(() => []) : []
    if (cancelSignal?.aborted || generation !== refreshGeneration.current) return
    // Publish approvals only after their task links are ready, so a fast click
    // cannot mistakenly continue a task-owned tool outside the orchestrator.
    setHealth(healthResult); setAiStatus(aiResult); setActivity(activityResult); setApprovals(approvalsResult)
    setTasks(tasksResult); if (!background) setMemories(memoriesResult); setTools(toolsResult); setCapabilities(capabilitiesResult)
    setSkills(skillsResult); setMcpServers(mcpResult); setWorkflows(workflowsResult); setSchedules(schedulesResult)
    setProjects(projectResult)
    setApprovalTasks(links)
    if (selectedTaskRef.current === previousSelection) {
      selectedTaskRef.current = selected?.task_id ?? null
      setSelectedTaskId(selected?.task_id ?? null)
      setTaskSteps(steps)
    }
    setConnectionError(null)
    return true
  }, [projectId])

  useEffect(() => {
    if (!authenticated) return
    const controller = new AbortController()
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    let refreshTimer: ReturnType<typeof setTimeout> | undefined
    const connect = async () => {
      try {
        await readProgressEvents(progressCursor.current, (event) => {
          progressCursor.current = event.id
          if (!refreshTimer) refreshTimer = setTimeout(() => { refreshTimer = undefined; void refreshWorkspace(undefined, true) }, 300)
        }, controller.signal, projectId)
      } catch { if (!controller.signal.aborted) void refreshWorkspace(undefined, true) }
      if (!controller.signal.aborted && hasSession()) retryTimer = setTimeout(() => { void connect() }, 2000)
    }
    void connect()
    return () => { controller.abort(); clearTimeout(retryTimer); clearTimeout(refreshTimer) }
  }, [authenticated, refreshWorkspace, projectId])

  useEffect(() => {
    const controller = new AbortController()
    let timer: number
    let first = true
    const poll = async () => {
      if (document.visibilityState !== 'hidden') {
        if (await refreshWorkspace(controller.signal, !first)) first = false
      }
      // Schedule after completion so slow requests cannot accumulate.
      if (!controller.signal.aborted) timer = window.setTimeout(() => { void poll() }, 5000)
    }
    timer = window.setTimeout(() => { void poll() }, 0)
    return () => { controller.abort(); refreshController.current?.abort(); window.clearTimeout(timer) }
  }, [refreshWorkspace])

  useEffect(() => {
    if (view === 'overview') return
    panelRef.current?.focus()
    const onEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      setView('overview')
      document.querySelector<HTMLButtonElement>('.brand')?.focus()
    }
    // A submit button loses focus while disabled; Escape must also work when
    // focus has returned to the document body after an HTTP response.
    document.addEventListener('keydown', onEscape)
    return () => document.removeEventListener('keydown', onEscape)
  }, [view])

  async function selectTask(taskId: string) {
    selectedTaskRef.current = taskId
    setSelectedTaskId(taskId)
    const scope = projectId
    try { const steps = await getTaskSteps(taskId, undefined, scope); if (projectRef.current === scope && selectedTaskRef.current === taskId) setTaskSteps(steps) }
    catch (error) { if (projectRef.current === scope && selectedTaskRef.current === taskId) setPageError(error instanceof Error ? error.message : 'Task details could not be loaded.') }
  }

  function switchProject(next: string) {
    const scope = next || null
    projectRef.current = scope
    ++refreshGeneration.current
    refreshController.current?.abort()
    progressCursor.current = 0
    selectedTaskRef.current = null
    setProjectId(scope); setSelectedTaskId(null); setTaskSteps([]); setTasks([])
    setApprovals([]); setApprovalTasks({}); setMemories([]); setMemoryEdit({})
    setActivity([]); setTools([]); setCapabilities([]); setSkills([])
    setMessages(initialMessages); setDraft(''); setTaskDraft(''); setMemoryDraft(''); setMemoryQuery('')
    setResponseVisible(false); setPageError(null)
  }

  async function decide(item: ApprovalItem, approved: boolean) {
    const scope = projectId
    const epoch = getSessionEpoch()
    setPageError(null); setIsSending(true)
    try {
      await decideApproval(item.request_id, approved, projectId)
      if (projectRef.current !== scope || getSessionEpoch() !== epoch) return
      const taskId = approvalTasks[item.request_id]
      if (taskId) await resumeTask(taskId, projectId)
      else if (approved) {
        const result = await continueTool(item.request_id, scope)
        if (projectRef.current !== scope || getSessionEpoch() !== epoch) return
        if (result?.status !== 'success') throw new Error(result?.detail ?? 'Continuation was not verified.')
        if ((result.tool_name ?? result.tool) === 'library_export') setApprovedExport({ scope, epoch, output: result })
        if (!item.preview.agent_job_id && (result.tool_name ?? result.tool) === 'studio_project_chat' && typeof result.result?.message === 'string') {
          setMessages((current) => [...current, { role: 'assistant', text: String(result.result!.message) }])
          setLastChatProvider(String(result.result.provider)); setResponseVisible(true); setView('chat')
        }
      }
      await refreshWorkspace()
    } catch (error) { setPageError(error instanceof Error ? error.message : 'Approval could not be completed.') }
    finally { setIsSending(false) }
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const message = draft.trim()
    if (!message || isSending) return
    const scope = projectId
    const epoch = getSessionEpoch()
    setResponseVisible(false)
    setDraft(''); setPageError(null); setMessages((current) => [...current, { role: 'user', text: message }]); setIsSending(true)
    try {
      const result = await sendChat(message, undefined, scope)
      if (projectRef.current !== scope || getSessionEpoch() !== epoch) return
      setMessages((current) => [...current, { role: 'assistant', text: result.message }])
      if (result.approval) { await refreshWorkspace(); setView('approvals'); return }
      setLastChatProvider(result.provider)
      setResponseVisible(true)
      setHealth((current) => current ?? { status: 'ok', provider: result.provider, model: result.model, provider_configured: true })
      void getActivity(undefined, projectId).then((items) => { if (projectRef.current === projectId) setActivity(items) }).catch(() => undefined)
      setConnectionError(null)
    } catch (error) { if (projectRef.current === scope && getSessionEpoch() === epoch) setPageError(error instanceof Error ? error.message : 'Unable to reach CAM.') }
    finally { setIsSending(false) }
  }

  async function onTask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const request = taskDraft.trim()
    if (!request || isSending) return
    setIsSending(true); setPageError(null)
    try { const created = await createTask(request, projectId); setTaskDraft(''); await refreshWorkspace(); setView('tasks'); await selectTask(created.task_id) }
    catch (error) { setPageError(error instanceof Error ? error.message : 'Task could not be created.') }
    finally { setIsSending(false) }
  }

  async function stopTask(taskId: string) {
    setIsSending(true)
    try { const result = await cancelTask(taskId, projectId); await refreshWorkspace(); await selectTask(result.task_id) }
    catch (error) { setPageError(error instanceof Error ? error.message : 'Task could not be cancelled.') }
    finally { setIsSending(false) }
  }

  async function resumeSelectedTask(taskId: string) {
    setIsSending(true)
    try { await resumeTask(taskId, projectId); await refreshWorkspace(); await selectTask(taskId) }
    catch (error) { setPageError(error instanceof Error ? error.message : 'Task could not be resumed.') }
    finally { setIsSending(false) }
  }

  async function saveMemory(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const content = memoryDraft.trim(); if (!content) return
    setIsSending(true)
    try { await createMemory(content, projectId); setMemoryDraft(''); setMemories(await getMemories(memoryQuery, undefined, projectId)) }
    catch (error) { setPageError(error instanceof Error ? error.message : 'Memory could not be saved.') }
    finally { setIsSending(false) }
  }

  async function searchMemory(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setIsSending(true)
    try { setMemories(await searchMemories(memoryQuery, projectId)) }
    catch (error) { setPageError(error instanceof Error ? error.message : 'Memory search failed.') }
    finally { setIsSending(false) }
  }

  async function saveMemoryEdit(item: MemoryItem) {
    setIsSending(true)
    try { await updateMemory(item.id, memoryEdit[item.id] ?? item.content, projectId); setMemoryEdit((current) => { const next = { ...current }; delete next[item.id]; return next }); setMemories(await searchMemories(memoryQuery, projectId)) }
    catch (error) { setPageError(error instanceof Error ? error.message : 'Memory could not be updated.') }
    finally { setIsSending(false) }
  }

  async function removeMemory(id: number) {
    setIsSending(true)
    try { await deleteMemory(id, projectId); setMemories(await searchMemories(memoryQuery, projectId)) }
    catch (error) { setPageError(error instanceof Error ? error.message : 'Memory could not be deleted.') }
    finally { setIsSending(false) }
  }

  async function configureMcp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setPageError(null)
    try {
      await createMcpServer({ ...mcpDraft, secret_reference: mcpDraft.secret_reference.trim() || null })
      setMcpDraft({ server_id: '', name: '', endpoint: '', secret_reference: '' }); setMcpServers(await getMcpServers()); setActivity(await getActivity())
    } catch (error) { setPageError(error instanceof Error ? error.message : 'MCP server could not be configured.') }
  }

  async function mcpAction(action: 'approve' | 'enable' | 'disable' | 'delete', serverId: string) {
    setPageError(null)
    try {
      if (action === 'approve') await approveMcpServer(serverId)
      if (action === 'enable') {
        const server = mcpServers.find((item) => item.server_id === serverId)
        if (!server?.approved || !window.confirm(`Connect to the approved MCP server "${String(server.name)}" now? This will contact its configured HTTPS endpoint.`)) return
        await enableMcpServer(serverId)
      }
      if (action === 'disable') await disableMcpServer(serverId)
      if (action === 'delete') await deleteMcpServer(serverId)
      setMcpServers(await getMcpServers()); setActivity(await getActivity())
    } catch (error) { setPageError(error instanceof Error ? error.message : 'MCP operation failed safely.') }
  }

  async function addWorkflow(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const kind = workflowDraft.trim(); if (!kind) return
    try { await createWorkflow(kind, crypto.randomUUID()); setWorkflowDraft(''); setWorkflows(await getWorkflows()) }
    catch (error) { setPageError(error instanceof Error ? error.message : 'Workflow could not be created.') }
  }

  async function setWorkflowState(id: string, status: string) {
    try { await transitionWorkflow(id, status); setWorkflows(await getWorkflows()) }
    catch (error) { setPageError(error instanceof Error ? error.message : 'Workflow state could not be updated.') }
  }

  async function addSchedule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!scheduleDraft.task_id.trim() || !scheduleDraft.next_run) return
    try {
      await createSchedule({ task_id: scheduleDraft.task_id.trim(), schedule_type: scheduleDraft.schedule_type, next_run: new Date(scheduleDraft.next_run).toISOString(), retry_limit: Number(scheduleDraft.retry_limit) })
      setScheduleDraft({ task_id: '', schedule_type: 'one_time', next_run: '', retry_limit: '0' }); setSchedules(await getSchedules())
    } catch (error) { setPageError(error instanceof Error ? error.message : 'Schedule could not be created.') }
  }

  async function stopSchedule(id: string) {
    try { await cancelSchedule(id); setSchedules(await getSchedules()) }
    catch (error) { setPageError(error instanceof Error ? error.message : 'Schedule could not be cancelled.') }
  }

  const online = health?.status === 'ok' && !connectionError
  const orbState = deriveOrbState({ online: Boolean(online), listening: false, busy: isSending || tasks.some(item => item.status === 'running'), responseVisible })
  const currentTask = tasks.find((item) => item.task_id === selectedTaskId) ?? null
  const aiMode = lastChatProvider === 'deterministic_fallback' ? 'Deterministic fallback' : lastChatProvider === 'mock' ? 'Mock (local)' : lastChatProvider ? `Live response (${lastChatProvider})` : health?.provider === 'mock' ? 'Mock (local)' : health?.provider_configured ? 'Live configured · no response yet' : health?.provider ? 'Live unavailable' : 'Unknown'
  const title = pageInfo[view]
  const activeCount = tasks.filter((item) => activeStatuses.has(item.status)).length
  const navCounts = useMemo(() => ({ approvals: approvals.length, tasks: activeCount }), [approvals.length, activeCount])

  function navButton(item: NavItem) {
    const count = item.id === 'approvals' ? navCounts.approvals : item.id === 'tasks' ? navCounts.tasks : 0
    const unavailable = ['browser', 'computer', 'voice'].includes(item.id)
    return <button className={`nav-item ${view === item.id ? 'selected' : ''}`} key={item.id} onClick={() => { setView(item.id); setNavOpen(false); setPageError(null) }} aria-label={item.label} aria-current={view === item.id ? 'page' : undefined}><Icon name={item.id} /><span className="dock-label">{item.label}{unavailable ? ' · unavailable' : ''}</span>{count > 0 && <b className="nav-count">{count}</b>}</button>
  }

  function renderTaskDetails() {
    if (!currentTask) return <EmptyState title="No task selected" text="Choose a task from the list or create a new one above." />
    return <section className="surface task-detail"><div className="surface-heading"><div><span className="eyebrow">TASK DETAIL</span><h3>{statusLabel(currentTask.status)}</h3></div><span className={`status-pill ${currentTask.status === 'completed' ? 'good' : currentTask.status === 'failed' ? 'bad' : ''}`}>{statusLabel(currentTask.status)}</span></div><p className="task-request">{currentTask.request}</p>{currentTask.error && <p className="inline-error">{currentTask.error}</p>}<div className="step-list">{taskSteps.length ? taskSteps.map((step) => <article className="step-row" key={step.step_number}><span className="step-number">{String(step.step_number).padStart(2, '0')}</span><div><strong>{step.tool_name}</strong><p>{step.description}</p>{typeof step.result?.detail === 'string' && <small>{step.result.detail}</small>}{step.result?.result !== undefined && <pre className="result-preview">{JSON.stringify(step.result.result, null, 2)}</pre>}</div><span className="status-pill">{statusLabel(step.status)}</span></article>) : <p className="muted">No planned steps have been saved for this task.</p>}</div><div className="button-row">{activeStatuses.has(currentTask.status) && <button className="button subtle danger" onClick={() => void stopTask(currentTask.task_id)}>Cancel task</button>}{currentTask.status === 'waiting_for_capability' && <button className="button primary" onClick={() => void resumeSelectedTask(currentTask.task_id)}>Resume task</button>}</div></section>
  }

  function renderView() {
    if (view === 'studio') return <CodingStudio key={projectId ?? 'personal'} projectId={projectId} authenticated={authenticated} />
    if (view === 'voice') return <VoicePanel />
    if (view === 'computer') return <OwnedComputerPanel key={`${projectId ?? 'personal'}:${getSessionEpoch()}`} projectId={projectId} authenticated={authenticated} />
    if (view === 'connections') return <ConnectionsPanel key={projectId ?? 'personal'} projectId={projectId} authenticated={authenticated} />
    if (view === 'agents' || view === 'library' || view === 'decisions' || view === 'work' || view === 'safety') return <WorkspacePanel key={`${view}:${projectId ?? 'personal'}`} section={view} projectId={projectId} authenticated={authenticated} />
    if (view === 'chat') return <div className="chat-home">
      <section className="chat-workspace"><div className="chat-panel-heading"><div className="chat-title-mark"><Icon name="chat" /></div><div><strong>Chat with CAM</strong><small>{aiMode}</small></div><span className={`status-pill ${online ? 'good' : 'bad'}`}>{online ? 'Online' : 'Offline'}</span></div><div className="chat-thread" aria-live="polite">{messages.map((item, index) => <article className={`chat-message ${item.role}`} key={`${item.role}-${index}`}><div className="message-avatar">{item.role === 'user' ? 'U' : 'C'}</div><div className="message-body"><div className="message-meta"><strong>{item.role === 'user' ? 'You' : 'CAM'}</strong><small>{item.role === 'user' ? 'You' : aiMode}</small></div><p>{item.text}</p></div></article>)}{isSending && <article className="chat-message assistant"><div className="message-avatar">C</div><div className="message-body"><div className="message-meta"><strong>CAM</strong><small>Thinking</small></div><p className="typing">Working on your response<span> ···</span></p></div></article>}</div><form className="chat-composer" onSubmit={onSubmit}><label className="sr-only" htmlFor="message">Message CAM</label><textarea id="message" value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit() } }} placeholder="Message CAM…" maxLength={4000} disabled={isSending} rows={2} /><div className="composer-footer"><span>Enter to send · Shift + Enter for a new line</span><button className="button primary send-button" type="submit" aria-label={isSending ? 'Sending message' : 'Send'} disabled={isSending || !draft.trim()}>{isSending ? 'Sending…' : 'Send'} <span>↑</span></button></div></form><div className="chat-bottom-note">CAM can make mistakes. Review tool actions before approving them.</div></section>
    </div>

    if (['browser', 'computer', 'voice'].includes(view)) return <UnavailableFeature view={view} onSettings={() => setView('settings')} />

    if (view === 'tasks') return <div className="page-stack"><section className="surface task-create"><SurfaceHeading eyebrow="NEW TASK" title="What should CAM do?" /><form className="inline-form" onSubmit={(event) => void onTask(event)}><input aria-label="Task request" value={taskDraft} onChange={(event) => setTaskDraft(event.target.value)} placeholder="Describe a task for CAM…" maxLength={4000} /><button className="button primary" disabled={!taskDraft.trim() || isSending}>Create task <span>↗</span></button></form><p className="helper-text">The planner proposes steps. Tools still pass through validation, permissions, approval, and audit.</p></section><div className="task-layout"><section className="surface"><SurfaceHeading eyebrow="TASK HISTORY" title={`${tasks.length} task${tasks.length === 1 ? '' : 's'}`} /><div className="task-list">{tasks.map((item) => <button className={`task-list-item ${selectedTaskId === item.task_id ? 'selected' : ''}`} key={item.task_id} onClick={() => void selectTask(item.task_id)}><span className="task-list-icon"><Icon name="tasks" /></span><span className="row-main"><strong>{item.request}</strong><small>{new Date(item.updated_at).toLocaleString()}</small></span><span className="status-pill">{statusLabel(item.status)}</span></button>)}{tasks.length === 0 && <EmptyState title="No tasks yet" text="Create a task above to see its plan and progress here." />}</div></section>{renderTaskDetails()}</div></div>

    if (view === 'approvals') return <div className="page-stack"><section className="notice-card approval-notice"><span className="notice-icon"><Icon name="approvals" /></span><p><strong>Nothing runs until you approve it.</strong><br />Review the action, target, and risk before choosing approve or reject.</p></section>{approvals.length ? <div className="approval-grid">{approvals.map((item) => <section className="surface approval-card" key={item.request_id}><div className="surface-heading"><div><span className="eyebrow">ACTION REQUEST</span><h3>{item.tool_name}</h3></div><span className={`risk-tag risk-${item.risk.toLowerCase()}`}>{item.risk} risk</span></div><dl className="detail-list">{Object.entries(item.preview).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{typeof value === 'string' ? value : JSON.stringify(value)}</dd></div>)}</dl><p className="helper-text">One-time approval · Expires {new Date(item.expires_at).toLocaleString()}</p><div className="button-row"><button className="button primary" onClick={() => void decide(item, true)}>Approve once</button><button className="button subtle danger" onClick={() => void decide(item, false)}>Reject</button></div></section>)}</div> : <EmptyState title="You’re all caught up" text="If a task needs approval, it will appear here before the action runs." />}</div>

    if (view === 'memory') return <div className="page-stack"><section className="surface"><SurfaceHeading eyebrow="LOCAL KNOWLEDGE" title="Save a memory" /><form className="inline-form" onSubmit={(event) => void saveMemory(event)}><input aria-label="Memory to save" value={memoryDraft} onChange={(event) => setMemoryDraft(event.target.value)} placeholder="Write a note to remember…" maxLength={4000} /><button className="button primary" aria-label="Save" disabled={!memoryDraft.trim()}>Save note <span>＋</span></button></form><p className="helper-text">Memory is stored in your local SQLite database. Credential-like text is rejected.</p></section><section className="surface"><SurfaceHeading eyebrow="YOUR NOTES" title={`${memories.length} saved ${memories.length === 1 ? 'note' : 'notes'}`} /><form className="search-form" onSubmit={(event) => void searchMemory(event)}><span>⌕</span><input aria-label="Search memories" value={memoryQuery} onChange={(event) => setMemoryQuery(event.target.value)} placeholder="Search your notes…" /><button className="button subtle">Search</button></form><div className="memory-list">{memories.map((item) => <article className="memory-row" key={item.id}><div className="memory-mark">✳</div><div className="memory-content">{memoryEdit[item.id] !== undefined ? <textarea aria-label={`Edit memory ${item.id}`} value={memoryEdit[item.id]} onChange={(event) => setMemoryEdit((current) => ({ ...current, [item.id]: event.target.value }))} /> : <p>{item.content}</p>}<small>{item.memory_type} · Updated {new Date(item.updated_at).toLocaleString()}</small></div><div className="memory-actions">{memoryEdit[item.id] !== undefined ? <button className="text-button" onClick={() => void saveMemoryEdit(item)}>Save</button> : <button className="text-button" onClick={() => setMemoryEdit((current) => ({ ...current, [item.id]: item.content }))}>Edit</button>}<button className="text-button danger-text" onClick={() => void removeMemory(item.id)}>Delete</button></div></article>)}{memories.length === 0 && <EmptyState title="No matching notes" text="Save a note above, or try a different search." />}</div></section></div>

    if (view === 'tools') return <div className="page-stack"><section className="notice-card"><span className="notice-icon"><Icon name="tools" /></span><p><strong>Tools run through CAM policy.</strong><br />Use the task planner to request an action. The registry validates arguments and applies permission and approval rules before execution.</p></section><section className="surface"><SurfaceHeading eyebrow="TOOL REGISTRY" title={`${tools.length} registered tools`} /><div className="data-grid">{tools.map((item) => <article className="data-card" key={String(item.name)}><div className="data-card-top"><span className="tool-glyph"><Icon name="tools" /></span><span className={`status-pill ${item.enabled ? 'good' : ''}`}>{item.enabled ? 'Available' : 'Disabled'}</span></div><h3>{String(item.name)}</h3><p>{String(item.description)}</p><div className="card-footer"><span className="risk-tag">{statusLabel(item.risk)} risk</span><small>Execution via planner</small></div></article>)}</div></section></div>

    if (view === 'capabilities') return <div className="page-stack"><section className="surface"><SurfaceHeading eyebrow="REGISTERED SKILLS" title={`${skills.length} skills`} /><div className="data-grid">{skills.map((item) => <article className="data-card" key={String(item.skill_id)}><div className="data-card-top"><span className="tool-glyph"><Icon name="capabilities" /></span><span className="status-pill good">{statusLabel(item.status)}</span></div><h3>{String(item.name)}</h3><p>{String(item.description)}</p><small>Allowed tools: {Array.isArray(item.allowed_tools) && item.allowed_tools.length ? item.allowed_tools.join(', ') : 'None'}</small></article>)}</div>{skills.length === 0 && <p className="muted">No skills are registered yet. Built-in capabilities are listed below.</p>}</section><section className="surface"><SurfaceHeading eyebrow="CAPABILITY REGISTRY" title={`${capabilities.length} capabilities`} /><div className="capability-list">{capabilities.map((item) => <article className="capability-row" key={String(item.capability_id)}><span className="capability-mark"><Icon name="capabilities" /></span><div className="row-main"><strong>{String(item.name)}</strong><small>{String(item.description)}</small></div><span className="tag">{statusLabel(item.source_type)}</span><span className={`status-pill ${item.enabled ? 'good' : ''}`}>{statusLabel(item.status)}</span></article>)}</div></section></div>

    if (view === 'mcp') return <div className="page-stack"><section className="notice-card"><span className="notice-icon"><Icon name="mcp" /></span><p><strong>MCP servers start disabled.</strong><br />Configuring a server does not connect it. Explicit approval is required, and enabling it contacts the configured HTTPS endpoint.</p></section><section className="surface"><SurfaceHeading eyebrow="ADD SERVER" title="Configure an MCP server" /><form className="form-grid" onSubmit={(event) => void configureMcp(event)}><label>Server ID<input required pattern="[a-z][a-z0-9_-]{0,99}" value={mcpDraft.server_id} onChange={(event) => setMcpDraft({ ...mcpDraft, server_id: event.target.value })} placeholder="my-server" /></label><label>Display name<input required maxLength={100} value={mcpDraft.name} onChange={(event) => setMcpDraft({ ...mcpDraft, name: event.target.value })} placeholder="My MCP server" /></label><label className="span-two">HTTPS endpoint<input required type="url" value={mcpDraft.endpoint} onChange={(event) => setMcpDraft({ ...mcpDraft, endpoint: event.target.value })} placeholder="https://server.example/mcp" /></label><label className="span-two">Credential reference (optional)<input value={mcpDraft.secret_reference} onChange={(event) => setMcpDraft({ ...mcpDraft, secret_reference: event.target.value })} placeholder="env:MY_MCP_TOKEN" /><small>Reference only—never enter a credential value here.</small></label><div className="span-two"><button className="button primary">Save disabled server</button></div></form></section><section className="surface"><SurfaceHeading eyebrow="CONFIGURED SERVERS" title={`${mcpServers.length} server${mcpServers.length === 1 ? '' : 's'}`} /><div className="server-list">{mcpServers.map((server) => <article className="server-row" key={String(server.server_id)}><div className="server-avatar">⌁</div><div className="row-main"><strong>{String(server.name)}</strong><small>{String(server.endpoint)}</small><small>{server.credential_reference_configured ? 'Credential reference configured' : 'No credential reference'} · {server.approved ? 'Approved' : 'Not approved'}</small></div><span className={`status-pill ${server.enabled ? 'good' : ''}`}>{statusLabel(server.status)}</span><div className="button-row compact">{server.approved !== true && <button className="button subtle" onClick={() => void mcpAction('approve', String(server.server_id))}>Approve</button>}{server.approved === true && server.enabled !== true && <button className="button subtle" onClick={() => void mcpAction('enable', String(server.server_id))}>Enable…</button>}{server.enabled === true && <button className="button subtle" onClick={() => void mcpAction('disable', String(server.server_id))}>Disable</button>}{server.enabled !== true && <button className="text-button danger-text" onClick={() => void mcpAction('delete', String(server.server_id))}>Remove</button>}</div></article>)}{mcpServers.length === 0 && <EmptyState title="No servers configured" text="Add a server above. It will remain disabled until you approve and enable it." />}</div></section></div>

    if (view === 'workflows') return <div className="page-stack"><section className="notice-card"><span className="notice-icon"><Icon name="workflows" /></span><p><strong>Workflow tracking only.</strong><br />These records track status and idempotency. Creating or transitioning a record does not execute external actions.</p></section><section className="surface"><SurfaceHeading eyebrow="NEW WORKFLOW" title="Create a workflow record" /><form className="inline-form" onSubmit={(event) => void addWorkflow(event)}><input aria-label="Workflow type" required maxLength={50} value={workflowDraft} onChange={(event) => setWorkflowDraft(event.target.value)} placeholder="Workflow type, e.g. research" /><button className="button primary">Create workflow <span>＋</span></button></form></section><section className="surface"><SurfaceHeading eyebrow="WORKFLOW HISTORY" title={`${workflows.length} workflows`} /><div className="workflow-list">{workflows.map((item) => <article className="workflow-row" key={String(item.id)}><div className="row-main"><strong>{String(item.kind)}</strong><small>{String(item.id)} · Updated {new Date(String(item.updated_at)).toLocaleString()}</small>{Boolean(item.failure) && <small className="danger-text">{String(item.failure)}</small>}</div><span className="status-pill">{statusLabel(item.status)}</span><select aria-label={`Set workflow ${String(item.kind)} status`} value="" disabled={!workflowTransitions[String(item.status)]?.length} onChange={(event) => { if (event.target.value) void setWorkflowState(String(item.id), event.target.value) }}><option value="">Change state…</option>{(workflowTransitions[String(item.status)] ?? []).map((state) => <option value={state} key={state}>{statusLabel(state)}</option>)}</select></article>)}</div>{workflows.length === 0 && <EmptyState title="No workflow records" text="Create one above to track a bounded workflow state." />}</section></div>

    if (view === 'schedules') return <div className="page-stack"><section className="notice-card"><span className="notice-icon"><Icon name="schedules" /></span><p><strong>Schedules are records only in this build.</strong><br />No background worker runs scheduled tasks. Creating a schedule will not execute the task automatically.</p></section><section className="surface"><SurfaceHeading eyebrow="NEW SCHEDULE" title="Add a schedule record" /><form className="form-grid" onSubmit={(event) => void addSchedule(event)}><label className="span-two">Task ID<input required maxLength={100} value={scheduleDraft.task_id} onChange={(event) => setScheduleDraft({ ...scheduleDraft, task_id: event.target.value })} placeholder="Paste an existing task ID" /></label><label>Schedule type<select value={scheduleDraft.schedule_type} onChange={(event) => setScheduleDraft({ ...scheduleDraft, schedule_type: event.target.value })}><option value="one_time">One time</option><option value="interval">Interval</option><option value="daily">Daily</option><option value="weekly">Weekly</option></select></label><label>Next run<input required type="datetime-local" value={scheduleDraft.next_run} onChange={(event) => setScheduleDraft({ ...scheduleDraft, next_run: event.target.value })} /></label><label>Retry limit<input type="number" min="0" max="3" value={scheduleDraft.retry_limit} onChange={(event) => setScheduleDraft({ ...scheduleDraft, retry_limit: event.target.value })} /></label><div className="form-action"><button className="button primary">Save schedule</button></div></form></section><section className="surface"><SurfaceHeading eyebrow="SCHEDULES" title={`${schedules.length} schedule records`} /><div className="workflow-list">{schedules.map((item) => <article className="workflow-row" key={String(item.id)}><div className="row-main"><strong>{String(item.schedule_type)} task</strong><small>{String(item.task_id)} · Next: {item.next_run ? new Date(String(item.next_run)).toLocaleString() : 'Not set'}</small><small>Retries allowed: {String(item.retry_limit)}</small></div><span className={`status-pill ${item.enabled ? 'good' : ''}`}>{item.enabled ? 'Enabled' : 'Cancelled'}</span>{Boolean(item.enabled) && <button className="button subtle danger" onClick={() => void stopSchedule(String(item.id))}>Cancel</button>}</article>)}</div>{schedules.length === 0 && <EmptyState title="No schedules" text="Schedule records will appear here." />}</section></div>

    if (view === 'activity') return <section className="surface"><SurfaceHeading eyebrow="AUDIT TRAIL" title="Recent activity" /><div className="activity-list">{activity.map((item, index) => <article className="activity-item" key={`${item.timestamp}-${item.tool_name}-${index}`}><span className={`activity-dot ${item.status === 'success' ? 'good' : ''}`} /><div className="row-main"><strong>{item.tool_name}</strong><p>{item.detail}</p><small>{new Date(item.timestamp).toLocaleString()}</small></div><span className={`status-pill ${item.status === 'success' ? 'good' : ''}`}>{statusLabel(item.status)}</span></article>)}{activity.length === 0 && <EmptyState title="No recent activity" text="Tool and workflow events will show up here." />}</div></section>

    return <div className="page-stack"><DesktopSessionControl onRenewed={() => { void refreshWorkspace() }} /><FoundationPanel authenticated={hasSession()} onProjectsChanged={setProjects} /><section className="surface"><SurfaceHeading eyebrow="CONNECTION" title="Local API status" /><div className="settings-status"><span className={`connection-indicator ${online ? 'online' : ''}`} /><div><strong>{online ? 'Connected to CAM API' : 'API unavailable'}</strong><p>{connectionError ?? 'API is responding on your configured local address.'}</p></div><button className="button subtle" onClick={() => void refreshWorkspace()}>Refresh status</button></div></section><section className="surface"><SurfaceHeading eyebrow="AI PROVIDERS" title="Provider status" /><div className="settings-grid"><SettingLine label="Chat provider" value={health?.provider ?? 'Unavailable'} detail={`${health?.model ?? 'No model'} · ${health?.provider_configured ? 'Configured' : 'Unavailable'}`} /><SettingLine label="Latest chat response" value={aiMode} detail="Status changes to live only after a live provider responds." /><SettingLine label="Planning provider" value={String(aiStatus?.provider ?? 'Unavailable')} detail={`${String(aiStatus?.model ?? 'No model')} · ${aiStatus?.available ? 'Available' : String(aiStatus?.availability_reason ?? 'Unavailable')}`} /></div><p className="helper-text">Use Desktop model providers above in the packaged app, then fully exit and reopen. Development API providers use backend configuration. Saved settings return only protected credential references.</p></section><section className="surface"><SurfaceHeading eyebrow="FEATURE AVAILABILITY" title="Integrations" /><div className="availability-grid"><AvailabilityCard title="Browser sessions" status="Unavailable" detail="Locked until Windows OS-level network isolation is implemented and verified." /><AvailabilityCard title="Computer control" status="Dependency required" detail="Needs a compatible installed Windows UI Automation dependency and local verification." /><AvailabilityCard title="MCP connections" status="Approval required" detail="Configured servers stay off until explicitly approved and enabled." /><AvailabilityCard title="Scheduled execution" status="Not active" detail="Schedule records are stored; no background worker runs them." /></div></section><section className="surface"><SurfaceHeading eyebrow="APPEARANCE" title="Choose your theme" /><ThemePicker theme={theme} onChange={setTheme} /></section><section className="surface"><SurfaceHeading eyebrow="LOCAL CONFIGURATION" title="Setup and help" /><p className="body-copy">Provider setup, environment references, Windows startup commands, and troubleshooting are documented in the repository README.</p><p className="helper-text">Current mode: {aiMode}. Cloud credentials are stored only when explicitly submitted to the protected Windows vault.</p></section></div>
  }

  const orbCopy = {
    idle: ['Standing by', 'Ready when you are.'],
    listening: ['Listening', 'Microphone session active.'],
    thinking: ['Processing', 'Working on your request.'],
    responding: ['Response received', 'Your answer is ready in the conversation.'],
    offline: ['Core offline', 'Connect the local API to begin.'],
  }[orbState]
  const closePanel = () => { setView('overview'); document.querySelector<HTMLButtonElement>('.brand')?.focus() }

  return <div className={`app-frame immersive ${view !== 'overview' ? 'panel-open' : ''} ${navOpen ? 'mobile-nav-open' : ''}`}>
    <a className="skip-link" href="#assistant-core">Skip to assistant</a>
    <aside className="sidebar" onKeyDown={event => { if (event.key === 'Escape') setNavOpen(false) }}><button className="mobile-menu" onClick={() => setNavOpen(false)}>Close menu</button><button className="brand" onClick={() => setView('overview')} aria-label="CAM overview"><Icon name="overview" /><span className="brand-name">CAM</span></button><nav className="primary-nav" aria-label="Main navigation"><div className="nav-group"><span className="nav-heading">WORKSPACE</span>{['overview', 'chat', 'studio', 'tasks', 'approvals', 'memory', 'library', 'decisions'].map(id => navigation.flatMap(group => group.items).find(item => item.id === id)!).map(navButton)}</div><details className="more-navigation"><summary>More tools</summary>{navigation.map(group => <div className="nav-group" key={group.heading}><span className="nav-heading">{group.heading}</span>{group.items.filter(item => !['overview', 'chat', 'studio', 'tasks', 'approvals', 'memory', 'library', 'decisions'].includes(item.id)).map(navButton)}</div>)}</details></nav><button className="nav-item" aria-label="System settings" onClick={() => { setView('settings'); setNavOpen(false) }}><Icon name="settings" /><span className="dock-label">AI & Settings</span></button><span className="operator-avatar" aria-label="Usman, local operator">U</span></aside>
    <main className="main-area">
      <header className="topbar"><button className="mobile-menu" aria-label="Navigation menu" aria-expanded={navOpen} onClick={() => setNavOpen(!navOpen)}>Menu</button><div className="identity"><span className="identity-word">CAM<span className="identity-dot">.</span></span><span className="identity-divider" /><span className="identity-caption">YOUR PERSONAL ASSISTANT<br /><small>LOCAL COMMAND ENVIRONMENT</small></span></div><div className="topbar-actions"><label className="project-selector">Workspace<select aria-label="Selected workspace" value={projectId ?? ""} disabled={!authenticated || isSending} onChange={(event) => switchProject(event.target.value)}><option value="">Personal</option>{projects.map((project) => <option key={project.project_id} value={project.project_id}>{project.name}</option>)}</select></label><span className="top-mode">{aiMode}</span><div className={`top-connection ${online ? 'online' : 'offline'}`}><span className="connection-indicator" />{online ? 'Connected' : 'Offline'}</div><div className="theme-control" aria-label="Appearance"><button aria-label="Use light theme" aria-pressed={theme === 'light'} onClick={() => setTheme('light')}>☼</button><button aria-label="Use dark theme" aria-pressed={theme === 'dark'} onClick={() => setTheme('dark')}>☾</button></div></div></header>
      {view === 'overview' && <section className="home-start" aria-label="Start here"><div><p className="eyebrow">YOUR WORKSPACE</p><h1>What would you like to do?</h1><p>Chat, work on a project, or connect your AI. Choose a place to start.</p></div><div className="home-shortcuts"><button onClick={() => setView('chat')}><strong>Chat with CAM</strong><span>Ask a question or start a conversation</span></button><button onClick={() => setView('studio')}><strong>Open Coding Studio</strong><span>Work with files in your project</span></button><button onClick={() => setView('settings')}><strong>Set up my AI</strong><span>Gemini, Claude, NVIDIA and local models</span></button></div></section>}
      <div className="environment-label"><span className="eyebrow">ASSISTANT CORE / 01</span><span>PRIVATE BY DESIGN</span></div>
      <div className="immersive-stage">
        <div className="ambient-floor" aria-hidden="true" />
        <aside className="floating-rail left-rail" aria-label="System and task summaries">
          <section className="float-panel"><div className="float-heading"><Icon name="activity" /><h2>System</h2><span className="micro-label">LOCAL</span></div><div className="telemetry-row"><span>Connection</span><strong className={online ? 'text-cyan' : ''}>{online ? 'Backend online' : 'Backend offline'}</strong></div><div className="telemetry-row"><span>Execution</span><strong>Approval gated</strong></div><div className="telemetry-row"><span>Voice</span><button className="inline-link" onClick={() => setView('voice')}>Unavailable <Icon name="arrow" /></button></div><div className="panel-footnote"><span className="connection-indicator online" />Local permissions enforced</div></section>
          <section className="float-panel"><div className="float-heading"><Icon name="tasks" /><h2>Active tasks</h2><span className="panel-count">{activeCount}</span></div>{tasks.filter(item => activeStatuses.has(item.status)).slice(0,3).map(item => <button className="compact-task" key={item.task_id} onClick={() => { void selectTask(item.task_id); setView('tasks') }}><span className="task-state-dot" /><span><strong>{item.request}</strong><small>{statusLabel(item.status)}</small></span><Icon name="arrow" /></button>)}{activeCount === 0 && <div className="quiet-empty"><span>No active tasks</span><small>Your next mission starts with a request.</small></div>}<button className="panel-link" onClick={() => setView('tasks')}>Open task workspace <Icon name="arrow" /></button></section>
          <section className="float-panel memory-mini"><div className="float-heading"><Icon name="memory" /><h2>Memory</h2></div><div className="memory-total"><strong>{memories.length.toString().padStart(2,'0')}</strong><span>notes in current view<br /><small>Stored locally</small></span></div><button className="panel-link" onClick={() => setView('memory')}>Explore memory <Icon name="arrow" /></button></section>
        </aside>
        <section id="assistant-core" className="core-stage" aria-label="Assistant core">
          <div className="core-orb-space"><AIOrb state={orbState} /><div className="core-axis-label axis-left" aria-hidden="true">J / 01</div><div className="core-axis-label axis-right" aria-hidden="true">LOCAL CORE</div></div>
          <div className="core-caption" role="status" aria-live="polite"><span className="core-state"><i />{orbCopy[0]}</span><h1>At your command.</h1><p>{orbCopy[1]}</p><span className="core-provider-note">{health?.provider === 'mock' ? 'MOCK PROVIDER · NO MODEL INFERENCE' : health?.provider_configured ? 'CONFIGURED PROVIDER' : 'PROVIDER UNAVAILABLE'}</span><button className="core-mobile-chat" onClick={() => setView('chat')}><Icon name="chat" /><span>Open conversation</span><Icon name="arrow" /></button>{responseVisible && <button className="text-button" onClick={() => setResponseVisible(false)}>Dismiss response</button>}</div>
        </section>
        <aside className="floating-rail right-rail" aria-label="Providers and approvals">
          <section className="float-panel"><div className="float-heading"><Icon name="capabilities" /><h2>Providers</h2><span className="micro-label">HEALTH</span></div><div className="provider-line"><span className={`provider-dot ${health?.provider_configured ? 'ready' : ''}`} /><div><strong>Chat</strong><small>{health?.provider ?? 'Unavailable'}</small></div><span>{health?.provider_configured ? 'Ready' : 'Offline'}</span></div><div className="provider-line"><span className={`provider-dot ${aiStatus?.available ? 'ready' : ''}`} /><div><strong>Planning</strong><small>{String(aiStatus?.provider ?? 'Unavailable')}</small></div><span>{aiStatus?.available ? 'Ready' : 'Offline'}</span></div><button className="panel-link" onClick={() => setView('settings')}>Provider settings <Icon name="arrow" /></button></section>
          <section className={`float-panel approval-mini ${approvals.length ? 'has-approvals' : ''}`}><div className="float-heading"><Icon name="approvals" /><h2>Approvals</h2><span className="panel-count">{approvals.length}</span></div><div className="quiet-empty"><span>{approvals.length ? `${approvals.length} action${approvals.length===1?'':'s'} awaiting review` : 'Nothing awaiting approval'}</span><small>{approvals.length ? 'Review the scope before allowing execution.' : 'You stay in control of sensitive actions.'}</small></div><button className="panel-link" onClick={() => setView('approvals')}>Review permissions <Icon name="arrow" /></button></section>
          <section className="float-panel activity-mini"><div className="float-heading"><Icon name="activity" /><h2>Activity</h2></div>{activity.slice(0,2).map((item,index) => <div className="compact-event" key={index}><span className="event-line" /><div><strong>{tasks.some(task => task.task_id === item.tool_name) ? 'Task update' : item.tool_name}</strong><small>{item.detail}</small></div></div>)}{activity.length===0 && <div className="quiet-empty"><span>A quiet workspace</span><small>Verified events will appear here.</small></div>}<button className="panel-link" onClick={() => setView('activity')}>Open activity log <Icon name="arrow" /></button></section>
        </aside>
      </div>
      <div className="command-deck"><button className="command-launch" onClick={() => { setView('chat'); setResponseVisible(false) }}><span className="command-mark"><Icon name="chat" /></span><span><strong>What can I help you with?</strong><small>Start a conversation with CAM</small></span><kbd>CHAT</kbd><Icon name="arrow" /></button><button className="deck-task" onClick={() => setView('tasks')}><Icon name="tasks" /><span>New task</span></button></div>
      <footer className="environment-footer"><span><i />YOUR MACHINE. YOUR CONTROL.</span><span>CAM <b>/</b> LOCAL FIRST</span></footer>
      {authenticated && <DesktopReminderNotifications key={`${projectId ?? 'personal'}:${getSessionEpoch()}`} projectId={projectId} />}
      {approvedExport && approvedExport.scope === projectId && approvedExport.epoch === getSessionEpoch() && <section className="surface"><h3>Approved artifact export</h3><ArtifactDownload output={approvedExport.output} /><button className="button subtle" onClick={() => setApprovedExport(null)}>Dismiss export</button></section>}
      <div className="environment-notices">{health?.authentication_required && !hasSession() && <form className="surface" onSubmit={(event) => {
        event.preventDefault(); setPairingBusy(true)
        void pairBackend(pairingCode).then(async () => { setPairingCode(''); setPageError(null); await refreshWorkspace() })
          .catch((error: unknown) => setPageError(error instanceof Error ? error.message : 'Pairing failed.'))
          .finally(() => setPairingBusy(false))
      }}><h2>Pair with your local backend</h2><p>Enter the one-time code shown in the backend terminal. Sessions expire after 12 hours or a backend restart.</p><label htmlFor="pairing-code">Pairing code</label><input id="pairing-code" type="password" autoComplete="off" value={pairingCode} onChange={(event) => setPairingCode(event.target.value)} required maxLength={128} /><button className="button" disabled={pairingBusy}>Pair dashboard</button></form>}{pageError && <div className="page-error" role="alert"><span>Something needs attention</span><p>{pageError}</p><button onClick={() => setPageError(null)} aria-label="Dismiss error">×</button></div>}{connectionError && <div className="page-error" role="alert"><span>Backend connection unavailable</span><p>{connectionError}</p><button onClick={() => void refreshWorkspace()}>Retry</button></div>}</div>
      {view !== 'overview' && <section ref={panelRef} tabIndex={-1} className="work-panel" aria-labelledby="work-panel-title"><header className="work-panel-header"><div><span className="eyebrow">WORKSPACE / {view.toUpperCase()}</span><h2 id="work-panel-title">{title.title}</h2><p>{title.subtitle}</p></div><button className="close-panel" aria-label="Close workspace" onClick={closePanel}><Icon name="close" /></button></header><div className="work-panel-content">{renderView()}</div></section>}
    </main>
  </div>
}

function SurfaceHeading({ eyebrow, title, action }: { eyebrow: string; title: string; action?: ReactNode }) {
  return <div className="surface-heading"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div>{action}</div>
}

function EmptyState({ title, text }: { title: string; text: string }) {
  return <div className="empty-state"><span className="empty-mark"><Icon name="overview" /></span><strong>{title}</strong><p>{text}</p></div>
}

function SettingLine({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <div className="setting-line"><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>
}

function AvailabilityCard({ title, status, detail }: { title: string; status: string; detail: string }) {
  return <article className="availability-card"><span className="availability-lock"><Icon name="approvals" /></span><div><strong>{title}</strong><small>{detail}</small></div><span className="tag">{status}</span></article>
}

function ThemePicker({ theme, onChange }: { theme: 'dark' | 'light'; onChange: (theme: 'dark' | 'light') => void }) {
  return <div className="theme-picker"><button className={theme === 'light' ? 'active' : ''} onClick={() => onChange('light')}><span>☼</span><strong>Light</strong><small>Bright and clear</small></button><button className={theme === 'dark' ? 'active' : ''} onClick={() => onChange('dark')}><span>☾</span><strong>Dark</strong><small>Easy on the eyes</small></button></div>
}

function UnavailableFeature({ view, onSettings }: { view: View; onSettings: () => void }) {
  const details: Record<string, { icon: string; title: string; reason: string; status: string }> = {
    browser: { icon: '⌑', title: 'Browser sessions are locked', reason: 'CAM will keep browser sessions disabled until Windows OS-level network isolation is implemented and verified. A browser proxy alone is not a sufficient network boundary.', status: 'Security requirement pending' },
    computer: { icon: '▣', title: 'Computer control is unavailable', reason: 'Real desktop control needs a compatible installed Windows UI Automation dependency and verification against the applications you want to control.', status: 'Windows dependency required' },
    voice: { icon: '♩', title: 'Voice assistant is not available yet', reason: 'This project does not currently include voice input or speech output. The navigation is here so availability is clear; no fake microphone control is enabled.', status: 'Not implemented' },
  }
  const item = details[view] ?? details.browser
  return <section className="unavailable-panel surface"><div className="unavailable-art" aria-hidden="true"><Icon name={view} /></div><span className="eyebrow">FEATURE AVAILABILITY</span><h2>{item.title}</h2><p>{item.reason}</p><span className="status-pill">{item.status}</span><button className="button subtle" onClick={onSettings}>Open system status</button></section>
}
