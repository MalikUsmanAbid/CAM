import { webcrypto } from 'node:crypto'
import { afterEach, expect, it, vi } from 'vitest'
import { clearSession, readEncryptedBackup } from './api'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); clearSession() })

it('accepts only the exact downloaded bytes and a real SHA-256 match', async () => {
  vi.stubGlobal('crypto', webcrypto)
  const payload = new Uint8Array([1, 2, 3, 4, 5])
  const digest = new Uint8Array(await webcrypto.subtle.digest('SHA-256', payload))
  const sha256 = Array.from(digest, (value) => value.toString(16).padStart(2, '0')).join('')
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(payload))
  const item = { download_id: 'a'.repeat(32), sha256, bytes: payload.byteLength, expires_in_seconds: 300 }
  const blob = await readEncryptedBackup(item, new AbortController().signal)
  expect(new Uint8Array(await blob.arrayBuffer())).toEqual(payload)
  expect(String(fetch.mock.calls[0][0])).toContain('/api/v1/backups/download/' + item.download_id)
})

it('rejects checksum mismatch and size overflow before offering any browser download', async () => {
  vi.stubGlobal('crypto', webcrypto)
  const payload = new Uint8Array([1, 2, 3, 4])
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(payload)).mockResolvedValueOnce(new Response(payload))
  const item = { download_id: 'a'.repeat(32), sha256: '0'.repeat(64), bytes: payload.byteLength, expires_in_seconds: 300 }
  await expect(readEncryptedBackup(item, new AbortController().signal)).rejects.toThrow('checksum')
  await expect(readEncryptedBackup({ ...item, bytes: 2 }, new AbortController().signal)).rejects.toThrow('approved size')
  expect(fetch).toHaveBeenCalledTimes(2)
})
