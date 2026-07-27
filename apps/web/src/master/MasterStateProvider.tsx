import React from 'react'
import {
  getMasterDesk,
  saveMasterSettings,
  sendMasterMessage,
  type MasterDesk,
  type MasterJob,
  type MasterSettings,
} from '../api/master'
import { listEvents } from '../api/runs'
import { listMessages } from '../api/sessions'
import { SESSION_EVENT_TYPES } from '../lib/eventTypes'
import type { ChatMessage, RunEvent } from '../types'

export type MasterConnectionState =
  | 'feature-off'
  | 'connecting'
  | 'connected'
  | 'retrying'
  | 'disconnected'

export type MasterViewMessage = ChatMessage & {
  clientId?: string
  pending?: boolean
}

export type MasterComposerSelection = { start: number; end: number }

export type MasterFutureState = {
  focus: {
    mode: 'fleet'
    containerId: null
    pendingContainerId: null
    durable: false
  }
  target: {
    mode: 'auto'
    containerId: null
    areaId: null
    enabled: false
  }
  toastQueue: readonly []
  popup: {
    open: false
    presentation: 'closed'
    preferredCorner: 'right'
    enabled: false
  }
}

export type MasterStateValue = {
  enabled: boolean
  loading: boolean
  desk: MasterDesk | null
  messages: MasterViewMessage[]
  activeRun: { id: number; status: string } | null
  connection: {
    state: MasterConnectionState
    resumeCursor: number
    reconnectCount: number
    error: string
  }
  unread: {
    count: number
  }
  composer: {
    draft: string
    selection: MasterComposerSelection
    sending: boolean
    error: string
    focusRequest: number
  }
  view: {
    homeActive: boolean
    sideCollapsed: boolean
    scrollTop: number
    followTail: boolean
    anchorMessageId: number | null
  }
  future: MasterFutureState
  actions: {
    setDraft: (draft: string) => void
    setSelection: (selection: MasterComposerSelection) => void
    seedDraft: (draft: string) => void
    send: (content?: string) => Promise<void>
    setHomeActive: (active: boolean) => void
    markRead: () => void
    setSideCollapsed: (collapsed: boolean) => void
    setScrollState: (state: {
      scrollTop: number
      followTail: boolean
      anchorMessageId: number | null
    }) => void
    refresh: () => Promise<void>
    updateSettings: (settings: Partial<MasterSettings>) => Promise<void>
    clearError: () => void
  }
}

const MasterStateContext = React.createContext<MasterStateValue | null>(null)

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

const FUTURE_STATE: MasterFutureState = {
  focus: {
    mode: 'fleet',
    containerId: null,
    pendingContainerId: null,
    durable: false,
  },
  target: {
    mode: 'auto',
    containerId: null,
    areaId: null,
    enabled: false,
  },
  toastQueue: [],
  popup: {
    open: false,
    presentation: 'closed',
    preferredCorner: 'right',
    enabled: false,
  },
}

const SIDE_COLLAPSED_KEY = 'proxima.master.sideCollapsed'
const RECONCILE_THROTTLE_MS = 1000
const EVENT_DEDUPE_LIMIT = 2000

function rememberEventIds(
  current: ReadonlySet<number>,
  events: readonly RunEvent[],
): Set<number> {
  const next = new Set(current)
  for (const event of events) next.add(event.id)
  if (next.size <= EVENT_DEDUPE_LIMIT * 2) return next
  return new Set(
    [...next].sort((a, b) => b - a).slice(0, EVENT_DEDUPE_LIMIT),
  )
}

