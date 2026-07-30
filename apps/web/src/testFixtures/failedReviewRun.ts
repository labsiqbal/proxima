import type { GraphJob } from '../types'
import type { AttentionItem } from '../api/master'

export const FRESH_FAILED_REVIEW_RUN: GraphJob = {
  id: 41,
  session_id: 14,
  title: 'Launch readiness review',
  status: 'review',
  engine: 'graph',
  graph: {
    nodes: [
      {
        id: 'gather',
        type: 'agent',
        name: 'Gather launch evidence',
        instruction: 'Gather launch evidence',
        output_kind: 'text',
      },
    ],
    edges: [],
  },
  node_states: [
    {
      id: 1,
      job_id: 41,
      node_id: 'gather',
      status: 'failed',
      output_kind: 'text',
      version: 1,
      started_at: '2026-07-31T05:00:00Z',
      finished_at: '2026-07-31T05:00:12Z',
    },
  ],
  created_at: '2026-07-31T05:00:00Z',
  started_at: '2026-07-31T05:00:00Z',
  finished_at: '2026-07-31T05:00:12Z',
}

export const FRESH_FAILED_REVIEW_ATTENTION: AttentionItem = {
  id: 'job:41',
  kind: 'job_diff',
  title: 'Launch readiness review failed',
  target: { view: 'workflows', job_id: 41, engine: 'graph' },
  inline_ok: false,
  actions: [],
  status: 'open',
  created_at: '2026-07-31T05:00:12Z',
  run_projection: {
    status: 'failed',
    started_at: '2026-07-31T05:00:00Z',
    finished_at: '2026-07-31T05:00:12Z',
    duration_seconds: 12,
  },
}
