import type { MasterDesk, MasterJob } from '../api/master'
import { SESSION_EVENT_TYPES } from '../lib/eventTypes'
import type { ChatMessage, RunEvent } from '../types'

export type MasterViewMessage = ChatMessage & {
  clientId?: string
  pending?: boolean
}

export type MasterEventProjection = {
  desk: MasterDesk
  messages: MasterViewMessage[]
  insertedMessageId: number | null
}

const MASTER_PROJECTION_TYPES = new Set(
  SESSION_EVENT_TYPES.filter(type => type.startsWith('master.')),
)

const MASTER_TASK_STATUS: Record<string, MasterJob['status']> = {
  'master.task.started': 'running',
  'master.task.review_ready': 'review',
  'master.task.completed': 'done',
  'master.task.failed': 'failed',
  'master.task.cancelled': 'cancelled',
  'master.task.blocked': 'queued',
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function positiveInteger(value: unknown): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0
    ? value
    : null
}

function safeProjectionContent(
  type: string,
  payload: Record<string, unknown>,
): string | null {
  const taskId = positiveInteger(payload.task_id)
  if (type === 'master.task.review_ready' && taskId != null) {
    const checkpointId = positiveInteger(payload.checkpoint_id)
    return `Task #${taskId} is ready for review.${checkpointId == null ? '' : ` Checkpoint #${checkpointId} is available.`}`
  }
  const taskLabels: Record<string, string> = {
    'master.task.started': 'Started',
    'master.task.completed': 'Completed',
    'master.task.failed': 'Failed',
    'master.task.cancelled': 'Cancelled',
  }
  if (taskLabels[type] && taskId != null) {
    return `${taskLabels[type]} Task #${taskId}.`
  }
  if (type === 'master.task.blocked' && taskId != null) {
    return `Task #${taskId} is blocked by a prerequisite.`
  }
  const satpamLabels: Record<string, string> = {
    'master.satpam.steered': 'steered',
    'master.satpam.restart_queued': 'needs approval to restart',
    'master.satpam.restarted': 'restarted',
    'master.satpam.escalated': 'escalated',
  }
  if (satpamLabels[type] && taskId != null) {
    return `Satpam ${satpamLabels[type]} Task #${taskId}.`
  }
  if (type === 'master.satpam.recovery_failed' && taskId != null) {
    return `Satpam could not complete the approved recovery for Task #${taskId}.`
  }
  const attentionKind = payload.attention_kind
  if (attentionKind === 'permission_job' && taskId != null) {
    return `Task #${taskId} needs an owner permission decision.`
  }
  if (attentionKind === 'master_budget') {
    return 'Master unattended work stopped at its configured budget.'
  }
  if (taskId != null) {
    return `Master needs an owner decision for Task #${taskId}.`
  }
  if (
    type === 'master.attention.required'
    || type === 'master.supervisor.outcome'
  ) {
    return 'Master needs an owner decision.'
  }
  return null
}

export function activeMasterRun(
  run: MasterDesk['master_run'],
): { id: number; status: string } | null {
  return run && ['queued', 'running'].includes(run.status) ? run : null
}

export function isMasterProjectionEvent(type: string): boolean {
  return MASTER_PROJECTION_TYPES.has(type as never)
}

export function parseMasterStreamEvent(value: string): RunEvent | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(value)
  } catch {
    return null
  }
  if (!isRecord(parsed)) return null
  if (
    !Number.isSafeInteger(parsed.id)
    || Number(parsed.id) <= 0
    || !Number.isSafeInteger(parsed.seq)
    || Number(parsed.seq) < 0
    || !Number.isSafeInteger(parsed.run_id)
    || Number(parsed.run_id) < 0
    || !Number.isSafeInteger(parsed.session_id)
    || Number(parsed.session_id) <= 0
    || typeof parsed.type !== 'string'
    || !parsed.type
    || !isRecord(parsed.payload)
    || typeof parsed.created_at !== 'string'
  ) return null
  return parsed as RunEvent
}

export function orderMasterMessages(
  messages: MasterViewMessage[],
): MasterViewMessage[] {
  return messages.slice().sort((left, right) => {
    if (left.id != null && right.id != null) return left.id - right.id
    if (left.id != null) return -1
    if (right.id != null) return 1
    return 0
  })
}

export function mergeMasterMessageSnapshot(
  snapshot: ChatMessage[],
  current: MasterViewMessage[],
): MasterViewMessage[] {
  const canonicalIds = new Set(
    snapshot.flatMap(message => message.id == null ? [] : [message.id]),
  )
  const canonicalRuns = new Set(
    snapshot.flatMap(message => (
      message.role === 'user' && message.run_id != null ? [message.run_id] : []
    )),
  )
  const extras = current.filter(message => {
    if (message.id != null) return !canonicalIds.has(message.id)
    if (message.run_id != null && canonicalRuns.has(message.run_id)) return false
    return true
  })
  return orderMasterMessages([...snapshot, ...extras])
}

