import { describe, expect, it } from 'vitest'
import { formatDecisionTime } from './MasterDecisionCard'

describe('formatDecisionTime', () => {
  it('formats canonical ISO-Z timestamps without Invalid Date', () => {
    const formatted = formatDecisionTime('2026-07-31T10:18:54Z')

    expect(formatted).not.toBe('Invalid Date')
    expect(formatted).not.toBe('Unknown time')
  })

  it('formats SQLite space-separated timestamps', () => {
    const formatted = formatDecisionTime('2026-07-31 10:18:54')

    expect(formatted).not.toBe('Invalid Date')
    expect(formatted).not.toBe('Unknown time')
  })
})
