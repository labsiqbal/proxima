import assert from 'node:assert/strict'
import { test } from 'vitest'
import { normalizeMasterDesk } from './master'

test('legacy Alpha desk payloads normalize to canonical Master ownership', () => {
  const payload = normalizeMasterDesk({
    session: { id: 7, title: 'Alpha', runner_id: 'hermes', visibility: 'private', mode: 'alpha' },
    alpha_run: { id: 8, status: 'done' },
    backing_runner: 'hermes',
    jobs: [{
      id: 9,
      project_id: null,
      workflow_id: null,
      session_id: 10,
      title: 'Preserved Task',
      status: 'done',
      current_step_idx: 0,
      input: {},
      steps_state: [],
      schedule_id: null,
      created_by: 1,
      created_at: '2026-01-01',
      updated_at: '2026-01-01',
      started_at: null,
      finished_at: null,
      archived_at: null,
      desk_status: 'done',
      alpha_session_id: 7,
    }],
    unattended: false,
    budgets: {
      unattended: false,
      budget_turns: 20,
      budget_wall_seconds: 3600,
      budget_tokens: null,
      tour_core_done: false,
    },
    capacity: { running: 0, max: 3, free: 3, queued: 0 },
    attention: [{
      id: 'attention:1',
      kind: 'alpha_budget',
      title: 'Legacy',
      target: { view: 'alpha', alpha_session_id: 7 },
      inline_ok: false,
      actions: [],
      status: 'open',
    }],
    checkpoints: [],
  })

  assert.equal(payload.session.id, 7)
  assert.equal(payload.session.mode, 'master')
  assert.deepEqual(payload.master_run, { id: 8, status: 'done' })
  assert.equal('alpha_run' in payload, false)
  assert.equal(payload.jobs[0].origin_master_session_id, 7)
  assert.equal('alpha_session_id' in payload.jobs[0], false)
  assert.equal(payload.attention[0].target.view, 'master')
  assert.equal(payload.attention[0].target.origin_master_session_id, 7)
  assert.equal('alpha_session_id' in payload.attention[0].target, false)
  assert.equal(payload.attention[0].kind, 'master_budget')
})
