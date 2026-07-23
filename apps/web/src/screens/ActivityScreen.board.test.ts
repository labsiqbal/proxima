import { describe, expect, it } from 'vitest'
import {
  TASK_BOARD_COLUMNS,
  boardPlanCardAriaLabel,
  boardTaskCardAriaLabel,
  listPlanRowAriaLabel,
  listTaskRowAriaLabel,
} from './ActivityScreen'

describe('TASK_BOARD_COLUMNS', () => {
  it('includes Failed so terminal failures stay visible on the board', () => {
    expect(TASK_BOARD_COLUMNS.map(column => column.key)).toEqual([
      'queued',
      'running',
      'review',
      'done',
      'failed',
    ])
    expect(TASK_BOARD_COLUMNS.map(column => column.label)).toContain('Failed')
  })
})

describe('board card aria labels', () => {
  it('spaces plan title, kind, progress, and age', () => {
    expect(boardPlanCardAriaLabel({ title: 'Add farewell output to demo app' }, '0/1', '11h ago'))
      .toBe('Add farewell output to demo app · Plan · 0/1 jobs · 11h ago')
  })

  it('labels classic tasks without smashing title into kind', () => {
    expect(boardTaskCardAriaLabel({ title: 'Ship readme', schedule_id: null, workflow_id: null }, 'Task', 'now'))
      .toBe('Ship readme · Task · Task · now')
    expect(boardTaskCardAriaLabel({ title: 'Nightly', schedule_id: 3, workflow_id: 9 }, '1/2 steps', '1d ago'))
      .toBe('Nightly · Scheduled · 1/2 steps · 1d ago')
  })
})

describe('list row aria labels', () => {
  it('spaces plan status and optional worktree state', () => {
    expect(listPlanRowAriaLabel(
      { title: 'gnhf-e2e-farewell', status: 'failed', worktree: null },
      '0/1',
      '2h ago',
    )).toBe('gnhf-e2e-farewell · Plan · failed · 0/1 · 2h ago')

    expect(listPlanRowAriaLabel(
      {
        title: 'gnhf-e2e-farewell',
        status: 'done',
        worktree: {
          area_id: 1,
          branch: 'proxima/job-1',
          base_branch: 'main',
          base_commit: 'aaa',
          status: 'merged',
          merge_commit: 'bbb',
          error: null,
          worktree_path: '/tmp/wt',
        },
      },
      '1/1',
      '2h ago',
    )).toBe('gnhf-e2e-farewell · Plan · done · merged · 1/1 · 2h ago')
  })

  it('spaces classic task status and type', () => {
    expect(listTaskRowAriaLabel(
      { title: 'Tidy docs', status: 'done', schedule_id: null, workflow_id: null },
      '—',
      '3h ago',
    )).toBe('Tidy docs · Task · done · — · 3h ago')
  })
})
