import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from './client'

describe('api client error rendering', () => {
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

  it('prefers structured detail.message over raw JSON blobs', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      text: async () => JSON.stringify({
        detail: {
          message: 'Project processes are still active; stop them before retrying.',
          active_processes: 2,
          unresolved_processes: 1,
          migration: { phase: 'attention' },
        },
      }),
    }))

    await expect(api('/api/projects/demo/ops-migration/retry', 'token', { method: 'POST' }))
      .rejects
      .toMatchObject({
        status: 409,
        message: expect.stringContaining(
          'Project processes are still active; stop them before retrying.',
        ),
      })

    try {
      await api('/api/projects/demo/ops-migration/retry', 'token', { method: 'POST' })
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError)
      const message = (error as ApiError).message
      expect(message).toContain('Active processes: 2.')
      expect(message).toContain('Unverified processes: 1.')
      expect(message).not.toContain('"migration"')
      expect(message).not.toContain('{')
    }
  })
})

// --- Actionable fail-closed refusals (prune B5, #133) ------------------------
describe('structured refusals keep the next step last', () => {
  it('puts the process counts between the diagnosis and the instruction', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      text: async () => JSON.stringify({
        detail: {
          code: 'purge_activity_blocked',
          message: 'This project still has processes running. Stop this project\'s running work, then try again.',
          next_step: 'Stop this project\'s running work, then try again.',
          active_processes: 2,
          unresolved_processes: 1,
        },
      }),
    }))

    try {
      await api('/api/projects/demo', 'token', { method: 'DELETE' })
      throw new Error('expected a refusal')
    } catch (error) {
      const apiError = error as ApiError
      expect(apiError.detail).toBe(
        'This project still has processes running. Active processes: 2. '
        + 'Unverified processes: 1. Stop this project\'s running work, then try again.',
      )
      expect(apiError.nextStep).toBe('Stop this project\'s running work, then try again.')
    }
  })
})
