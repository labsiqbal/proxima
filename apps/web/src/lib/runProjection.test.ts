import { describe, expect, it } from 'vitest'
import {
  formatRunAge,
  formatRunDuration,
  projectRun,
  runStatusLabel,
} from './runProjection'
import { FRESH_FAILED_REVIEW_RUN } from '../testFixtures/failedReviewRun'

describe('authoritative run projection', () => {
  it('projects one failed status, start, and duration for every surface', () => {
    const projection = projectRun(FRESH_FAILED_REVIEW_RUN)

    expect(projection).toEqual({
      status: 'failed',
      started_at: '2026-07-31T05:00:00Z',
      finished_at: '2026-07-31T05:00:12Z',
      duration_seconds: 12,
    })
    expect(runStatusLabel(projection.status)).toBe('Failed')
    expect(formatRunAge(projection, FRESH_FAILED_REVIEW_RUN.created_at, Date.parse('2026-07-31T05:00:30Z'))).toBe('Just now')
    expect(formatRunDuration(projection, Date.parse('2026-07-31T05:00:30Z'))).toBe('12s')
  })

  it('does not interpret a timezone-less value in the browser timezone', () => {
    const projection = projectRun({
      ...FRESH_FAILED_REVIEW_RUN,
      run_projection: {
        status: 'failed',
        started_at: '2026-07-31T05:00:00Z',
        finished_at: '2026-07-31T05:00:12Z',
        duration_seconds: 12,
      },
    })

    expect(projection.started_at).toMatch(/(?:Z|[+-]\d\d:\d\d)$/)
  })

  it('uses linear step state when an API payload carries no graph nodes', () => {
    const projection = projectRun({
      status: 'review',
      node_states: [],
      steps_state: [{ status: 'failed' }],
    })

    expect(projection.status).toBe('failed')
  })
})
