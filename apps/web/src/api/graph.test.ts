import { afterEach, describe, expect, it, vi } from 'vitest'
import { startGraphJob } from './graph'

describe('startGraphJob', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('always sends a JSON body, including when no manual input is provided', async () => {
    const payload = JSON.stringify({
      id: 7,
      title: 'Draft',
      status: 'running',
      engine: 'graph',
      graph: { nodes: [], edges: [] },
      node_states: [],
    })
    const fetchMock = vi.fn().mockImplementation(async () => new Response(payload, {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await startGraphJob('token', 7)
    await startGraphJob('token', 7, { campaign: 'Launch' })

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/graph/jobs/7/start', expect.objectContaining({
      method: 'POST',
      body: '{}',
      headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
    }))
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/graph/jobs/7/start', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ input: { campaign: 'Launch' } }),
    }))
  })
})