function readSideCollapsed(): boolean {
  if (typeof localStorage === 'undefined') return false
  const stored = localStorage.getItem(SIDE_COLLAPSED_KEY)
  if (stored === '1') return true
  if (stored === '0') return false
  try {
    return Boolean(window.matchMedia?.('(max-width: 900px)').matches)
  } catch {
    return false
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function positiveInteger(value: unknown): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0
    ? value
    : null
}

function activeMasterRun(
  run: MasterDesk['master_run'],
): { id: number; status: string } | null {
  return run && ['queued', 'running'].includes(run.status) ? run : null
}

function safeProjectionContent(type: string, payload: Record<string, unknown>): string | null {
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
  if (taskLabels[type] && taskId != null) return `${taskLabels[type]} Task #${taskId}.`
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
  if (taskId != null) return `Master needs an owner decision for Task #${taskId}.`
  if (type === 'master.attention.required' || type === 'master.supervisor.outcome') {
    return 'Master needs an owner decision.'
  }
  return null
}

function mergeMessageSnapshot(
  snapshot: ChatMessage[],
  current: MasterViewMessage[],
): MasterViewMessage[] {
  const canonicalIds = new Set(snapshot.flatMap(message => message.id == null ? [] : [message.id]))
  const canonicalRuns = new Set(
    snapshot.flatMap(message => message.role === 'user' && message.run_id != null ? [message.run_id] : []),
  )
  const extras = current.filter(message => {
    if (message.id != null) return !canonicalIds.has(message.id)
    if (message.run_id != null && canonicalRuns.has(message.run_id)) return false
    return true
  })
  return [...snapshot, ...extras]
}

function updateProjectedJob(
  desk: MasterDesk | null,
  event: RunEvent,
): MasterDesk | null {
  if (!desk) return desk
  const status = MASTER_TASK_STATUS[event.type]
  const taskId = positiveInteger(event.payload.task_id)
  if (!status || taskId == null) return desk
  const index = desk.jobs.findIndex(job => job.id === taskId)
  if (index < 0) {
    const now = event.created_at
    const projected: MasterJob = {
      id: taskId,
      project_id: positiveInteger(event.payload.container_id),
      project_slug: typeof event.payload.container_slug === 'string'
        ? event.payload.container_slug
        : null,
      project_name: typeof event.payload.container_slug === 'string'
        ? event.payload.container_slug
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

function MasterStateHost({
  token,
  ownerId,
  enabled,
  children,
}: {
  token: string
  ownerId: number
  enabled: boolean
  children: React.ReactNode
}) {
  const [loading, setLoading] = React.useState(enabled)
  const [desk, setDesk] = React.useState<MasterDesk | null>(null)
  const [messages, setMessages] = React.useState<MasterViewMessage[]>([])
  const [activeRun, setActiveRun] = React.useState<{ id: number; status: string } | null>(null)
  const [connectionState, setConnectionState] = React.useState<MasterConnectionState>(
    enabled ? 'connecting' : 'feature-off',
  )
  const [resumeCursor, setResumeCursor] = React.useState(0)
  const [reconnectCount, setReconnectCount] = React.useState(0)
  const [connectionError, setConnectionError] = React.useState('')
  const [unreadCount, setUnreadCount] = React.useState(0)
  const [draft, setDraftState] = React.useState('')
  const [selection, setSelection] = React.useState<MasterComposerSelection>({ start: 0, end: 0 })
  const [sending, setSending] = React.useState(false)
  const [sendError, setSendError] = React.useState('')
  const [focusRequest, setFocusRequest] = React.useState(0)
  const [homeActive, setHomeActiveState] = React.useState(false)
  const [sideCollapsed, setSideCollapsedState] = React.useState(readSideCollapsed)
  const [scrollState, setScrollStateValue] = React.useState({
    scrollTop: 0,
    followTail: true,
    anchorMessageId: null as number | null,
  })

  const generationRef = React.useRef(0)
  const deskRef = React.useRef<MasterDesk | null>(null)
  const cursorRef = React.useRef(0)
  const eventIdsRef = React.useRef(new Set<number>())
  const runSeqRef = React.useRef(new Map<number, number>())
  const sourceRef = React.useRef<EventSource | null>(null)
  const lifecycleAbortRef = React.useRef<AbortController | null>(null)
  const sendAbortRef = React.useRef<AbortController | null>(null)
  const settingsAbortRef = React.useRef<AbortController | null>(null)
  const reconcileAbortRef = React.useRef<AbortController | null>(null)
  const reconcilePromiseRef = React.useRef<Promise<void> | null>(null)
  const reconcileRequestedAtRef = React.useRef(0)
  const sendLockRef = React.useRef(false)
  const tempMessageIdRef = React.useRef(0)
  const homeActiveRef = React.useRef(false)
  const handleEventRef = React.useRef<(event: RunEvent) => void>(() => {})
  const reconcileRef = React.useRef<(reason?: string, afterId?: number) => Promise<void>>(
    async () => {},
  )

  React.useEffect(() => { deskRef.current = desk }, [desk])
  React.useEffect(() => { homeActiveRef.current = homeActive }, [homeActive])

  const setCursor = React.useCallback((cursor: number) => {
    cursorRef.current = Math.max(cursorRef.current, cursor)
    setResumeCursor(cursorRef.current)
  }, [])

  const clearOwnedState = React.useCallback((nextEnabled: boolean) => {
    deskRef.current = null
    cursorRef.current = 0
    eventIdsRef.current = new Set()
    runSeqRef.current = new Map()
    sendLockRef.current = false
    setDesk(null)
    setMessages([])
    setActiveRun(null)
    setLoading(nextEnabled)
    setConnectionState(nextEnabled ? 'connecting' : 'feature-off')
    setResumeCursor(0)
    setReconnectCount(0)
    setConnectionError('')
    setUnreadCount(0)
    setDraftState('')
    setSelection({ start: 0, end: 0 })
    setSending(false)
    setSendError('')
    setHomeActiveState(false)
    homeActiveRef.current = false
    setScrollStateValue({ scrollTop: 0, followTail: true, anchorMessageId: null })
  }, [])

  const reconcile = React.useCallback(async (
    reason = 'manual',
    afterId = cursorRef.current,
  ) => {
    if (!enabled || !token || !deskRef.current) return
    if (reconcilePromiseRef.current) return reconcilePromiseRef.current
    const now = Date.now()
    if (
      reason !== 'manual'
      && now - reconcileRequestedAtRef.current < RECONCILE_THROTTLE_MS
    ) return
    reconcileRequestedAtRef.current = now
    const generation = generationRef.current
    const sessionId = deskRef.current.session.id
    const controller = new AbortController()
    reconcileAbortRef.current?.abort()
    reconcileAbortRef.current = controller
    const promise = (async () => {
      try {
        const [nextDesk, thread, delta] = await Promise.all([
          getMasterDesk(token, controller.signal),
          listMessages(token, sessionId, controller.signal),
          listEvents(token, sessionId, Math.max(0, afterId), controller.signal),
        ])
        if (
          controller.signal.aborted
          || generation !== generationRef.current
          || nextDesk.session.id !== sessionId
        ) return
        eventIdsRef.current = rememberEventIds(eventIdsRef.current, delta.events)
        for (const event of delta.events) {
          if (event.run_id > 0) {
            runSeqRef.current.set(
              event.run_id,
              Math.max(runSeqRef.current.get(event.run_id) || 0, event.seq),
            )
          }
        }
        const newest = delta.events.reduce(
          (cursor, event) => Math.max(cursor, event.id),
          afterId,
        )
        // A newer live event may land while these three reconciliation requests
        // are in flight. Never let its older desk snapshot roll that projection
        // backward. Message snapshots merge by durable id and are safe to apply.
        if (cursorRef.current <= newest) {
          deskRef.current = nextDesk
          setDesk(nextDesk)
          setActiveRun(activeMasterRun(nextDesk.master_run))
        }
        setMessages(current => mergeMessageSnapshot(thread.messages, current))
        setCursor(newest)
        setConnectionError('')
      } catch (error) {
        if (
          !controller.signal.aborted
          && generation === generationRef.current
        ) {
          if (reason !== 'manual') setConnectionState('disconnected')
          setConnectionError(
            reason === 'manual'
              ? errorMessage(error)
              : 'Master state could not be reconciled after the live connection changed.',
          )
        }
      }
    })().finally(() => {
      if (reconcilePromiseRef.current === promise) reconcilePromiseRef.current = null
      if (reconcileAbortRef.current === controller) reconcileAbortRef.current = null
    })
    reconcilePromiseRef.current = promise
    return promise
  }, [enabled, setCursor, token])

  reconcileRef.current = reconcile

  const applyEvent = React.useCallback((event: RunEvent) => {
    const currentDesk = deskRef.current
    if (!currentDesk || event.session_id !== currentDesk.session.id) return
    if (!Number.isSafeInteger(event.id) || event.id <= 0) return
    if (eventIdsRef.current.has(event.id) || event.id <= cursorRef.current) return

    const previousCursor = cursorRef.current
    let sequenceGap = false
    if (event.run_id > 0 && event.seq > 0) {
      const previousSeq = runSeqRef.current.get(event.run_id)
      if (previousSeq != null && event.seq > previousSeq + 1) sequenceGap = true
      runSeqRef.current.set(event.run_id, Math.max(previousSeq || 0, event.seq))
    }
    eventIdsRef.current = rememberEventIds(eventIdsRef.current, [event])
    setCursor(event.id)

    if (event.type === 'run.queued') {
      setActiveRun({ id: event.run_id, status: 'queued' })
      setDesk(current => {
        if (!current) return current
        const next = { id: event.run_id, status: 'queued' }
        deskRef.current = { ...current, master_run: next }
        return deskRef.current
      })
    } else if (event.type === 'run.started') {
      setActiveRun({ id: event.run_id, status: 'running' })
      setDesk(current => {
        if (!current) return current
        const next = { id: event.run_id, status: 'running' }
        deskRef.current = { ...current, master_run: next }
        return deskRef.current
      })
    } else if (event.type === 'message.complete') {
      const payload = isRecord(event.payload) ? event.payload : {}
      const messageId = positiveInteger(payload.message_id)
      const text = typeof payload.text === 'string' ? payload.text : ''
      if (messageId != null && text) {
        setMessages(current => current.some(message => message.id === messageId)
          ? current
          : [...current, {
              id: messageId,
              role: 'assistant',
              author: 'Master',
              content: text,
              run_id: event.run_id,
              created_at: event.created_at,
            }])
        if (!homeActiveRef.current) setUnreadCount(count => count + 1)
      }
    } else if (MASTER_PROJECTION_TYPES.has(event.type as never)) {
      const payload = isRecord(event.payload) ? event.payload : {}
      const messageId = positiveInteger(payload.message_id)
      const content = safeProjectionContent(event.type, payload)
      if (messageId != null && content) {
        setMessages(current => current.some(message => message.id === messageId)
          ? current
          : [...current, {
              id: messageId,
              role: 'assistant',
              author: 'Master',
              content,
              run_id: null,
              created_at: event.created_at,
            }])
        if (!homeActiveRef.current) setUnreadCount(count => count + 1)
      }
      setDesk(current => {
        const next = updateProjectedJob(current, event)
        deskRef.current = next
        return next
      })
    }

    if (
      event.type === 'run.completed'
      || event.type === 'run.failed'
      || event.type === 'run.cancelled'
    ) {
      setActiveRun(null)
      setDesk(current => {
        if (!current) return current
        deskRef.current = { ...current, master_run: null }
        return deskRef.current
      })
      if (event.type === 'run.failed') {
        setConnectionError('Master could not complete the accepted turn. The durable thread is preserved.')
      }
      void reconcileRef.current('terminal', previousCursor)
    } else if (sequenceGap) {
      setConnectionError('A live event gap was detected. Master is reconciling durable state.')
      void reconcileRef.current('cursor-gap', previousCursor)
    }
  }, [setCursor])

  handleEventRef.current = applyEvent

  React.useEffect(() => {
    const generation = ++generationRef.current
    sourceRef.current?.close()
    sourceRef.current = null
    lifecycleAbortRef.current?.abort()
    sendAbortRef.current?.abort()
    settingsAbortRef.current?.abort()
    reconcileAbortRef.current?.abort()
    reconcilePromiseRef.current = null
    clearOwnedState(enabled)
    if (!enabled || !token || !ownerId) return

    const controller = new AbortController()
    lifecycleAbortRef.current = controller
    let startTimer = window.setTimeout(() => {
      startTimer = 0
      void (async () => {
        try {
          const initialDesk = await getMasterDesk(token, controller.signal)
          const sessionId = initialDesk.session.id
          const [thread, eventPage] = await Promise.all([
            listMessages(token, sessionId, controller.signal),
            listEvents(token, sessionId, 0, controller.signal),
          ])
          if (
            controller.signal.aborted
            || generation !== generationRef.current
          ) return
          const cursor = eventPage.events.reduce(
            (latest, event) => Math.max(latest, event.id),
            0,
          )
          eventIdsRef.current = rememberEventIds(new Set(), eventPage.events)
          runSeqRef.current = new Map()
          for (const event of eventPage.events) {
            if (event.run_id > 0) {
              runSeqRef.current.set(
                event.run_id,
                Math.max(runSeqRef.current.get(event.run_id) || 0, event.seq),
              )
            }
          }
          deskRef.current = initialDesk
          cursorRef.current = cursor
          setDesk(initialDesk)
          setMessages(thread.messages)
          setActiveRun(activeMasterRun(initialDesk.master_run))
          setResumeCursor(cursor)
          setLoading(false)
          setConnectionError('')

          let opened = false
          let disconnected = false
          const source = new EventSource(
            `/api/sessions/${sessionId}/events/stream?after_id=${cursor}`,
            { withCredentials: true },
          )
          if (
            controller.signal.aborted
            || generation !== generationRef.current
          ) {
            source.close()
            return
          }
          sourceRef.current = source
          const emit = (value: string) => {
            if (
              controller.signal.aborted
              || generation !== generationRef.current
            ) return
            try {
              const parsed = JSON.parse(value) as RunEvent
              handleEventRef.current(parsed)
            } catch {
              setConnectionError('A malformed live event was ignored. Master is reconciling durable state.')
              void reconcileRef.current('cursor-gap', cursorRef.current)
            }
          }
          source.onopen = () => {
            if (
              controller.signal.aborted
              || generation !== generationRef.current
            ) return
            const shouldReconcile = opened && disconnected
            opened = true
            disconnected = false
            setConnectionState('connected')
            setConnectionError('')
            if (shouldReconcile) {
              setReconnectCount(count => count + 1)
              void reconcileRef.current('reconnect', cursorRef.current)
            }
          }
          source.onerror = () => {
            if (
              controller.signal.aborted
              || generation !== generationRef.current
            ) return
            disconnected = true
            setConnectionState(
              source.readyState === EventSource.CLOSED ? 'disconnected' : 'retrying',
            )
          }
          for (const type of SESSION_EVENT_TYPES) {
            source.addEventListener(type, event => emit((event as MessageEvent).data))
          }
        } catch (error) {
          if (
            controller.signal.aborted
            || generation !== generationRef.current
          ) return
          setLoading(false)
          setConnectionState('disconnected')
          setConnectionError(errorMessage(error))
        }
      })()
    }, 0)

    return () => {
      if (startTimer) window.clearTimeout(startTimer)
      controller.abort()
      sourceRef.current?.close()
      sourceRef.current = null
      if (lifecycleAbortRef.current === controller) lifecycleAbortRef.current = null
      sendAbortRef.current?.abort()
      sendAbortRef.current = null
      settingsAbortRef.current?.abort()
      settingsAbortRef.current = null
      reconcileAbortRef.current?.abort()
      reconcileAbortRef.current = null
      sendLockRef.current = false
    }
  }, [clearOwnedState, enabled, ownerId, token])

  const setDraft = React.useCallback((nextDraft: string) => {
    if (!sendLockRef.current) setDraftState(nextDraft)
  }, [])

  const seedDraft = React.useCallback((nextDraft: string) => {
    if (sendLockRef.current) return
    setDraftState(nextDraft)
    setSelection({ start: nextDraft.length, end: nextDraft.length })
    setFocusRequest(request => request + 1)
  }, [])

  const send = React.useCallback(async (content = draft) => {
    const text = content.trim()
    if (!enabled || !token || !deskRef.current || !text) return
    if (
      sendLockRef.current
      || ['queued', 'running'].includes(activeRun?.status || '')
    ) return
    sendLockRef.current = true
    setSending(true)
    setSendError('')
    const generation = generationRef.current
    const controller = new AbortController()
    sendAbortRef.current?.abort()
    sendAbortRef.current = controller
    const clientId = `master-send-${++tempMessageIdRef.current}`
    setMessages(current => [...current, {
      role: 'user',
      content: text,
      author: 'You',
      clientId,
      pending: true,
    }])
    try {
      const result = await sendMasterMessage(token, text, controller.signal)
      if (
        controller.signal.aborted
        || generation !== generationRef.current
      ) return
      setMessages(current => current.map(message => message.clientId === clientId
        ? { ...message, pending: false, run_id: result.run_id }
        : message))
      setActiveRun({ id: result.run_id, status: result.status })
      setDesk(current => {
        if (!current) return current
        const next = { id: result.run_id, status: result.status }
        deskRef.current = { ...current, master_run: next }
        return deskRef.current
      })
      setDraftState('')
      setSelection({ start: 0, end: 0 })
    } catch (error) {
      if (
        !controller.signal.aborted
        && generation === generationRef.current
      ) {
        setMessages(current => current.filter(message => message.clientId !== clientId))
        setSendError(errorMessage(error))
        throw error
      }
    } finally {
      if (generation === generationRef.current) {
        setSending(false)
        sendLockRef.current = false
      }
      if (sendAbortRef.current === controller) sendAbortRef.current = null
    }
  }, [activeRun?.status, draft, enabled, token])

  const setHomeActive = React.useCallback((active: boolean) => {
    homeActiveRef.current = active
    setHomeActiveState(active)
    if (active) setUnreadCount(0)
  }, [])

  const setSideCollapsed = React.useCallback((collapsed: boolean) => {
    setSideCollapsedState(collapsed)
    try {
      localStorage.setItem(SIDE_COLLAPSED_KEY, collapsed ? '1' : '0')
    } catch {
      // UI preference persistence is best effort.
    }
  }, [])

  const setScrollState = React.useCallback((next: {
    scrollTop: number
    followTail: boolean
    anchorMessageId: number | null
  }) => {
    setScrollStateValue(current => (
      current.scrollTop === next.scrollTop
      && current.followTail === next.followTail
      && current.anchorMessageId === next.anchorMessageId
        ? current
        : next
    ))
  }, [])

  const updateSettings = React.useCallback(async (settings: Partial<MasterSettings>) => {
    if (!enabled || !token || !deskRef.current) return
    const generation = generationRef.current
    const controller = new AbortController()
    settingsAbortRef.current?.abort()
    settingsAbortRef.current = controller
    try {
      const result = await saveMasterSettings(token, settings, controller.signal)
      if (
        controller.signal.aborted
        || generation !== generationRef.current
      ) return
      setDesk(current => {
        if (!current) return current
        deskRef.current = {
          ...current,
          backing_runner: result.runner_id,
          unattended: result.unattended,
          budgets: {
            unattended: result.unattended,
            budget_turns: result.budget_turns,
            budget_wall_seconds: result.budget_wall_seconds,
            budget_tokens: result.budget_tokens,
            tour_core_done: result.tour_core_done,
          },
          capacity: {
            ...current.capacity,
            max: result.max_parallel,
            free: Math.max(0, result.max_parallel - current.capacity.running),
          },
        }
        return deskRef.current
      })
      setConnectionError('')
    } catch (error) {
      if (
        !controller.signal.aborted
        && generation === generationRef.current
      ) {
        setConnectionError(errorMessage(error))
        throw error
      }
    } finally {
      if (settingsAbortRef.current === controller) settingsAbortRef.current = null
    }
  }, [enabled, token])

  const value = React.useMemo<MasterStateValue>(() => ({
    enabled,
    loading,
    desk,
    messages,
    activeRun,
    connection: {
      state: connectionState,
      resumeCursor,
      reconnectCount,
      error: connectionError,
    },
    unread: { count: unreadCount },
    composer: {
      draft,
      selection,
      sending,
      error: sendError,
      focusRequest,
    },
    view: {
      homeActive,
      sideCollapsed,
      ...scrollState,
    },
    future: FUTURE_STATE,
    actions: {
      setDraft,
      setSelection,
      seedDraft,
      send,
      setHomeActive,
      markRead: () => setUnreadCount(0),
      setSideCollapsed,
      setScrollState,
      refresh: () => reconcile('manual'),
      updateSettings,
      clearError: () => {
        setConnectionError('')
        setSendError('')
      },
    },
  }), [
    activeRun,
    connectionError,
    connectionState,
    desk,
    draft,
    enabled,
    focusRequest,
    homeActive,
    loading,
    messages,
    reconnectCount,
    resumeCursor,
    scrollState,
    selection,
    send,
    sendError,
    sending,
    setDraft,
    setHomeActive,
    setScrollState,
    setSideCollapsed,
    sideCollapsed,
    unreadCount,
    updateSettings,
  ])

  return (
    <MasterStateContext.Provider value={value}>
      {children}
    </MasterStateContext.Provider>
  )
}

export function MasterStateProvider(props: {
  token: string
  ownerId: number
  enabled: boolean
  children: React.ReactNode
}) {
  const identity = props.enabled
    ? `${props.ownerId}:${props.token}`
    : 'feature-off'
  return <MasterStateHost key={identity} {...props} />
}

export function useMasterState(): MasterStateValue {
  const value = React.useContext(MasterStateContext)
  if (!value) throw new Error('useMasterState must be used inside MasterStateProvider')
  return value
}
