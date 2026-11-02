import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { clearSession, pairBackend } from './api'

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); clearSession() })

const emptyStartup = (provider = 'mock') => {
  const chat = () => new Response(JSON.stringify({ status: 'ok', provider, model: provider === 'mock' ? 'jarvis-dev' : 'test-model', provider_configured: true }))
  const planning = () => new Response(JSON.stringify({ provider, model: provider === 'mock' ? 'jarvis-dev' : 'test-model', available: true, supported_capabilities: [] }))
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    if (url.endsWith('/health')) return chat()
    if (url.endsWith('/ai/status')) return planning()
    return new Response(JSON.stringify([]))
  })
  return fetchMock
}


const openMoreTools = () => { const summary = screen.getByText('More tools'); if (!(summary.parentElement as HTMLDetailsElement).open) fireEvent.click(summary) }

describe('CAM dashboard', () => {
  it('sends project conversation through the selected project scope', async () => {
    const calls: string[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = new URL(String(input))
      if (url.pathname.endsWith('/session/pair')) return new Response(JSON.stringify({ token: 'chat-fixture-token' }))
      if (url.pathname.endsWith('/health')) return new Response(JSON.stringify({ status: 'ok', provider: 'ollama', model: 'fixture', provider_configured: true, authentication_required: true }))
      if (url.pathname.endsWith('/projects')) return new Response(JSON.stringify([{ project_id: 'beta', name: 'Beta', root: 'fixture-beta' }]))
      if (url.pathname.endsWith('/chat')) {
        calls.push(url.searchParams.get('project_id') ?? 'personal')
        return new Response(JSON.stringify({ message: calls.at(-1) === 'beta' ? 'Scoped Beta answer' : 'Wrong personal route', provider: 'ollama', model: 'fixture' }))
      }
      return new Response(JSON.stringify([]))
    })
    await pairBackend('fixture-code')
    render(<App />)
    await screen.findByRole('option', { name: 'Beta' })
    fireEvent.change(screen.getByLabelText('Selected workspace'), { target: { value: 'beta' } })
    fireEvent.click(screen.getByRole('button', { name: 'Chat' }))
    fireEvent.change(screen.getByLabelText('Message CAM'), { target: { value: 'Explain this project' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText('Scoped Beta answer')).toBeInTheDocument()
    expect(calls).toEqual(['beta'])
    expect(screen.queryByText('Wrong personal route')).not.toBeInTheDocument()
  })

  it('ignores a chat reply belonging to a replaced session', async () => {
    let finish: (value: Response) => void = () => undefined
    let started = false
    const fetchMock = emptyStartup()
    fetchMock.mockImplementation(async (input) => {
      const url = String(input)
      if (url.endsWith('/session/pair')) return new Response(JSON.stringify({ token: 'fixture-session' }))
      if (url.endsWith('/health')) return new Response(JSON.stringify({ status: 'ok', provider: 'ollama', model: 'fixture', provider_configured: true }))
      if (url.endsWith('/chat')) { started = true; return new Promise<Response>((resolve) => { finish = resolve }) }
      return new Response(JSON.stringify([]))
    })
    await pairBackend('first-code')
    render(<App />)
    await screen.findByText('Connected')
    fireEvent.click(screen.getByRole('button', { name: 'Chat' }))
    fireEvent.change(screen.getByLabelText('Message CAM'), { target: { value: 'Old request' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(started).toBe(true))
    clearSession()
    await pairBackend('new-code')
    await act(async () => { finish(new Response(JSON.stringify({ message: 'Old session private reply', provider: 'ollama', model: 'fixture' }))) })
    expect(screen.queryByText('Old session private reply')).not.toBeInTheDocument()
  })
  it('ignores a stale task-detail rejection after switching projects', async () => {
    const fetchMock = emptyStartup()
    let finish: (value: Response) => void = () => undefined
    let detailCalls = 0
    fetchMock.mockImplementation(async (input) => {
      const url = new URL(String(input))
      if (url.pathname.endsWith('/health')) return new Response(JSON.stringify({ status: 'ok', provider: 'mock', model: 'fixture', provider_configured: true }))
      if (url.pathname.endsWith('/ai/status')) return new Response(JSON.stringify({ provider: 'mock', model: 'fixture', available: true }))
      if (url.pathname.endsWith('/projects')) return new Response(JSON.stringify([{ project_id: 'b', name: 'Beta', root: 'fixture-b' }]))
      if (url.pathname.endsWith('/tasks') && !url.searchParams.has('project_id')) return new Response(JSON.stringify([{ task_id: 'personal-task', request: 'personal fixture request', status: 'completed', current_step: 1, limits: {}, created_at: '', updated_at: '' }]))
      if (url.pathname.endsWith('/personal-task/steps')) {
        if (++detailCalls === 2) return await new Promise<Response>((resolve) => { finish = resolve })
      }
      return new Response(JSON.stringify([]))
    })
    render(<App />)
    await screen.findByRole('option', { name: 'Beta' })
    fireEvent.click(screen.getByRole('button', { name: 'Tasks' }))
    fireEvent.click(screen.getByRole('button', { name: /personal fixture request/ }))
    await waitFor(() => expect(detailCalls).toBe(2))
    fireEvent.change(screen.getByLabelText('Selected workspace'), { target: { value: 'b' } })
    await act(async () => { finish(new Response(JSON.stringify({ detail: 'old private task error' }), { status: 404 })) })
    expect(screen.queryByText('old private task error')).not.toBeInTheDocument()
    expect(screen.queryByText('personal fixture request')).not.toBeInTheDocument()
  })
  it('clears project data and ignores a late response from the previous workspace', async () => {
    const fetchMock = emptyStartup()
    let finishA: (value: Response) => void = () => undefined
    let requestedA = false
    const memory = (content: string) => [{ id: 1, memory_type: 'note', content, importance: 3, source: 'fixture', created_at: '', updated_at: '' }]
    fetchMock.mockImplementation(async (input) => {
      const url = new URL(String(input))
      if (url.pathname.endsWith('/health')) return new Response(JSON.stringify({ status: 'ok', provider: 'mock', model: 'fixture', provider_configured: true }))
      if (url.pathname.endsWith('/ai/status')) return new Response(JSON.stringify({ provider: 'mock', model: 'fixture', available: true }))
      if (url.pathname.endsWith('/projects')) return new Response(JSON.stringify([{ project_id: 'a', name: 'Alpha', root: 'fixture-a' }, { project_id: 'b', name: 'Beta', root: 'fixture-b' }]))
      if (url.pathname.endsWith('/memories')) {
        if (url.searchParams.get('project_id') === 'a') { requestedA = true; return await new Promise<Response>((resolve) => { finishA = resolve }) }
        return new Response(JSON.stringify(memory(url.searchParams.get('project_id') === 'b' ? 'Beta private note' : 'Personal private note')))
      }
      return new Response(JSON.stringify([]))
    })
    render(<App />)
    await screen.findByRole('option', { name: 'Alpha' })
    fireEvent.click(screen.getByRole('button', { name: 'Memory' }))
    expect(screen.getByText('Personal private note')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Selected workspace'), { target: { value: 'a' } })
    expect(screen.queryByText('Personal private note')).not.toBeInTheDocument()
    await waitFor(() => expect(requestedA).toBe(true))
    fireEvent.change(screen.getByLabelText('Selected workspace'), { target: { value: 'b' } })
    await screen.findByText('Beta private note')
    await act(async () => { finishA(new Response(JSON.stringify(memory('Alpha private note')))) })
    expect(screen.queryByText('Alpha private note')).not.toBeInTheDocument()
    expect(screen.getByText('Beta private note')).toBeInTheDocument()
  })
  it('shows backend status and renders a chat reply', async () => {
    const fetchMock = emptyStartup()
    fetchMock.mockImplementation(async (input) => {
      const url = String(input)
      if (url.endsWith('/health')) return new Response(JSON.stringify({ status: 'ok', provider: 'mock', model: 'jarvis-dev', provider_configured: true }))
      if (url.endsWith('/ai/status')) return new Response(JSON.stringify({ provider: 'mock', model: 'jarvis-dev', available: true, supported_capabilities: [] }))
      if (url.endsWith('/chat')) return new Response(JSON.stringify({ message: 'Mock response', provider: 'mock', model: 'jarvis-dev' }))
      return new Response(JSON.stringify([]))
    })
    render(<App />)
    await screen.findByText('Connected')
    fireEvent.click(screen.getByRole('button', { name: 'Chat' }))
    expect(within(screen.getByRole('region', { name: 'Chat' })).getAllByText('Mock (local)').length).toBeGreaterThan(0)
    fireEvent.change(screen.getByLabelText('Message CAM'), { target: { value: 'Hello' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await screen.findByText('Mock response')
  })

  it('drives the orb from a real pending request and a received response', async () => {
    let finish: (response: Response) => void = () => { throw new Error('No pending chat request') }
    emptyStartup().mockImplementation(async (input) => {
      const url = String(input)
      if (url.endsWith('/health')) return new Response(JSON.stringify({ status: 'ok', provider: 'mock', model: 'jarvis-dev', provider_configured: true }))
      if (url.endsWith('/chat')) return new Promise<Response>(resolve => { finish = resolve })
      return new Response(JSON.stringify([]))
    })
    render(<App />)
    await screen.findByRole('img', { name: '3D AI orb: idle' })
    expect(screen.getByText(/MOCK PROVIDER/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Chat' }))
    fireEvent.change(screen.getByLabelText('Message CAM'), { target: { value: 'Hello' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(screen.getByRole('img', { name: '3D AI orb: thinking' })).toBeInTheDocument()
    await act(async () => { finish(new Response(JSON.stringify({ message: 'Actual mock response', provider: 'mock', model: 'jarvis-dev' }))) })
    expect(screen.getByRole('img', { name: '3D AI orb: responding' })).toBeInTheDocument()
    expect(screen.getByText('Actual mock response')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss response' }))
    expect(screen.getByRole('img', { name: '3D AI orb: idle' })).toBeInTheDocument()
    openMoreTools(); fireEvent.click(screen.getByRole('button', { name: 'Voice assistant' }))
    expect(screen.queryByRole('img', { name: '3D AI orb: listening' })).not.toBeInTheDocument()
  })

  it('keeps feature access available when WebGL is unavailable and closes with Escape', async () => {
    emptyStartup()
    render(<App />)
    await screen.findByText(/3D rendering unavailable/)
    fireEvent.click(screen.getByRole('button', { name: 'Memory' }))
    const panel = screen.getByRole('region', { name: 'Memory' })
    expect(panel).toHaveFocus()
    // Escape also works after a disabled submit button drops focus to body.
    panel.blur()
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(screen.queryByRole('region', { name: 'Memory' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'CAM overview' })).toHaveFocus()
  })

  it.each(['openai_compatible', 'gemini', 'anthropic'])('shows %s live responses without exposing provider credentials', async (provider) => {
    const fetchMock = emptyStartup(provider)
    fetchMock.mockImplementation(async (input) => {
      const url = String(input)
      if (url.endsWith('/health')) return new Response(JSON.stringify({ status: 'ok', provider, model: 'test-model', provider_configured: true }))
      if (url.endsWith('/ai/status')) return new Response(JSON.stringify({ provider, model: 'test-model', available: true, supported_capabilities: [] }))
      if (url.endsWith('/chat')) return new Response(JSON.stringify({ message: 'Live answer', provider, model: 'test-model' }))
      return new Response(JSON.stringify([]))
    })
    render(<App />)
    await screen.findByText(/Live configured · no response yet/)
    fireEvent.click(screen.getByRole('button', { name: 'Chat' }))
    fireEvent.change(screen.getByLabelText('Message CAM'), { target: { value: 'Hello' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await screen.findByText('Live answer')
    expect(screen.getAllByText(`Live response (${provider})`).length).toBeGreaterThan(0)
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain('api-key')
  })

  it('identifies deterministic fallback after a live provider is unavailable', async () => {
    const fetchMock = emptyStartup('openai_compatible')
    fetchMock.mockImplementation(async (input) => {
      const url = String(input)
      if (url.endsWith('/health')) return new Response(JSON.stringify({ status: 'ok', provider: 'openai_compatible', model: 'test-model', provider_configured: true }))
      if (url.endsWith('/ai/status')) return new Response(JSON.stringify({ provider: 'openai_compatible', model: 'test-model', available: true, supported_capabilities: [] }))
      if (url.endsWith('/chat')) return new Response(JSON.stringify({ message: 'The configured AI provider is unavailable. This local fallback did not run a model or use tools.', provider: 'deterministic_fallback', model: 'local' }))
      return new Response(JSON.stringify([]))
    })
    render(<App />)
    await screen.findByText(/Live configured · no response yet/)
    fireEvent.click(screen.getByRole('button', { name: 'Chat' }))
    fireEvent.change(screen.getByLabelText('Message CAM'), { target: { value: 'Hello' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect((await screen.findAllByText('Deterministic fallback')).length).toBeGreaterThan(0)
  })

  it('shows a usable error when the backend is unavailable', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))
    render(<App />)
    await waitFor(() => expect(screen.getByText(/Backend offline/)).toBeInTheDocument())
  })

  it('reconnects after the API starts and clears stale health after it stops', async () => {
    const fetchMock = emptyStartup()
    fetchMock.mockRejectedValue(new Error('offline'))
    render(<App />)
    await screen.findByText('Backend connection unavailable')
    expect(screen.getByText(/Start the CAM API on port 8000/)).toBeInTheDocument()
    fetchMock.mockImplementation(async (input) => {
      if (String(input).endsWith('/health')) return new Response(JSON.stringify({ status: 'ok', provider: 'mock', model: 'jarvis-dev', provider_configured: true }))
      return new Response(JSON.stringify([]))
    })
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await screen.findByText('Connected')
    expect(screen.queryByText('Backend connection unavailable')).not.toBeInTheDocument()
    fetchMock.mockRejectedValue(new Error('offline'))
    fireEvent.click(screen.getByRole('button', { name: 'System settings' }))
    fireEvent.click(screen.getByRole('button', { name: 'Refresh status' }))
    await screen.findByText('Backend connection unavailable')
    expect(screen.queryByText('Connected')).not.toBeInTheDocument()
    expect(screen.queryByText('jarvis-dev')).not.toBeInTheDocument()
  })

  it('polls for backend recovery and cancels polling when unmounted', async () => {
    vi.useFakeTimers()
    const fetchMock = emptyStartup()
    fetchMock.mockRejectedValue(new Error('offline'))
    const { unmount } = render(<App />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByText('Backend connection unavailable')).toBeInTheDocument()
    fetchMock.mockImplementation(async (input) => {
      if (String(input).endsWith('/health')) return new Response(JSON.stringify({ status: 'ok', provider: 'mock', model: 'jarvis-dev', provider_configured: true }))
      return new Response(JSON.stringify([]))
    })
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(screen.getByText('Connected')).toBeInTheDocument()
    const signal = fetchMock.mock.calls.at(-1)?.[1]?.signal
    unmount()
    expect(signal?.aborted).toBe(true)
    const calls = fetchMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(15000)
    expect(fetchMock).toHaveBeenCalledTimes(calls)
  })

  it('updates task progress without another user action', async () => {
    vi.useFakeTimers()
    let completed = false
    emptyStartup().mockImplementation(async (input) => {
      const url = String(input)
      if (url.endsWith('/health')) return new Response(JSON.stringify({ status: 'ok', provider: 'mock', model: 'jarvis-dev', provider_configured: true }))
      if (url.endsWith('/steps')) return new Response(JSON.stringify([{ step_number: 1, description: 'Track progress', tool_name: 'echo', status: completed ? 'success' : 'running', result: completed ? { status: 'success', result: { message: 'Finished safely' } } : null }]))
      if (url.endsWith('/tasks')) return new Response(JSON.stringify([{ task_id: 'task-live', request: 'Track progress', status: completed ? 'completed' : 'running', current_step: completed ? 1 : 0, created_at: '2026-10-10T00:00:00Z', updated_at: '2026-10-10T00:00:00Z' }]))
      return new Response(JSON.stringify([]))
    })
    render(<App />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    fireEvent.click(screen.getByRole('button', { name: /^Tasks/ }))
    expect(screen.getByRole('heading', { name: 'running' })).toBeInTheDocument()
    completed = true
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(screen.getByRole('heading', { name: 'completed' })).toBeInTheDocument()
    expect(screen.getByText(/Finished safely/)).toBeInTheDocument()
  })

  it('bounds slow refreshes and does not start overlapping polls', async () => {
    vi.useFakeTimers()
    const deadline = new AbortController()
    const timeout = vi.spyOn(AbortSignal, 'timeout').mockReturnValue(deadline.signal)
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((_input, init) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    }))
    render(<App />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(timeout).toHaveBeenCalledWith(8000)
    const calls = fetchMock.mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(fetchMock).toHaveBeenCalledTimes(calls)
    await act(async () => { deadline.abort() })
    expect(screen.getByText('Backend connection unavailable')).toBeInTheDocument()
    expect(fetchMock.mock.calls.every(([, init]) => init?.signal?.aborted)).toBe(true)
  })

  it('pauses polling in hidden tabs', async () => {
    vi.useFakeTimers()
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    const fetchMock = emptyStartup()
    render(<App />)
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(fetchMock).not.toHaveBeenCalled()
    visibility.mockReturnValue('visible')
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(screen.getByText('Connected')).toBeInTheDocument()
  })

  it('shows recent tool activity', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).endsWith('/health')) return new Response(JSON.stringify({ status: 'ok', provider: 'mock', model: 'jarvis-dev', provider_configured: true }))
      if (String(input).endsWith('/activity')) return new Response(JSON.stringify([{ tool_name: 'echo', status: 'success', detail: 'Tool completed.', timestamp: '2026-09-08T00:00:00Z' }]))
      if (String(input).endsWith('/ai/status')) return new Response(JSON.stringify({ provider: 'mock', model: 'jarvis-dev', available: true, supported_capabilities: [] }))
      return new Response(JSON.stringify([]))
    })
    render(<App />)
    openMoreTools(); fireEvent.click(screen.getByRole('button', { name: 'Activity log' }))
    await within(screen.getByRole('region', { name: 'Activity log' })).findByText('echo')
    expect(screen.getByText('success')).toBeInTheDocument()
  })

  it('saves a note to persistent memory through the API', async () => {
    const fetchMock = emptyStartup()
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.endsWith('/health')) return new Response(JSON.stringify({ status: 'ok', provider: 'mock', model: 'jarvis-dev', provider_configured: true }))
      if (url.endsWith('/ai/status')) return new Response(JSON.stringify({ provider: 'mock', model: 'jarvis-dev', available: true, supported_capabilities: [] }))
      if (url.endsWith('/api/v1/memories') && init?.method === 'POST') return new Response(JSON.stringify({ id: 1, memory_type: 'note', content: 'Remember this', importance: 3, source: 'desktop', created_at: 'now', updated_at: 'now' }))
      if (url.endsWith('/api/v1/memories?query=')) return new Response(JSON.stringify([{ id: 1, memory_type: 'note', content: 'Remember this', importance: 3, source: 'desktop', created_at: 'now', updated_at: 'now' }]))
      return new Response(JSON.stringify([]))
    })
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Memory' }))
    fireEvent.change(screen.getByLabelText('Memory to save'), { target: { value: 'Remember this' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await screen.findByText('Remember this')
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/api/v1/memories'))).toBe(true)
  })

  it('resumes a paused task through the task API after approval', async () => {
    let taskStatus = 'waiting_for_approval'
    const task = () => ({ task_id: 'task-1', request: 'write note', status: taskStatus, current_step: 1, created_at: 'now', updated_at: 'now' })
    const approval = { request_id: 'approval-1', tool_name: 'write_file', risk: 'medium', status: 'pending', expires_at: 'later', preview: { target: 'note.txt' } }
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.endsWith('/health')) return new Response(JSON.stringify({ status: 'ok', provider: 'mock', model: 'jarvis-dev', provider_configured: true }))
      if (url.endsWith('/activity')) return new Response(JSON.stringify([]))
      if (url.endsWith('/approvals')) return new Response(JSON.stringify(init?.method === 'POST' ? approval : taskStatus === 'waiting_for_approval' ? [approval] : []))
      if (url.endsWith('/tasks')) return new Response(JSON.stringify([task()]))
      if (url.endsWith('/memories?query=')) return new Response(JSON.stringify([]))
      if (url.endsWith('/tasks/task-1/steps')) return new Response(JSON.stringify([{ step_number: 1, description: 'write note', tool_name: 'write_file', status: taskStatus === 'waiting_for_approval' ? 'approval_required' : 'success', approval_id: 'approval-1', result: taskStatus === 'waiting_for_approval' ? null : { detail: 'Tool completed.', result: { path: 'note.txt', effect: 'created' } } }]))
      if (url.endsWith('/approvals/approval-1/approve')) return new Response(JSON.stringify({ ...approval, status: 'approved' }))
      if (url.endsWith('/tasks/task-1/resume')) { taskStatus = 'completed'; return new Response(JSON.stringify(task())) }
      return new Response(JSON.stringify([]))
    })
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Tasks' }))
    await screen.findByRole('heading', { name: 'waiting for approval' })
    fireEvent.click(screen.getByRole('button', { name: 'Approvals' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Approve once' }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/tasks/task-1/resume'))).toBe(true))
    fireEvent.click(screen.getByRole('button', { name: 'Tasks' }))
    await screen.findByRole('heading', { name: 'completed' })
    expect(screen.getByText(/"effect": "created"/)).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/tools/execute/approval-1/continue'))).toBe(false)
  })

  it('provides feature navigation and a persistent light/dark theme toggle', async () => {
    emptyStartup()
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Use light theme' }))
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(localStorage.getItem('jarvis-theme')).toBe('light')
    openMoreTools(); fireEvent.click(screen.getByRole('button', { name: 'MCP servers' }))
    expect(await screen.findByRole('heading', { name: 'Configure an MCP server' })).toBeInTheDocument()
    expect(screen.getByText(/Explicit approval is required/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Use dark theme' }))
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  it('opens in the requested dark theme and exposes locked integrations honestly', async () => {
    emptyStartup()
    localStorage.removeItem('jarvis-theme')
    render(<App />)
    await screen.findByText('Connected')
    expect(document.documentElement.dataset.theme).toBe('dark')
    openMoreTools(); fireEvent.click(screen.getByRole('button', { name: 'Browser' }))
    expect(screen.getByText('Browser sessions are locked')).toBeInTheDocument()
    expect(screen.getByText(/Windows OS-level network isolation/)).toBeInTheDocument()
    openMoreTools(); fireEvent.click(screen.getByRole('button', { name: 'Voice assistant' }))
    expect(screen.getByRole('heading', { name: 'Push-to-talk' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Hold to talk' })).toBeDisabled()
  })

})
