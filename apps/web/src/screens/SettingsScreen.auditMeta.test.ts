import { describe, expect, it } from 'vitest'
import { formatAuditMeta } from './SettingsScreen'

describe('formatAuditMeta', () => {
  it('returns empty for blank or empty-object metadata', () => {
    expect(formatAuditMeta('')).toBe('')
    expect(formatAuditMeta(null)).toBe('')
    expect(formatAuditMeta('{}')).toBe('')
  })

  it('shows a plain path value without the path key wrapper', () => {
    expect(formatAuditMeta(JSON.stringify({ path: 'wiki/note.md' }))).toBe('wiki/note.md')
    expect(formatAuditMeta(JSON.stringify({ path: 'demo-app: python3 app.py' }))).toBe('demo-app: python3 app.py')
  })

  it('unwraps historical double-encoded settings payloads under path', () => {
    const legacy = JSON.stringify({ path: JSON.stringify({ provider: 'codex', status: 'ok' }) })
    expect(formatAuditMeta(legacy)).toBe('provider: codex · status: ok')
  })

  it('formats multi-key settings metadata as scannable pairs', () => {
    expect(formatAuditMeta(JSON.stringify({
      provider: 'codex',
      model: null,
      baseUrl: '',
      key_set: false,
    }))).toBe('provider: codex · key_set: false')
  })

  it('falls back to truncated raw text when JSON is invalid', () => {
    expect(formatAuditMeta('not-json-at-all')).toBe('not-json-at-all')
  })
})
