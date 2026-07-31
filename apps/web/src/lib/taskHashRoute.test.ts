import { describe, expect, it } from 'vitest'
import {
  nextPreserveWorkTaskContext,
  taskHashPreservesWorkProject,
  withInAppTaskPolicy,
  withResolvedTaskOwnership,
  withoutTaskPolicy,
} from './taskHashRoute'

describe('task hash Work Project policy', () => {
  it('preserves Work for in-app history entries on every sync event', () => {
    expect(taskHashPreservesWorkProject(false, {
      proximaView: 'task',
      proximaTaskPolicy: 'preserve-work',
    })).toBe(true)
  })

  it('keeps cold permalink adopt-and-lock when policy is absent', () => {
    expect(taskHashPreservesWorkProject(true, {
      proximaTaskPolicy: 'preserve-work',
    })).toBe(false)
    expect(taskHashPreservesWorkProject(false, { proximaView: 'task' })).toBe(false)
    expect(taskHashPreservesWorkProject(false, null)).toBe(false)
  })

  it('clears preserve-work when leaving a Task so later hash opens stay cold', () => {
    expect(withoutTaskPolicy({
      proximaView: 'activity',
      proximaTaskPolicy: 'preserve-work',
      other: 1,
    })).toEqual({
      proximaView: 'activity',
      other: 1,
    })
    expect(withoutTaskPolicy(null)).toEqual({})
  })

  it('restamps preserve-work when returning from Task-linked Design', () => {
    const restored = withInAppTaskPolicy({
      proximaView: 'design',
      other: 1,
    })
    expect(restored).toEqual({
      proximaView: 'task',
      proximaTaskPolicy: 'preserve-work',
      other: 1,
    })
    expect(taskHashPreservesWorkProject(false, restored)).toBe(true)
    expect(taskHashPreservesWorkProject(false, withInAppTaskPolicy(null))).toBe(true)
  })

  it('keeps resolved ownership when the same preserve-work Task is restored', () => {
    const job = { id: 7, project_slug: 'beacon' }
    const resolved = withResolvedTaskOwnership(
      { jobId: 7, projectSlug: null, initialJob: null },
      job,
    )
    expect(resolved).toEqual({
      jobId: 7,
      projectSlug: 'beacon',
      initialJob: job,
    })

    const restored = nextPreserveWorkTaskContext(resolved, 7)
    expect(restored).toBe(resolved)
    expect(withResolvedTaskOwnership(restored, { ...job, title: 'later' })).toBe(restored)
  })

  it('re-resolves when preserve-work lands on a different Task', () => {
    const prior = {
      jobId: 7,
      projectSlug: 'beacon',
      initialJob: { id: 7, project_slug: 'beacon' },
    }
    expect(nextPreserveWorkTaskContext(prior, 8)).toEqual({
      jobId: 8,
      projectSlug: null,
      initialJob: null,
    })
    expect(withResolvedTaskOwnership(prior, { id: 8, project_slug: 'atlas' })).toBe(prior)
  })
})
