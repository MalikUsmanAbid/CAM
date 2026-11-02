import type { JsonRecord } from './api'

const member = (value: unknown, allowed: string[]) => typeof value === 'string' && allowed.includes(value) ? value : 'unavailable'
const integer = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value >= 1 && value <= 10000 ? value : null

export function diagnosticReport(snapshot: JsonRecord) {
  // Deliberate allowlist: never serialize the original response or new fields
  // added by future integrations. Unknown labels can themselves contain secrets.
  return {
    format: 'usman-jarvis-redacted-diagnostics-v1',
    generated_at: new Date().toISOString(),
    platform: member(snapshot.platform, ['Windows', 'Linux', 'Darwin']),
    python: typeof snapshot.python === 'string' && /^\d{1,2}\.\d{1,2}\.\d{1,3}$/.test(snapshot.python) ? snapshot.python : 'unavailable',
    schema_version: integer(snapshot.schema_version),
    provider: member(snapshot.provider, ['mock', 'ollama', 'openai_compatible']),
    provider_configured: typeof snapshot.provider_configured === 'boolean' ? snapshot.provider_configured : null,
    scheduler: member(snapshot.scheduler, ['starting', 'running', 'unavailable', 'stopped']),
    emergency_stop: typeof snapshot.emergency_stop === 'boolean' ? snapshot.emergency_stop : null,
    verification: 'Configuration snapshot only. This report does not run checks or establish release acceptance.',
  }
}
