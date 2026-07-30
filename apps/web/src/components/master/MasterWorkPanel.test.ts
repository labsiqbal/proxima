import { describe, expect, it } from 'vitest'
import { formatCheckpointTime } from './MasterWorkPanel'

describe('formatCheckpointTime', () => {
  it('accepts the timezone-aware API timestamp without appending another zone', () => {
    const formatted = formatCheckpointTime('2026-07-31T05:00:00Z')

    expect(formatted).not.toBe('Invalid Date')
    expect(formatted).not.toBe('Unknown time')
  })
})
