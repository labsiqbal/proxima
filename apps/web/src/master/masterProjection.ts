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
  'master.task.recovered': 'queued',
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function positiveInteger(value: unknown): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0
    ? value
    : null
}

function attributedId(value: unknown): number | null | undefined {
  if (value === null) return null
  return positiveInteger(value) ?? undefined
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
  if (type === 'master.task.recovered' && taskId != null) {
    const checkpointId = positiveInteger(payload.checkpoint_id)
    const actor = isRecord(payload.actor)
      && typeof payload.actor.username === 'string'
      ? payload.actor.username
      : null
    const prior = typeof payload.prior_status === 'string'
      ? payload.prior_status
      : null
    const restored = typeof payload.restored_status === 'string'
      ? payload.restored_status
      : null
    const discarded = Array.isArray(payload.discarded_progress)
      && payload.discarded_progress.every(item => typeof item === 'string')
      ? payload.discarded_progress
      : null
    const conflicting = Array.isArray(payload.conflicting_progress)
      && payload.conflicting_progress.every(item => typeof item === 'string')
      ? payload.conflicting_progress
      : null
    if (
      checkpointId == null
      || actor == null
      || prior == null
      || restored == null
      || discarded == null
      || conflicting == null
    ) return null
    const title = (value: string) => (
      value ? `${value[0].toUpperCase()}${value.slice(1)}` : value
    )
    const discardedText = discarded.length
      ? `Discarded progress: ${discarded.join('; ')}.`
      : 'No later progress was discarded.'
    const conflictText = conflicting.length
      ? `Conflicting progress: ${conflicting.join('; ')}.`
      : 'No conflicting progress was present.'
    return `${actor} restored Task #${taskId} from checkpoint #${checkpointId}: ${title(prior)} to ${title(restored)}. ${discardedText} ${conflictText}`
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
  const runId = parsed.run_id == null ? 0 : parsed.run_id
  if (
    !Number.isSafeInteger(parsed.id)
    || Number(parsed.id) <= 0
    || !Number.isSafeInteger(parsed.seq)
    || Number(parsed.seq) < 0
    || !Number.isSafeInteger(runId)
    || Number(runId) < 0
    || !Number.isSafeInteger(parsed.session_id)
    || Number(parsed.session_id) <= 0
    || typeof parsed.type !== 'string'
    || !parsed.type
    || !isRecord(parsed.payload)
    || typeof parsed.created_at !== 'string'
  ) return null
  return { ...parsed, run_id: runId } as RunEvent
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
  const currentById = new Map<number, MasterViewMessage>()
  for (const message of current) {
    if (message.id != null) currentById.set(message.id, message)
  }
  const pendingSends = current.filter(message => (
    message.role === 'user'
    && message.id == null
    && message.pending === true
    && message.clientId != null
  ))
  const claimed = new Set<string>()
  const merged: MasterViewMessage[] = snapshot.map(message => {
    if (message.id != null && currentById.has(message.id)) {
      const existing = currentById.get(message.id)
      if (existing?.clientId != null) {
        return { ...message, clientId: existing.clientId }
      }
      return message
    }
    if (message.role === 'user' && message.id != null) {
      const pending = pendingSends.find(candidate => (
        candidate.clientId != null
        && !claimed.has(candidate.clientId)
        && candidate.content === message.content
      ))
      if (pending?.clientId != null) {
        claimed.add(pending.clientId)
        return { ...message, clientId: pending.clientId, pending: false }
      }
    }
    return message
  })
  const extras = current.filter(message => {
    if (message.id != null) return !canonicalIds.has(message.id)
    if (message.run_id != null && canonicalRuns.has(message.run_id)) return false
    if (message.clientId != null && claimed.has(message.clientId)) return false
    return true
  })
  return orderMasterMessages([...merged, ...extras])
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
    run_status: status === 'running' ? 'running' : null,
    blocked_reason: event.type === 'master.task.blocked'
      ? jobs[index].blocked_reason || 'Waiting for a prerequisite'
      : null,
    updated_at: event.created_at,
    finished_at: ['done', 'failed', 'cancelled'].includes(status)
      ? event.created_at
      : null,
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
        message_focus: {
          focus_epoch_id: desk.focus.current_epoch_id,
          focus_container_id: desk.focus.current_container_id,
          subject_container_id: null,
        },
      }])
    }
  } else if (event.type === 'master.focus.changed') {
    const payload = isRecord(event.payload) ? event.payload : {}
    const messageId = positiveInteger(payload.message_id)
    const epochId = payload.focus_epoch_id == null
      ? null
      : positiveInteger(payload.focus_epoch_id)
    const containerId = payload.container_id == null
      ? null
      : positiveInteger(payload.container_id)
    const version = typeof payload.version === 'number'
      && Number.isSafeInteger(payload.version)
      && payload.version >= 0
        ? payload.version
        : null
    if (
      messageId != null
      && version != null
      && (payload.focus_epoch_id == null || epochId != null)
      && (payload.container_id == null || containerId != null)
    ) {
      if (version >= nextDesk.focus.version) {
        nextDesk = {
          ...nextDesk,
          focus: {
            current_epoch_id: epochId,
            current_container_id: containerId,
            pending_container_id: null,
            pending: false,
            version,
          },
        }
      }
      if (!messages.some(message => message.id === messageId)) {
        insertedMessageId = messageId
        nextMessages = orderMasterMessages([...messages, {
          id: messageId,
          role: 'system',
          author: 'Proxima',
          content: containerId == null
            ? 'Master Focus changed to Fleet mode.'
            : `Master Focus changed to Container ${containerId}.`,
          run_id: null,
          created_at: event.created_at,
          message_focus: {
            focus_epoch_id: epochId,
            focus_container_id: containerId,
            subject_container_id: null,
          },
        }])
      }
    }
  } else if (isMasterProjectionEvent(event.type)) {
    const payload = isRecord(event.payload) ? event.payload : {}
    const messageId = positiveInteger(payload.message_id)
    const content = safeProjectionContent(event.type, payload)
    const focusEpochId = attributedId(payload.focus_epoch_id)
    const focusContainerId = attributedId(payload.focus_container_id)
    const subjectContainerId = attributedId(payload.subject_container_id)
    if (
      messageId != null
      && content
      && focusEpochId !== undefined
      && focusContainerId !== undefined
      && subjectContainerId !== undefined
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
        message_focus: {
          focus_epoch_id: focusEpochId,
          focus_container_id: focusContainerId,
          subject_container_id: subjectContainerId,
        },
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