function updateProjectedJob(
  desk: MasterDesk,
  event: RunEvent,
  payload: Record<string, unknown>,
): MasterDesk {
  const status = MASTER_TASK_STATUS[event.type]
  const taskId = positiveInteger(payload.task_id)
  if (!status || taskId == null) return desk
  const index = desk.jobs.findIndex(job => job.id === taskId)
  if (index < 0) {
    const now = event.created_at
    const projected: MasterJob = {
      id: taskId,
      project_id: positiveInteger(payload.container_id),
      project_slug: typeof payload.container_slug === 'string'
        ? payload.container_slug
        : null,
      project_name: typeof payload.container_slug === 'string'
        ? payload.container_slug
        : null,
      workflow_id: null,
      session_id: desk.session.id,
      origin_master_session_id: desk.session.id,
      title: `Task #${taskId}`,
      status,
      desk_status: status,
      run_status: status === 'running' ? 'running' : null,
      engine: 'linear',
      current_step_idx: 0,
      input: {},
      steps_state: [],
      schedule_id: null,
      created_by: null,
      created_at: now,
      updated_at: now,
      started_at: status === 'running' ? now : null,
      finished_at: ['done', 'failed', 'cancelled'].includes(status) ? now : null,
      archived_at: null,
      blocked_reason: event.type === 'master.task.blocked'
        ? 'Waiting for a prerequisite'
        : null,
    }
    return { ...desk, jobs: [projected, ...desk.jobs] }
  }
  const jobs = desk.jobs.slice()
  jobs[index] = {
    ...jobs[index],
    status,
    desk_status: status,
    run_status: status === 'running' ? 'running' : jobs[index].run_status,
    blocked_reason: event.type === 'master.task.blocked'
      ? jobs[index].blocked_reason || 'Waiting for a prerequisite'
      : null,
    updated_at: event.created_at,
    finished_at: ['done', 'failed', 'cancelled'].includes(status)
      ? event.created_at
      : jobs[index].finished_at,
  }
  return { ...desk, jobs }
}

export function projectMasterEvent(
  desk: MasterDesk,
  messages: MasterViewMessage[],
  event: RunEvent,
): MasterEventProjection {
  let nextDesk = desk
  let nextMessages = messages
  let insertedMessageId: number | null = null
  const activeRun = activeMasterRun(desk.master_run)

  if (event.type === 'run.queued' || event.type === 'run.started') {
    if (!activeRun || event.run_id >= activeRun.id) {
      nextDesk = {
        ...desk,
        master_run: {
          id: event.run_id,
          status: event.type === 'run.queued' ? 'queued' : 'running',
        },
      }
    }
  } else if (
    event.type === 'run.completed'
    || event.type === 'run.failed'
    || event.type === 'run.cancelled'
  ) {
    if (activeRun?.id === event.run_id) {
      nextDesk = { ...desk, master_run: null }
    }
  } else if (event.type === 'message.complete') {
    const payload = isRecord(event.payload) ? event.payload : {}
    const messageId = positiveInteger(payload.message_id)
    const text = typeof payload.text === 'string' ? payload.text : ''
    if (
      messageId != null
      && text
      && !messages.some(message => message.id === messageId)
    ) {
      insertedMessageId = messageId
      nextMessages = orderMasterMessages([...messages, {
        id: messageId,
        role: 'assistant',
        author: 'Master',
        content: text,
        run_id: event.run_id,
        created_at: event.created_at,
      }])
    }
  } else if (isMasterProjectionEvent(event.type)) {
    const payload = isRecord(event.payload) ? event.payload : {}
    const messageId = positiveInteger(payload.message_id)
    const content = safeProjectionContent(event.type, payload)
    if (
      messageId != null
      && content
      && !messages.some(message => message.id === messageId)
    ) {
      insertedMessageId = messageId
      nextMessages = orderMasterMessages([...messages, {
        id: messageId,
        role: 'assistant',
        author: 'Master',
        content,
        run_id: null,
        created_at: event.created_at,
      }])
    }
    nextDesk = updateProjectedJob(nextDesk, event, payload)
  }

  return {
    desk: nextDesk.event_cursor >= event.id
      ? nextDesk
      : { ...nextDesk, event_cursor: event.id },
    messages: nextMessages,
    insertedMessageId,
  }
}

export function projectMasterSnapshot(
  snapshotDesk: MasterDesk,
  snapshotMessages: ChatMessage[],
  events: readonly RunEvent[],
): MasterEventProjection {
  let projection: MasterEventProjection = {
    desk: snapshotDesk,
    messages: orderMasterMessages(snapshotMessages),
    insertedMessageId: null,
  }
  const seenEventIds = new Set<number>()

  for (const event of [...events].sort((left, right) => left.id - right.id)) {
    if (
      !Number.isSafeInteger(event.id)
      || event.id <= 0
      || seenEventIds.has(event.id)
      || event.session_id !== snapshotDesk.session.id
    ) continue
    seenEventIds.add(event.id)
    projection = projectMasterEvent(
      projection.desk,
      projection.messages,
      event,
    )
  }

  return projection
}
