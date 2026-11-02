import { afterEach, expect, it, vi } from 'vitest'

afterEach(() => {
  Reflect.deleteProperty(window, '__TAURI__')
  vi.resetModules()
})

it('receives the native session once and sends its token only as an authorization header', async () => {
  vi.resetModules()
  const token = 'a'.repeat(43)
  const invoke = vi.fn().mockResolvedValue({ version: 1, event: 'ready', endpoint: 'http://127.0.0.1:23456', token })
  Object.defineProperty(window, '__TAURI__', { configurable: true, value: { core: { invoke } } })
  const api = await import('./api')
  await Promise.all([api.initializeDesktopSession(), api.initializeDesktopSession()])
  expect(invoke).toHaveBeenCalledTimes(1)
  expect(invoke).toHaveBeenCalledWith('desktop_session')
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ status: 'ok' })))
  await api.getHealth()
  expect(String(fetchMock.mock.calls[0][0])).toBe('http://127.0.0.1:23456/api/v1/health')
  expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get('Authorization')).toBe(`Bearer ${token}`)
  api.clearSession()
  await api.initializeDesktopSession()
  expect(api.hasSession()).toBe(false)
  fetchMock.mockRestore()
})

it('rejects a remote backend endpoint before accepting a token', async () => {
  vi.resetModules()
  Object.defineProperty(window, '__TAURI__', { configurable: true, value: { core: { invoke: vi.fn().mockResolvedValue({
    version: 1, event: 'ready', endpoint: 'https://example.com', token: 'a'.repeat(43),
  }) } } })
  const api = await import('./api')
  await expect(api.initializeDesktopSession()).rejects.toThrow('invalid session contract')
  expect(api.hasSession()).toBe(false)
})

it('discards a native bootstrap reply after the session has been cleared', async () => {
  vi.resetModules()
  let resolve!: (value: unknown) => void
  const pending = new Promise((accept) => { resolve = accept })
  Object.defineProperty(window, '__TAURI__', { configurable: true, value: { core: { invoke: vi.fn().mockReturnValue(pending) } } })
  const api = await import('./api')
  const start = api.initializeDesktopSession()
  api.clearSession()
  resolve({ version: 1, event: 'ready', endpoint: 'http://127.0.0.1:23456', token: 'a'.repeat(43) })
  await start
  expect(api.hasSession()).toBe(false)
})
