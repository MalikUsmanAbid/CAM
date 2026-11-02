import { expect, it } from 'vitest'
import { diagnosticReport } from './diagnostic-report'

it('exports only known status fields and discards credentials, paths and unrecognized labels', () => {
  const report = diagnosticReport({ platform: 'Windows', python: '3.12.9', schema_version: 13,
    provider: 'sensitive-unrecognized-label', provider_configured: true, scheduler: 'running', emergency_stop: false,
    credential: 'private-secret-fixture', project_path: 'private-path-fixture', prompts: ['private-prompt-fixture'],
    verification: 'untrusted success claim' })
  const serialized = JSON.stringify(report)
  for (const value of ['private-secret-fixture', 'private-path-fixture', 'private-prompt-fixture', 'sensitive-unrecognized-label', 'untrusted success claim']) {
    expect(serialized).not.toContain(value)
  }
  expect(report.provider).toBe('unavailable')
  expect(report.platform).toBe('Windows')
  expect(report.schema_version).toBe(13)
  expect(report.verification).toContain('does not run checks')
})

it('does not coerce arbitrary field values into a support report', () => {
  const report = diagnosticReport({ python: '3.12.9 plus private text', platform: { secret: 'fixture' },
    schema_version: '13', provider_configured: 'yes', emergency_stop: {}, scheduler: 'private-label' })
  expect(report.python).toBe('unavailable')
  expect(report.platform).toBe('unavailable')
  expect(report.schema_version).toBeNull()
  expect(report.provider_configured).toBeNull()
  expect(report.emergency_stop).toBeNull()
})
