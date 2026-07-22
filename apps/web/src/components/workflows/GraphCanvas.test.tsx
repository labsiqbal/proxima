import { describe, expect, it } from 'vitest'
import { statusLabel } from './GraphCanvas'

describe('statusLabel', () => {
  it('returns proper-cased single-word statuses for chips', () => {
    expect(statusLabel('pending')).toBe('Pending')
    expect(statusLabel('running')).toBe('Running')
    expect(statusLabel('review')).toBe('Review')
    expect(statusLabel('done')).toBe('Done')
    expect(statusLabel('failed')).toBe('Failed')
    expect(statusLabel('queued')).toBe('Queued')
    expect(statusLabel('cancelled')).toBe('Cancelled')
  })

  it('title-cases unknown underscore statuses in the fallback path', () => {
    // Cast through unknown: the helper accepts the union, but we still want the
    // default branch covered if a future status slips through untyped.
    expect(statusLabel('in_progress' as unknown as 'running')).toBe('In progress')
  })
})
