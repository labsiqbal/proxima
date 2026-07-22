import { describe, expect, it } from 'vitest'
import { appExitSummary } from './files'

describe('appExitSummary', () => {
  it('explains a successful short-lived command with no preview server', () => {
    const info = appExitSummary({ exit_code: 0, command: 'python3 app.py' })
    expect(info.tone).toBe('ok')
    expect(info.title).toBe('Command finished (exit 0)')
    expect(info.hint).toMatch(/long-lived server/i)
    expect(info.hint).toMatch(/npm run dev/i)
  })

  it('treats a missing exit_code as success (legacy sticky payloads)', () => {
    const info = appExitSummary({ command: 'python3 app.py' })
    expect(info.tone).toBe('ok')
    expect(info.title).toContain('exit 0')
  })

  it('surfaces non-zero exits as failures with a fix-and-rerun hint', () => {
    const info = appExitSummary({ exit_code: 7, command: 'bash -lc boom' })
    expect(info.tone).toBe('fail')
    expect(info.title).toBe('Command failed (exit 7)')
    expect(info.hint).toMatch(/Run again/i)
  })
})
