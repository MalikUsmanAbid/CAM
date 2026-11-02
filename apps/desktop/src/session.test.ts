import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { clearSession, createTask, decideApproval, getTasks, hasSession, pairBackend, readProgressEvents, resumeTask, sendChat, updateMemory } from './api'

beforeEach(() => { clearSession() })
afterEach(() => { vi.unstubAllGlobals(); clearSession() })

it('binds chat requests to the selected project and preserves read approvals', async () => {
  let seen = ''
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    seen = String(input)
    return new Response(JSON.stringify({ status: 'approval_required', detail: 'read approval', result: null, approval: { request_id: 'chat-grant', preview: { project_id: 'beta' } } }), { status: 409 })
  }))
  const result = await sendChat('Explain project', undefined, 'beta')
  expect(new URL(seen).searchParams.get('project_id')).toBe('beta')
  expect(result.approval?.request_id).toBe('chat-grant')
  expect(result.provider).toBe('approval_required')
})

it('does not clear a newer session when an old request returns unauthorized', async () => {
  let finish: (response: Response) => void = () => undefined
  let pair = 0
  vi.stubGlobal('fetch', vi.fn(async (input, init?: RequestInit) => {
    if (String(input).endsWith('/session/pair')) return new Response(JSON.stringify({ token: ++pair === 1 ? 'old-fixture' : 'new-fixture' }))
    if (new Headers(init?.headers).get('Authorization') === 'Bearer old-fixture') return new Promise<Response>((resolve) => { finish = resolve })
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer new-fixture')
    return new Response(JSON.stringify([]))
  }))
  await pairBackend('first-code')
  const pending = getTasks()
  const rejected = expect(pending).rejects.toThrow('old session expired')
  await pairBackend('replacement-code')
  finish(new Response(JSON.stringify({ detail: 'old session expired' }), { status: 401 }))
  await rejected
  expect(hasSession()).toBe(true)
  await getTasks()
})

it('keeps pairing credentials in memory and authenticates subsequent API requests', async () => {
  const requests: { url: string; init?: RequestInit }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    requests.push({ url, init })
    return new Response(JSON.stringify(url.endsWith('/session/pair') ? { token: 'test-bearer-token' } : []), { status: 200 })
  }))
  await pairBackend('one-time-code')
  expect(hasSession()).toBe(true)
  await getTasks()
  expect(new Headers(requests[1].init?.headers).get('Authorization')).toBe('Bearer test-bearer-token')
  expect(requests[1].url).not.toContain('test-bearer-token')
  expect(localStorage.getItem('jarvis-session')).toBeNull()
  expect(sessionStorage.getItem('jarvis-session')).toBeNull()
})

it('clears an expired session after an authenticated rejection', async () => {
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ token: 'expired-token' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Session expired' }), { status: 401 })))
  await pairBackend('pairing-code')
  await expect(getTasks()).rejects.toThrow('Session expired')
  expect(hasSession()).toBe(false)
})

it('replays fragmented progress frames once and sends the reconnect cursor in a header', async () => {
  let headers: Headers | undefined
  const bytes = new TextEncoder()
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
    headers = new Headers(init?.headers)
    const stream = new ReadableStream({ start(controller) {
      controller.enqueue(bytes.encode('id: 7\nevent: activity\ndata: {"id":7,"detail":"al'))
      controller.enqueue(bytes.encode('ready seen"}\n\nid: 8\nevent: activity\ndata: {"id":8,"detail":"new"}\n\n: heartbeat\n\n'))
      controller.close()
    } })
    return new Response(stream, { status: 200 })
  }))
  const ids: number[] = []
  await readProgressEvents(7, (event) => ids.push(event.id), new AbortController().signal)
  expect(ids).toEqual([8])
  expect(headers?.get('Last-Event-ID')).toBe('7')
})

it('binds task, approval, memory and reconnect requests to their explicit project', async () => {
  const requests: { url: string; init?: RequestInit }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    requests.push({ url, init })
    if (url.includes('/events/stream')) return new Response(': heartbeat\n\n')
    return new Response(JSON.stringify({}))
  }))
  await createTask('fix the fixture', 'project-b')
  await decideApproval('grant', true, 'project-b')
  await resumeTask('task', 'project-b')
  await updateMemory(1, 'private project note', 'project-b')
  await readProgressEvents(17, () => undefined, new AbortController().signal, 'project-b')
  expect(JSON.parse(String(requests[0].init?.body))).toEqual({ request: 'fix the fixture', project_id: 'project-b' })
  expect(requests.slice(1).every((item) => new URL(item.url).searchParams.get('project_id') === 'project-b')).toBe(true)
  expect(new Headers(requests[4].init?.headers).get('Last-Event-ID')).toBe('17')
})
