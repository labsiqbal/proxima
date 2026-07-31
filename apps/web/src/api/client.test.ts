import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './client'

describe('api error fields', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('preserves a structured API error field', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: { message: 'slug already exists', field: 'name' } }),
      { status: 409, headers: { 'Content-Type': 'application/json' } },
    )))

    await expect(api('/api/projects/link', 'token', { method: 'POST' }))
      .rejects.toMatchObject({
        status: 409,
        field: 'name',
        detail: 'slug already exists',
        message: expect.stringContaining('slug already exists'),
      })
  })

  it('derives the field from FastAPI validation locations', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({
        detail: [{ loc: ['body', 'name'], msg: 'String should have at most 120 characters' }],
      }),
      { status: 422, headers: { 'Content-Type': 'application/json' } },
    )))

    await expect(api('/api/projects/link', 'token', { method: 'POST' }))
      .rejects.toMatchObject({
        status: 422,
        field: 'name',
        message: expect.stringContaining('at most 120 characters'),
      })
  })
})
