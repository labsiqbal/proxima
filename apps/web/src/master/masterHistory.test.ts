import { describe, expect, it } from 'vitest'
import { projectMasterHistory } from './masterHistory'

const messages = [
  { id: 1, role: 'system' as const, content: 'Master Focus changed to Container 10.', message_focus: { focus_epoch_id: 1, focus_container_id: 10, subject_container_id: null } },
  { id: 2, role: 'user' as const, content: 'A secret only', message_focus: { focus_epoch_id: 1, focus_container_id: 10, subject_container_id: null } },
  { id: 3, role: 'assistant' as const, content: 'A response only', message_focus: { focus_epoch_id: 1, focus_container_id: 10, subject_container_id: null } },
  { id: 4, role: 'user' as const, content: 'Fleet request', message_focus: { focus_epoch_id: null, focus_container_id: null, subject_container_id: null } },
  { id: 5, role: 'assistant' as const, content: 'A system event', message_focus: { focus_epoch_id: null, focus_container_id: null, subject_container_id: 10 } },
  { id: 6, role: 'user' as const, content: 'B secret only', message_focus: { focus_epoch_id: 2, focus_container_id: 20, subject_container_id: null } },
  { id: 7, role: 'assistant' as const, content: 'B task update', message_focus: { focus_epoch_id: 2, focus_container_id: 20, subject_container_id: 20 } },
]

describe('projectMasterHistory', () => {
  it('keeps the roving thread exact and ordered without duplicate copies', () => {
    const result = projectMasterHistory(messages, { kind: 'roving' })
    expect(result.map(message => message.id)).toEqual([1, 2, 3, 4, 5, 6, 7])
    expect(new Set(result.map(message => message.id)).size).toBe(result.length)
  })

  it('shows one Container its Focused segment plus subject-attributed system updates only', () => {
    const result = projectMasterHistory(messages, { kind: 'container', containerId: 10 })
    expect(result.map(message => message.id)).toEqual([1, 2, 3, 5])
    expect(result.map(message => message.historyKind)).toEqual([
      'focus-boundary', 'focused-segment', 'focused-segment', 'system-event',
    ])
    expect(result.map(message => message.content).join(' ')).not.toContain('B secret')
  })

  it('keeps Fleet history free of Container subject events', () => {
    const result = projectMasterHistory(messages, { kind: 'fleet' })
    expect(result.map(message => message.id)).toEqual([4])
    expect(result.map(message => message.content).join(' ')).not.toContain('secret')
  })

  it('requires positive Fleet attribution instead of treating erased scope as Fleet', () => {
    const result = projectMasterHistory([
      {
        id: 8,
        role: 'assistant',
        content: 'Deleted Container response',
        message_focus: {
          focus_epoch_id: 3,
          focus_container_id: null,
          subject_container_id: null,
        },
      },
      {
        id: 9,
        role: 'assistant',
        content: 'Unattributed legacy response',
      },
    ], { kind: 'fleet' })

    expect(result).toEqual([])
  })
})
