import { describe, expect, it, vi } from 'vitest'
import type { GraphJob, GraphWorkflowDraft } from '../types'
import { getOrStartDraftCreate } from './GraphScreen'

const draft = (name = 'todo-storage-decision'): GraphWorkflowDraft => ({
  name,
  graph: {
    nodes: [
      { id: 'trigger', type: 'trigger', name: 'Go', instruction: '', output_kind: 'json' },
      { id: 'step', type: 'agent', name: 'Step', instruction: 'do it', output_kind: 'text' },
    ],
    edges: [{ from: 'trigger', to: 'step' }],
  },
})

const job = (id: number, title: string): GraphJob =>
  ({
    id,
    title,
    status: 'queued',
    graph: draft(title).graph,
    node_states: [],
  }) as GraphJob

describe('getOrStartDraftCreate', () => {
  it('starts create once and reuses the same promise for the same draft object', async () => {
    const body = draft()
    const start = vi.fn(async () => job(8, body.name))
    const first = getOrStartDraftCreate(body, start)
    const second = getOrStartDraftCreate(body, start)
    expect(first).toBe(second)
    expect(start).toHaveBeenCalledTimes(1)
    await expect(first).resolves.toMatchObject({ id: 8, title: body.name })
  })

  it('allows a new create after the previous attempt failed', async () => {
    const body = draft('retry-me')
    const start = vi
      .fn()
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce(job(9, body.name))
    await expect(getOrStartDraftCreate(body, start)).rejects.toThrow('network')
    await expect(getOrStartDraftCreate(body, start)).resolves.toMatchObject({ id: 9 })
    expect(start).toHaveBeenCalledTimes(2)
  })

  it('treats a different draft object as a separate create', async () => {
    const a = draft('a')
    const b = draft('b')
    const startA = vi.fn(async () => job(1, 'a'))
    const startB = vi.fn(async () => job(2, 'b'))
    await getOrStartDraftCreate(a, startA)
    await getOrStartDraftCreate(b, startB)
    expect(startA).toHaveBeenCalledTimes(1)
    expect(startB).toHaveBeenCalledTimes(1)
  })
})
