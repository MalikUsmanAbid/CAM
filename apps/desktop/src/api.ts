export let API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

let desktopInitialization: Promise<void> | undefined

export function initializeDesktopSession(): Promise<void> {
  const bridge = (window as unknown as {
    __TAURI__?: { core: { invoke: (command: string) => Promise<unknown> } }
  }).__TAURI__
  if (!bridge) return Promise.resolve()
  if (!desktopInitialization) {
    const initialEpoch = sessionEpoch
    desktopInitialization = bridge.core.invoke('desktop_session').then((response) => {
      const result = response as { version?: number; event?: string; endpoint?: string; token?: string }
      if (result.version !== 1 || !['ready', 'session_renewed'].includes(result.event ?? '')
        || typeof result.endpoint !== 'string' || !/^http:\/\/127\.0\.0\.1:\d{1,5}$/.test(result.endpoint)
        || Number(new URL(result.endpoint).port) < 1 || Number(new URL(result.endpoint).port) > 65535
        || typeof result.token !== 'string' || !/^[A-Za-z0-9_-]{32,128}$/.test(result.token)) {
        throw new Error('Desktop backend returned an invalid session contract.')
      }
      // A logout or a later pairing supersedes a delayed bootstrap reply.
      if (sessionEpoch !== initialEpoch) return
      API_BASE_URL = result.endpoint
      sessionToken = result.token
      sessionEpoch++
    })
  }
  return desktopInitialization
}

// Session credentials deliberately live only in memory, never in URLs or browser storage.
let sessionToken: string | null = null
let sessionEpoch = 0
let sessionIntentEpoch = 0
export const getSessionEpoch = () => sessionEpoch
export const hasSession = () => sessionToken !== null
export const clearSession = (reason?: 'unauthorized') => { sessionToken = null; sessionEpoch++; if (reason !== 'unauthorized') sessionIntentEpoch++ }

let desktopRenewal: Promise<void> | undefined
export function renewDesktopSession(): Promise<void> {
  const bridge = (window as unknown as { __TAURI__?: { core: { invoke: (command: string) => Promise<unknown> } } }).__TAURI__
  if (!bridge) return Promise.reject(new Error('Private session renewal requires the desktop shell.'))
  if (desktopRenewal) return desktopRenewal
  const intent = sessionIntentEpoch
  desktopRenewal = bridge.core.invoke('renew_desktop_session').then((response) => {
    const result = response as { version?: number; event?: string; endpoint?: string; token?: string }
    if (result.version !== 1 || result.event !== 'session_renewed' || result.endpoint !== API_BASE_URL
      || typeof result.token !== 'string' || !/^[A-Za-z0-9_-]{32,128}$/.test(result.token)) {
      throw new Error('Private renewal contract rejected.')
    }
    if (intent !== sessionIntentEpoch) throw new Error('Renewal was superseded by a user session change.')
    sessionToken = result.token; sessionEpoch++
  }).finally(() => { desktopRenewal = undefined })
  return desktopRenewal
}

