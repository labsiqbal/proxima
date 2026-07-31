import { describe, expect, it } from 'vitest'
import { projectMasterEvent, type MasterEventProjection } from './masterProjection'
import type { MasterDesk } from '../api/master'

const desk = {
  event_cursor: 0,
  jobs: [],
  decisions: [],
  attention: [],
  master_run: null,
} as unknown as MasterDesk

describe('masterProjection decision audit text', () => {
  it('uses owner deferred/resolved copy for live decision events', () => {
    const deferred = projectMasterEvent(desk, [], {
      id: 11,
      seq: 1,
      run_id: 0,
      type: 'master.decision.deferred',
      created_at: '2026-01-01T00:00:00Z',
      payload: {
        message_id: 31,
        decision_id: 8,
        task_id: 4,
        focus_epoch_id: null,
        focus_container_id: null,
        subject_container_id: null,
        closed_without_owner_response: false,
      },
    } as never) as MasterEventProjection
    expect(deferred.messages.at(-1)?.content).toBe(
      'Owner deferred decision #8 for Task #4.',
    )

    const resolved = projectMasterEvent(desk, deferred.messages, {
      id: 12,
      seq: 2,
      run_id: 0,
      type: 'master.decision.resolved',
      created_at: '2026-01-01T00:01:00Z',
      payload: {
        message_id: 32,
        decision_id: 8,
        task_id: 4,
        focus_epoch_id: null,
        focus_container_id: null,
        subject_container_id: null,
        closed_without_owner_response: false,
      },
    } as never) as MasterEventProjection
    expect(resolved.messages.at(-1)?.content).toBe(
      'Owner resolved decision #8 for Task #4. The Task is continuing.',
    )

    const settled = projectMasterEvent(desk, [], {
      id: 13,
      seq: 3,
      run_id: 0,
      type: 'master.decision.resolved',
      created_at: '2026-01-01T00:02:00Z',
      payload: {
        message_id: 33,
        decision_id: 9,
        task_id: 5,
        focus_epoch_id: null,
        focus_container_id: null,
        subject_container_id: null,
        closed_without_owner_response: true,
      },
    } as never) as MasterEventProjection
    expect(settled.messages.at(-1)?.content).toBe(
      'Decision #9 for Task #5 was closed because the Task left review.',
    )
  })
})
