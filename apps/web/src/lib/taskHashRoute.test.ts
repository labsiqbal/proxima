import { describe, expect, it } from 'vitest'
import {
  taskHashPreservesWorkProject,
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
})