export async function pairBackend(code: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/v1/session/pair`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }), cache: 'no-store',
  })
  if (!response.ok) throw new Error(await readError(response))
  const payload = await response.json() as { token: string }
  sessionToken = payload.token
  sessionEpoch++
  sessionIntentEpoch++
}

async function authenticatedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const requestEpoch = sessionEpoch
  const headers = new Headers(init?.headers)
  if (sessionToken) headers.set('Authorization', `Bearer ${sessionToken}`)
  const response = await fetch(input, { ...init, headers, cache: 'no-store' })
  if (response.status === 401 && requestEpoch === sessionEpoch) clearSession('unauthorized')
  return response
}

export interface HealthStatus {
  status: string
  provider: string
  model: string
  provider_configured: boolean
  authentication_required?: boolean
}

export interface ChatResult {
  approval?: StudioResult['approval']
  message: string
  provider: string
  model: string
}

export interface ActivityItem {
  tool_name: string
  status: string
  detail: string
  timestamp: string
}

export interface ApprovalItem {
  request_id: string
  tool_name: string
  risk: string
  status: string
  expires_at: string
  preview: Record<string, unknown>
}

export interface TaskStatus {
  task_id: string
  request: string
  status: string
  current_step: number
  error?: string | null
  created_at: string
  updated_at: string
}

export interface TaskStep {
  step_number: number
  description: string
  tool_name: string
  status: string
  approval_id?: string | null
  result?: Record<string, unknown> | null
}

export interface MemoryItem {
  id: number
  memory_type: string
  content: string
  importance: number | null
  source: string | null
  created_at: string
  updated_at: string
}

export type JsonRecord = Record<string, unknown>

export interface McpServerRequest {
  server_id: string
  name: string
  endpoint: string
  secret_reference: string | null
}

interface ApiErrorPayload {
  detail?: string
}

async function readError(response: Response): Promise<string> {
  const payload = (await response.json().catch(() => ({}))) as ApiErrorPayload
  return payload.detail ?? `Request failed with status ${response.status}.`
}

export async function getHealth(signal?: AbortSignal): Promise<HealthStatus> {
  const response = await authenticatedFetch(`${API_BASE_URL}/api/v1/health`, { signal })
  if (!response.ok) throw new Error(await readError(response))
  return (await response.json()) as HealthStatus
}

export async function sendChat(message: string, signal?: AbortSignal, projectId?: string | null): Promise<ChatResult> {
  const response = await authenticatedFetch(scopedUrl('/api/v1/chat', projectId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
    signal,
  })
  if (response.status === 409) {
    const pending = await response.json() as StudioResult
    if (pending.status === 'approval_required' && pending.approval) return { message: 'Project-context read needs approval. Review the exact request in Approvals.', provider: 'approval_required', model: 'pending', approval: pending.approval }
    throw new Error(pending.detail)
  }
  if (!response.ok) throw new Error(await readError(response))
  return (await response.json()) as ChatResult
}

function scopedUrl(path: string, projectId?: string | null): string {
  return `${API_BASE_URL}${path}${projectId ? `${path.includes('?') ? '&' : '?'}project_id=${encodeURIComponent(projectId)}` : ''}`
}

export async function getActivity(signal?: AbortSignal, projectId?: string | null): Promise<ActivityItem[]> {
  const response = await authenticatedFetch(scopedUrl('/api/v1/activity', projectId), { signal })
  if (!response.ok) throw new Error(await readError(response))
  return (await response.json()) as ActivityItem[]
}

export async function getApprovals(signal?: AbortSignal, projectId?: string | null): Promise<ApprovalItem[]> {
  const response = await authenticatedFetch(scopedUrl('/api/v1/approvals', projectId), { signal })
  if (!response.ok) throw new Error(await readError(response))
  return (await response.json()) as ApprovalItem[]
}

export async function decideApproval(id: string, approved: boolean, projectId?: string | null): Promise<ApprovalItem> {
  const action = approved ? 'approve' : 'reject'
  const response = await authenticatedFetch(scopedUrl(`/api/v1/approvals/${encodeURIComponent(id)}/${action}`, projectId), { method: 'POST' })
  if (!response.ok) throw new Error(await readError(response))
  return (await response.json()) as ApprovalItem
}

export async function continueTool(id: string, projectId?: string | null): Promise<StudioResult> {
  const response = await authenticatedFetch(scopedUrl(`/api/v1/tools/execute/${encodeURIComponent(id)}/continue`, projectId), { method: 'POST' })
  if (!response.ok) throw new Error(await readError(response))
  return await response.json() as StudioResult
}

export async function createTask(request: string, projectId?: string | null): Promise<TaskStatus> {
  const response = await authenticatedFetch(`${API_BASE_URL}/api/v1/tasks`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request, ...(projectId ? { project_id: projectId } : {}) }) })
  if (!response.ok) throw new Error(await readError(response))
  return (await response.json()) as TaskStatus
}

export async function getTasks(signal?: AbortSignal, projectId?: string | null): Promise<TaskStatus[]> {
  const response = await authenticatedFetch(scopedUrl('/api/v1/tasks', projectId), { signal })
  if (!response.ok) throw new Error(await readError(response))
  return (await response.json()) as TaskStatus[]
}

export async function getTaskSteps(taskId: string, signal?: AbortSignal, projectId?: string | null): Promise<TaskStep[]> {
  const response = await authenticatedFetch(scopedUrl(`/api/v1/tasks/${encodeURIComponent(taskId)}/steps`, projectId), { signal })
  if (!response.ok) throw new Error(await readError(response))
  return (await response.json()) as TaskStep[]
}

export async function resumeTask(taskId: string, projectId?: string | null): Promise<TaskStatus> {
  const response = await authenticatedFetch(scopedUrl(`/api/v1/tasks/${encodeURIComponent(taskId)}/resume`, projectId), { method: 'POST' })
  if (!response.ok) throw new Error(await readError(response))
  return (await response.json()) as TaskStatus
}

export async function cancelTask(taskId: string, projectId?: string | null): Promise<TaskStatus> {
  const response = await authenticatedFetch(scopedUrl(`/api/v1/tasks/${encodeURIComponent(taskId)}/cancel`, projectId), { method: 'POST' })
  if (!response.ok) throw new Error(await readError(response))
  return (await response.json()) as TaskStatus
}

export async function getMemories(query = '', signal?: AbortSignal, projectId?: string | null): Promise<MemoryItem[]> {
  const params = new URLSearchParams({ query })
  const response = await authenticatedFetch(scopedUrl(`/api/v1/memories?${params}`, projectId), { signal })
  if (!response.ok) throw new Error(await readError(response))
  return (await response.json()) as MemoryItem[]
}

export async function createMemory(content: string, projectId?: string | null): Promise<MemoryItem> {
  const response = await authenticatedFetch(scopedUrl('/api/v1/memories', projectId), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ memory_type: 'note', content, importance: 3, source: 'desktop' }),
  })
  if (!response.ok) throw new Error(await readError(response))
  return (await response.json()) as MemoryItem
}

async function requestJson<T>(path: string, init?: RequestInit, projectId?: string | null): Promise<T> {
  const response = await authenticatedFetch(scopedUrl(path, projectId), init)
  if (!response.ok) throw new Error(await readError(response))
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

function jsonRequest(method: string, payload?: unknown): RequestInit {
  return {
    method,
    ...(payload === undefined ? {} : {
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  }
}

export interface RegisteredProject { project_id: string; name: string; root: string }
export interface StudioResult { tool?: string; tool_name?: string; status: string; detail: string; result: JsonRecord | null; approval?: { request_id: string; preview: JsonRecord } | null }
export const executeStudioTool = (name: 'studio_list' | 'studio_read' | 'studio_propose' | 'studio_ai_propose' | 'studio_propose_create' | 'studio_inspect_change' | 'studio_apply' | 'studio_rollback' | 'studio_rollback_create' | 'studio_changes' | 'studio_inspect_recovery' | 'studio_recover' | 'studio_prepare_command' | 'studio_prepare_git' | 'studio_run_command' | 'studio_command_runs', arguments_: JsonRecord, projectId: string, signal?: AbortSignal) => requestJson<StudioResult>('/api/v1/tools/execute', { ...jsonRequest('POST', { name, arguments: arguments_, project_id: projectId }), signal })
export const continueStudioTool = (id: string, projectId: string) => requestJson<StudioResult>(`/api/v1/tools/execute/${encodeURIComponent(id)}/continue`, jsonRequest('POST'), projectId)
export const prepareNewProject = (name: string, path: string) => requestJson<StudioResult>('/api/v1/tools/execute', jsonRequest('POST', { name: 'studio_create_project', project_id: null, arguments: { name, path } }))
export const continueNewProject = (id: string) => requestJson<StudioResult>(`/api/v1/tools/execute/${encodeURIComponent(id)}/continue`, jsonRequest('POST'))
export const executeOwnedComputerTool = (name: 'computer_owned_open' | 'computer_owned_observe' | 'computer_owned_sessions' | 'computer_owned_operations' | 'computer_owned_locate' | 'computer_owned_invoke' | 'computer_owned_set_value' | 'computer_owned_close', arguments_: JsonRecord) => requestJson<StudioResult>('/api/v1/tools/execute', jsonRequest('POST', { name, arguments: arguments_, project_id: null }))
export const getProjects = (signal?: AbortSignal) => requestJson<RegisteredProject[]>('/api/v1/projects', { signal })
export const registerProject = (name: string, root: string) => requestJson<RegisteredProject>('/api/v1/projects', jsonRequest('POST', { name, root }))
export const getUsage = () => requestJson<JsonRecord>('/api/v1/usage')
export interface ActionPolicy { revision: number; blocked_categories: string[]; require_read_approval: boolean }
export const getActionPolicy = () => requestJson<ActionPolicy>('/api/v1/policy')
export const updateActionPolicy = (policy: Omit<ActionPolicy, 'revision'>) => requestJson<ActionPolicy>('/api/v1/policy', jsonRequest('PUT', policy))
export const configureCloudPolicy = (policy: JsonRecord) => requestJson<JsonRecord>('/api/v1/usage/policy', jsonRequest('PUT', policy))

export async function readProgressEvents(after: number, onEvent: (event: JsonRecord & { id: number }) => void, signal: AbortSignal, projectId?: string | null): Promise<void> {
  const response = await authenticatedFetch(scopedUrl('/api/v1/events/stream', projectId), {
    headers: { 'Last-Event-ID': String(after) }, signal,
  })
  if (!response.ok) throw new Error(await readError(response))
  if (!response.body) throw new Error('Progress stream is unavailable.')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let pending = ''
  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read()
      if (done) break
      pending += decoder.decode(value, { stream: true })
      let boundary = pending.indexOf('\n\n')
      while (boundary >= 0) {
        const frame = pending.slice(0, boundary)
        if (frame.length > 65536) throw new Error('Progress event exceeded the allowed size.')
        pending = pending.slice(boundary + 2)
        const data = frame.split('\n').filter((line) => line.startsWith('data: ')).map((line) => line.slice(6)).join('\n')
        if (data) {
          const event = JSON.parse(data) as JsonRecord & { id: number }
          if (Number.isSafeInteger(event.id) && event.id > after) { after = event.id; onEvent(event) }
        }
        boundary = pending.indexOf('\n\n')
      }
      if (pending.length > 65536) throw new Error('Progress event exceeded the allowed size.')
    }
  } finally { await reader.cancel().catch(() => undefined); reader.releaseLock() }
}

export function getAiStatus(signal?: AbortSignal): Promise<JsonRecord> { return requestJson('/api/v1/ai/status', { signal }) }
export function getTools(signal?: AbortSignal, projectId?: string | null): Promise<JsonRecord[]> { return requestJson('/api/v1/tools', { signal }, projectId) }
export function getCapabilities(signal?: AbortSignal, projectId?: string | null): Promise<JsonRecord[]> { return requestJson('/api/v1/capabilities', { signal }, projectId) }
export function getSkills(signal?: AbortSignal, projectId?: string | null): Promise<JsonRecord[]> { return requestJson('/api/v1/skills', { signal }, projectId) }
export function getMcpServers(signal?: AbortSignal): Promise<JsonRecord[]> { return requestJson('/api/v1/mcp', { signal }) }
export function getWorkflows(signal?: AbortSignal): Promise<JsonRecord[]> { return requestJson('/api/v1/workflows', { signal }) }
export function getSchedules(signal?: AbortSignal): Promise<JsonRecord[]> { return requestJson('/api/v1/schedules', { signal }) }

export function createMcpServer(server: McpServerRequest): Promise<JsonRecord> {
  return requestJson('/api/v1/mcp/servers', jsonRequest('POST', server))
}
export function approveMcpServer(id: string): Promise<JsonRecord> {
  return requestJson(`/api/v1/mcp/servers/${encodeURIComponent(id)}/approve`, jsonRequest('POST'))
}
export function enableMcpServer(id: string): Promise<JsonRecord> {
  return requestJson(`/api/v1/mcp/servers/${encodeURIComponent(id)}/enable`, jsonRequest('POST'))
}
export function disableMcpServer(id: string): Promise<JsonRecord> {
  return requestJson(`/api/v1/mcp/servers/${encodeURIComponent(id)}/disable`, jsonRequest('POST'))
}
export function deleteMcpServer(id: string): Promise<void> {
  return requestJson(`/api/v1/mcp/servers/${encodeURIComponent(id)}`, jsonRequest('DELETE'))
}

export function createWorkflow(kind: string, idempotencyKey: string): Promise<JsonRecord> {
  return requestJson('/api/v1/workflows', jsonRequest('POST', { kind, idempotency_key: idempotencyKey }))
}
export function transitionWorkflow(id: string, status: string): Promise<JsonRecord> {
  return requestJson(`/api/v1/workflows/${encodeURIComponent(id)}/transition`, jsonRequest('POST', { status }))
}
export function createSchedule(schedule: { task_id: string; schedule_type: string; next_run: string; retry_limit: number }): Promise<JsonRecord> {
  return requestJson('/api/v1/schedules', jsonRequest('POST', schedule))
}
export function cancelSchedule(id: string): Promise<void> {
  return requestJson(`/api/v1/schedules/${encodeURIComponent(id)}`, jsonRequest('DELETE'))
}
export function updateMemory(id: number, content: string, projectId?: string | null): Promise<MemoryItem> {
  return requestJson(`/api/v1/memories/${id}`, jsonRequest('PUT', { memory_type: 'note', content, source: 'desktop' }), projectId)
}
export function deleteMemory(id: number, projectId?: string | null): Promise<void> {
  return requestJson(`/api/v1/memories/${id}`, jsonRequest('DELETE'), projectId)
}
export function searchMemories(query: string, projectId?: string | null): Promise<MemoryItem[]> {
  const params = new URLSearchParams({ query })
  return requestJson(`/api/v1/memories?${params}`, undefined, projectId)
}

export function workspaceRequest<T>(path: string, method = 'GET', body?: unknown, projectId?: string | null, signal?: AbortSignal): Promise<T> {
  return requestJson(`/api/v1/workspace${path}`, { ...jsonRequest(method, body), signal }, projectId)
}

export function voiceStatus(signal?: AbortSignal): Promise<{ available: boolean; privacy: string }> {
  return requestJson('/api/v1/voice/status', { signal })
}

export function oauthRequest<T>(path: string, method: string, body?: unknown): Promise<T> {
  return requestJson(`/api/v1/oauth${path}`, jsonRequest(method, body))
}

export type BackupReady = { download_id: string; sha256: string; bytes: number; expires_in_seconds: number }
export type BackupStatus = { available: boolean; ready: BackupReady[]; pending: { capture_id: string; expires_in_seconds: number }[] }
export const getBackupStatus = (signal?: AbortSignal) => requestJson<BackupStatus>('/api/v1/backups/status', { signal })
export const prepareBackup = (passphrase: string) => requestJson<StudioResult & { capture_id: string }>('/api/v1/backups/prepare', jsonRequest('POST', { passphrase }))
export const discardBackup = (identifier: string) => requestJson('/api/v1/backups/capture/' + encodeURIComponent(identifier), jsonRequest('DELETE'))
export async function readEncryptedBackup(item: BackupReady, signal: AbortSignal): Promise<Blob> {
  if (!/^[a-f0-9]{32}$/.test(item.download_id) || !/^[a-f0-9]{64}$/.test(item.sha256)
    || !Number.isSafeInteger(item.bytes) || item.bytes < 1 || item.bytes > 36 * 1024 * 1024) throw new Error('Invalid encrypted backup contract.')
  const response = await authenticatedFetch(`${API_BASE_URL}/api/v1/backups/download/${item.download_id}`, { signal })
  if (!response.ok) throw new Error(await readError(response))
  if (!response.body) throw new Error('Encrypted download stream is unavailable.')
  const reader = response.body.getReader()
  const parts: ArrayBuffer[] = []
  let received = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      received += value.byteLength
      if (received > item.bytes) throw new Error('Encrypted download exceeds its approved size.')
      parts.push(Uint8Array.from(value).buffer)
    }
  } finally { await reader.cancel().catch(() => undefined); reader.releaseLock() }
  if (received !== item.bytes) throw new Error('Encrypted download is incomplete.')
  const blob = new Blob(parts, { type: 'application/octet-stream' })
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer()))
  if (Array.from(digest, (value) => value.toString(16).padStart(2, '0')).join('') !== item.sha256) throw new Error('Encrypted download checksum mismatch.')
  return blob
}

export async function transcribeCapture(audio: ArrayBuffer, signal: AbortSignal): Promise<StudioResult> {
  const response = await authenticatedFetch(`${API_BASE_URL}/api/v1/voice/capture`, {
    method: 'POST', body: audio, headers: { 'Content-Type': 'audio/wav' }, signal,
  })
  if (!response.ok) throw new Error(await readError(response))
  return response.json() as Promise<StudioResult>
}

// Keys pass once over the authenticated loopback channel; never browser storage.
export const storeModelCredential = (secret: string) => requestJson<{ reference: string }>('/api/v1/credentials', jsonRequest('POST', { secret }))
export const discoverModels = (provider: 'gemini' | 'anthropic' | 'nvidia', reference: string) => requestJson<{ models: { id: string; name: string }[]; partial: boolean; notice: string }>('/api/v1/models/discover', jsonRequest('POST', { provider, credential_reference: reference }))
export const revokeModelCredential = (reference: string) => requestJson<JsonRecord>(`/api/v1/credentials/${encodeURIComponent(reference)}`, { method: 'DELETE' })
