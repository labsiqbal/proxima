import React from 'react'
import {
  getMasterDesk,
  saveMasterSettings,
  sendMasterMessage,
  updateMasterFocus,
  type MasterDesk,
  type MasterFocusSnapshot,
  type MasterMessageContext,
  type MasterSettings,
} from '../api/master'
import { listContainerAreas, listContainers } from '../api/containers'
import { listEvents } from '../api/runs'
import { listMessages } from '../api/sessions'
import { SESSION_EVENT_TYPES } from '../lib/eventTypes'
import { notify } from '../lib/notify'
import type { Container, ContainerAreas, RunEvent } from '../types'
import {
  activeMasterRun,
  mergeMasterMessageSnapshot,
  orderMasterMessages,
  parseMasterStreamEvent,
  projectMasterEvent,
  projectMasterSnapshot,
  type MasterViewMessage,
} from './masterProjection'

export type { MasterViewMessage } from './masterProjection'

export type MasterConnectionState =
  | 'feature-off'
  | 'connecting'
  | 'connected'
  | 'retrying'
  | 'disconnected'

export type MasterComposerSelection = { start: number; end: number }

export type MasterFocusState = {
  mode: 'fleet' | 'container'
  containerId: number | null
}

export type MasterTargetState = {
  mode: 'auto' | 'explicit'
  containerId: number | null
  areaId: number | null
}

export type MasterToast = {
  id: number
  sourceKey: string
  title: string
  body: string
  tone: 'info' | 'success' | 'warning' | 'danger'
  priority: 'polite' | 'assertive'
}

export type MasterPopupState = {
  open: boolean
  preferredCorner: 'left' | 'right'
}

export type MasterFleetState = {
  loading: boolean
  error: string
  containers: Container[]
  areasByContainer: Record<number, ContainerAreas>
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
  focus: MasterFocusState
  target: MasterTargetState
  popup: MasterPopupState
  toasts: MasterToast[]
  fleet: MasterFleetState
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
    setFocus: (containerId: number | null) => Promise<void>
    setTargetContainer: (containerId: number | null) => void
    setTargetArea: (areaId: number | null) => void
    loadTargetAreas: (containerId: number) => Promise<void>
    openPopup: () => void
    closePopup: () => void
    togglePopup: () => void
    setPopupCorner: (corner: 'left' | 'right') => void
    dismissToast: (id: number) => void
    refresh: () => Promise<void>
    updateSettings: (settings: Partial<MasterSettings>) => Promise<void>
    clearError: () => void
  }
}

const MasterStateContext = React.createContext<MasterStateValue | null>(null)

const SIDE_COLLAPSED_KEY = 'proxima.master.sideCollapsed'
const MASTER_TARGET_KEY = 'proxima.master.target'
const MASTER_POPUP_CORNER_KEY = 'proxima.master.popupCorner'
const EVENT_DEDUPE_LIMIT = 2000
const RECONCILE_RACE_LIMIT = 3
const TOAST_QUEUE_LIMIT = 3

const DEFAULT_FOCUS: MasterFocusState = { mode: 'fleet', containerId: null }
const DEFAULT_TARGET: MasterTargetState = {
  mode: 'auto',
  containerId: null,
  areaId: null,
}

function ownerPreferenceKey(key: string, ownerId: number): string {
  return `${key}.${ownerId}`
}

function readTarget(ownerId: number): MasterTargetState {
  if (typeof localStorage === 'undefined') return DEFAULT_TARGET
  try {
    const value = JSON.parse(
      localStorage.getItem(ownerPreferenceKey(MASTER_TARGET_KEY, ownerId)) || 'null',
    )
    if (
      value?.mode === 'explicit'
      && Number.isSafeInteger(value.containerId)
      && value.containerId > 0
    ) {
      return {
        mode: 'explicit',
        containerId: value.containerId,
        areaId: Number.isSafeInteger(value.areaId) && value.areaId > 0
          ? value.areaId
          : null,
      }
    }
  } catch {
    // Preference recovery is best effort.
  }
  return DEFAULT_TARGET
}

function readPopupCorner(ownerId: number): 'left' | 'right' {
  if (typeof localStorage === 'undefined') return 'right'
  return localStorage.getItem(
    ownerPreferenceKey(MASTER_POPUP_CORNER_KEY, ownerId),
  ) === 'left' ? 'left' : 'right'
}

function writePreference(key: string, ownerId: number, value: unknown) {
  try {
    localStorage.setItem(ownerPreferenceKey(key, ownerId), JSON.stringify(value))
  } catch {
    // UI preference persistence is best effort.
  }
}

function positiveInteger(value: unknown): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0
    ? value
    : null
}

function focusState(snapshot: MasterFocusSnapshot): MasterFocusState {
  return snapshot.current_container_id == null
    ? DEFAULT_FOCUS
    : {
        mode: 'container',
        containerId: snapshot.current_container_id,
      }
}

function latestFocusSnapshot(
  current: MasterFocusSnapshot,
  incoming: MasterFocusSnapshot,
): MasterFocusSnapshot {
  return current.version > incoming.version ? current : incoming
}

function toastForEvent(event: RunEvent): Omit<MasterToast, 'id'> | null {
  const messageId = positiveInteger(event.payload.message_id)
  const taskId = positiveInteger(event.payload.task_id)
  if (messageId == null) return null
  if (event.type === 'master.task.started' && taskId != null) {
    return {
      sourceKey: `task:${taskId}:progress`,
      title: `Task #${taskId} started`,
      body: 'Master is tracking its durable progress in the conversation.',
      tone: 'info',
      priority: 'polite',
    }
  }
  const terminal: Record<string, {
    title: string
    body: string
    tone: MasterToast['tone']
  }> = {
    'master.task.completed': {
      title: `Task #${taskId} completed`,
      body: 'The durable result is ready in Master.',
      tone: 'success',
    },
    'master.task.failed': {
      title: `Task #${taskId} failed`,
      body: 'Master preserved the failure details in the conversation.',
      tone: 'danger',
    },
    'master.task.cancelled': {
      title: `Task #${taskId} cancelled`,
      body: 'The durable Task state is available in Master.',
      tone: 'warning',
    },
    'master.task.review_ready': {
      title: `Task #${taskId} needs review`,
      body: 'Open Master to review the durable Task result.',
      tone: 'warning',
    },
    'master.task.blocked': {
      title: `Task #${taskId} is blocked`,
      body: 'Master recorded the prerequisite that needs attention.',
      tone: 'warning',
    },
  }
  const match = terminal[event.type]
  if (match && taskId != null) {
    return {
      sourceKey: `task:${taskId}:${event.type}:${messageId}`,
      ...match,
      priority: match.tone === 'danger' ? 'assertive' : 'polite',
    }
  }
  if (
    event.type === 'master.attention.required'
    || event.type === 'master.supervisor.outcome'
  ) {
    return {
      sourceKey: `attention:${event.type}:${messageId}`,
      title: 'Master needs your attention',
      body: 'The durable decision request is ready in the conversation.',
      tone: 'warning',
      priority: 'assertive',
    }
  }
  if (event.type.startsWith('master.satpam.')) {
    const failed = event.type.endsWith('recovery_failed')
      || event.type.endsWith('escalated')
    return {
      sourceKey: `satpam:${event.type}:${messageId}`,
      title: failed ? 'Satpam escalated a Task' : 'Satpam updated a Task',
      body: 'The durable supervision result is ready in Master.',
      tone: failed ? 'danger' : 'warning',
      priority: failed ? 'assertive' : 'polite',
    }
  }
  return null
}

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
  const [focus, setFocusState] = React.useState<MasterFocusState>(DEFAULT_FOCUS)
  const [target, setTargetState] = React.useState<MasterTargetState>(
    () => readTarget(ownerId),
  )
  const [popup, setPopupState] = React.useState<MasterPopupState>(() => ({
    open: false,
    preferredCorner: readPopupCorner(ownerId),
  }))
  const [toasts, setToasts] = React.useState<MasterToast[]>([])
  const [fleet, setFleet] = React.useState<MasterFleetState>({
    loading: enabled,
    error: '',
    containers: [],
    areasByContainer: {},
  })
  const [bootstrapRequest, setBootstrapRequest] = React.useState(0)

  const generationRef = React.useRef(0)
  const deskRef = React.useRef<MasterDesk | null>(null)
  const messagesRef = React.useRef<MasterViewMessage[]>([])
  const cursorRef = React.useRef(0)
  const eventIdsRef = React.useRef(new Set<number>())
  const runSeqRef = React.useRef(new Map<number, number>())
  const sourceRef = React.useRef<EventSource | null>(null)
  const lifecycleAbortRef = React.useRef<AbortController | null>(null)
  const sendAbortRef = React.useRef<AbortController | null>(null)
  const settingsAbortRef = React.useRef<AbortController | null>(null)
  const focusAbortRef = React.useRef<AbortController | null>(null)
  const focusPromiseRef = React.useRef<Promise<void>>(Promise.resolve())
  const reconcileAbortRef = React.useRef<AbortController | null>(null)
  const reconcilePromiseRef = React.useRef<Promise<void> | null>(null)
  const reconcileRequestRef = React.useRef<{
    reason: string
    afterId: number
  } | null>(null)
  const mutationRevisionRef = React.useRef(0)
  const sendLockRef = React.useRef(false)
  const tempMessageIdRef = React.useRef(0)
  const toastIdRef = React.useRef(0)
  const toastTransitionsRef = React.useRef(new Set<string>())
  const homeActiveRef = React.useRef(false)
  const popupOpenRef = React.useRef(false)
  const focusRef = React.useRef(focus)
  const targetRef = React.useRef(target)
  const fleetRef = React.useRef(fleet)
  const areaRequestsRef = React.useRef(new Map<number, Promise<void>>())
  const handleEventRef = React.useRef<(event: RunEvent) => void>(() => {})
  const reconcileRef = React.useRef<(reason?: string, afterId?: number) => Promise<void>>(
    async () => {},
  )

  React.useEffect(() => { deskRef.current = desk }, [desk])
  React.useEffect(() => { homeActiveRef.current = homeActive }, [homeActive])
  React.useEffect(() => { popupOpenRef.current = popup.open }, [popup.open])
  React.useEffect(() => { focusRef.current = focus }, [focus])
  React.useEffect(() => { targetRef.current = target }, [target])
  React.useEffect(() => { fleetRef.current = fleet }, [fleet])

  const setCursor = React.useCallback((cursor: number) => {
    cursorRef.current = Math.max(cursorRef.current, cursor)
    setResumeCursor(cursorRef.current)
  }, [])

  const clearOwnedState = React.useCallback((nextEnabled: boolean) => {
    deskRef.current = null
    messagesRef.current = []
    cursorRef.current = 0
    eventIdsRef.current = new Set()
    runSeqRef.current = new Map()
    reconcileRequestRef.current = null
    mutationRevisionRef.current = 0
    sendLockRef.current = false
    setDesk(null)
    setMessages([])
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
    const nextFocus = DEFAULT_FOCUS
    const nextTarget = readTarget(ownerId)
    const nextPopup = {
      open: false,
      preferredCorner: readPopupCorner(ownerId),
    } satisfies MasterPopupState
    focusRef.current = nextFocus
    targetRef.current = nextTarget
    popupOpenRef.current = false
    fleetRef.current = {
      loading: nextEnabled,
      error: '',
      containers: [],
      areasByContainer: {},
    }
    areaRequestsRef.current.clear()
    toastTransitionsRef.current.clear()
    setFocusState(nextFocus)
    setTargetState(nextTarget)
    setPopupState(nextPopup)
    setToasts([])
    setFleet(fleetRef.current)
  }, [ownerId])

  const reconcile = React.useCallback(async (
    reason = 'manual',
    afterId = cursorRef.current,
  ) => {
    if (!enabled || !token || !deskRef.current) return
    const pending = reconcileRequestRef.current
    reconcileRequestRef.current = {
      reason: reason === 'manual' || pending?.reason === 'manual'
        ? 'manual'
        : reason,
      afterId: Math.min(
        Math.max(0, afterId),
        pending?.afterId ?? Number.MAX_SAFE_INTEGER,
      ),
    }
    if (reconcilePromiseRef.current) return reconcilePromiseRef.current
    const generation = generationRef.current
    const controller = new AbortController()
    reconcileAbortRef.current = controller
    const promise = (async () => {
      let attempts = 0
      while (
        reconcileRequestRef.current
        && attempts < RECONCILE_RACE_LIMIT
        && !controller.signal.aborted
        && generation === generationRef.current
      ) {
        attempts += 1
        const request = reconcileRequestRef.current
        reconcileRequestRef.current = null
        const currentDesk = deskRef.current
        if (!currentDesk) return
        const sessionId = currentDesk.session.id
        const revision = mutationRevisionRef.current
        try {
          const [nextDesk, thread] = await Promise.all([
            getMasterDesk(token, controller.signal),
            listMessages(token, sessionId, controller.signal),
          ])
          const delta = await listEvents(
            token,
            sessionId,
            request.afterId,
            controller.signal,
          )
          if (
            controller.signal.aborted
            || generation !== generationRef.current
            || nextDesk.session.id !== sessionId
          ) return
          const barrier = delta.events.reduce(
            (cursor, event) => Math.max(cursor, event.id),
            request.afterId,
          )
          if (
            cursorRef.current > barrier
            || mutationRevisionRef.current !== revision
          ) {
            const queued = reconcileRequestRef.current as {
              reason: string
              afterId: number
            } | null
            reconcileRequestRef.current = {
              reason: request.reason,
              afterId: Math.min(request.afterId, queued?.afterId ?? request.afterId),
            }
            continue
          }
          const projected = projectMasterSnapshot(
            nextDesk,
            thread.messages,
            delta.events,
          )
          const previousMessageIds = new Set(
            messagesRef.current.flatMap(message => message.id == null ? [] : [message.id]),
          )
          const nextMessages = mergeMasterMessageSnapshot(
            projected.messages,
            messagesRef.current,
          )
          const unreadMessageIds = new Set(
            nextMessages.flatMap(message => (
              message.id != null
              && message.role !== 'user'
              && !previousMessageIds.has(message.id)
                ? [message.id]
                : []
            )),
          )
          eventIdsRef.current = rememberEventIds(eventIdsRef.current, delta.events)
          for (const event of delta.events) {
            if (event.run_id > 0 && event.seq > 0) {
              runSeqRef.current.set(
                event.run_id,
                Math.max(runSeqRef.current.get(event.run_id) || 0, event.seq),
              )
            }
          }
          deskRef.current = projected.desk
          messagesRef.current = nextMessages
          const nextFocus = focusState(projected.desk.focus)
          focusRef.current = nextFocus
          setDesk(projected.desk)
          setMessages(nextMessages)
          setFocusState(nextFocus)
          if (
            !homeActiveRef.current
            && !popupOpenRef.current
            && unreadMessageIds.size
          ) {
            setUnreadCount(count => count + unreadMessageIds.size)
          }
          setCursor(barrier)
          setConnectionError('')
        } catch (error) {
          reconcileRequestRef.current = null
          if (
            !controller.signal.aborted
            && generation === generationRef.current
          ) {
            if (request.reason !== 'manual') {
              const source = sourceRef.current
              if (!source || source.readyState !== EventSource.OPEN) {
                setConnectionState('disconnected')
              }
            }
            setConnectionError(
              request.reason === 'manual'
                ? errorMessage(error)
                : 'Master state could not be reconciled after the live connection changed.',
            )
          }
          return
        }
      }
      if (reconcileRequestRef.current && generation === generationRef.current) {
        setConnectionError(
          'Master state changed repeatedly during reconciliation. Live state was kept and another retry is available.',
        )
        reconcileRequestRef.current = null
      }
    })().finally(() => {
      if (reconcilePromiseRef.current === promise) reconcilePromiseRef.current = null
      if (reconcileAbortRef.current === controller) reconcileAbortRef.current = null
    })
    reconcilePromiseRef.current = promise
    return promise
  }, [enabled, setCursor, token])

  reconcileRef.current = reconcile

  const enqueueToast = React.useCallback((event: RunEvent) => {
    const toast = toastForEvent(event)
    if (!toast) return
    const isProgress = toast.sourceKey.endsWith(':progress')
    if (!isProgress && toastTransitionsRef.current.has(toast.sourceKey)) return
    if (!isProgress) toastTransitionsRef.current.add(toast.sourceKey)
    const next = { ...toast, id: ++toastIdRef.current }
    setToasts(current => {
      const withoutSource = current.filter(
        item => item.sourceKey !== toast.sourceKey,
      )
      return [...withoutSource, next].slice(-TOAST_QUEUE_LIMIT)
    })
    notify(toast.title, toast.body)
  }, [])

  const applyEvent = React.useCallback((event: RunEvent) => {
    const currentDesk = deskRef.current
    if (!currentDesk || event.session_id !== currentDesk.session.id) return
    if (!Number.isSafeInteger(event.id) || event.id <= 0) return
    if (eventIdsRef.current.has(event.id) || event.id <= cursorRef.current) return

    const previousCursor = cursorRef.current
    let sequenceGap = false
    let staleSequence = false
    if (event.run_id > 0 && event.seq > 0) {
      const previousSeq = runSeqRef.current.get(event.run_id)
      if (previousSeq != null && event.seq > previousSeq + 1) sequenceGap = true
      if (previousSeq != null && event.seq <= previousSeq) staleSequence = true
      runSeqRef.current.set(event.run_id, Math.max(previousSeq || 0, event.seq))
    }
    eventIdsRef.current = rememberEventIds(eventIdsRef.current, [event])
    setCursor(event.id)
    if (staleSequence) {
      setConnectionError('An out-of-order live event was ignored. Master is reconciling durable state.')
      void reconcileRef.current('cursor-gap', previousCursor)
      return
    }

    const projected = projectMasterEvent(
      currentDesk,
      messagesRef.current,
      event,
    )
    if (projected.desk !== currentDesk) {
      deskRef.current = projected.desk
      const nextFocus = focusState(projected.desk.focus)
      focusRef.current = nextFocus
      setDesk(projected.desk)
      setFocusState(nextFocus)
    }
    if (projected.messages !== messagesRef.current) {
      messagesRef.current = projected.messages
      setMessages(projected.messages)
    }
    if (
      projected.insertedMessageId != null
      && !homeActiveRef.current
      && !popupOpenRef.current
    ) {
      setUnreadCount(count => count + 1)
    }
    enqueueToast(event)

    if (
      event.type === 'run.completed'
      || event.type === 'run.failed'
      || event.type === 'run.cancelled'
    ) {
      if (
        event.type === 'run.failed'
        && activeMasterRun(currentDesk.master_run)?.id === event.run_id
      ) {
        setConnectionError('Master could not complete the accepted turn. The durable thread is preserved.')
      }
    } else if (sequenceGap) {
      setConnectionError('A live event gap was detected. Master is reconciling durable state.')
      void reconcileRef.current('cursor-gap', previousCursor)
    }
  }, [enqueueToast, setCursor])

  handleEventRef.current = applyEvent

  React.useEffect(() => {
    const generation = ++generationRef.current
    sourceRef.current?.close()
    sourceRef.current = null
    lifecycleAbortRef.current?.abort()
    sendAbortRef.current?.abort()
    settingsAbortRef.current?.abort()
    focusAbortRef.current?.abort()
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
          const [initialDesk, fleetOutcome] = await Promise.all([
            getMasterDesk(token, controller.signal),
            listContainers(token, controller.signal).then(
              result => ({ result, error: '' }),
              error => ({ result: null, error: errorMessage(error) }),
            ),
          ])
          const sessionId = initialDesk.session.id
          const cursor = initialDesk.event_cursor
          const thread = await listMessages(token, sessionId, controller.signal)
          if (
            controller.signal.aborted
            || generation !== generationRef.current
          ) return
          let nextFocus = focusState(initialDesk.focus)
          let nextTarget = targetRef.current
          if (fleetOutcome.result) {
            const availableIds = new Set(
              fleetOutcome.result.containers.map(container => container.id),
            )
            if (
              nextTarget.mode === 'explicit'
              && !availableIds.has(nextTarget.containerId || 0)
            ) {
              nextTarget = DEFAULT_TARGET
            }
          }
          focusRef.current = nextFocus
          targetRef.current = nextTarget
          fleetRef.current = {
            loading: false,
            error: fleetOutcome.error,
            containers: fleetOutcome.result?.containers ?? [],
            areasByContainer: {},
          }
          eventIdsRef.current = new Set()
          runSeqRef.current = new Map()
          deskRef.current = initialDesk
          messagesRef.current = orderMasterMessages(thread.messages)
          cursorRef.current = cursor
          setDesk(initialDesk)
          setMessages(messagesRef.current)
          setFocusState(nextFocus)
          setTargetState(nextTarget)
          setFleet(fleetRef.current)
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
            const parsed = parseMasterStreamEvent(value)
            if (!parsed) {
              setConnectionError('A malformed live event was ignored. Master is reconciling durable state.')
              void reconcileRef.current('cursor-gap', cursorRef.current)
              return
            }
            handleEventRef.current(parsed)
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
          setFleet(current => ({ ...current, loading: false, error: errorMessage(error) }))
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
      focusAbortRef.current?.abort()
      focusAbortRef.current = null
      reconcileAbortRef.current?.abort()
      reconcileAbortRef.current = null
      sendLockRef.current = false
    }
  }, [bootstrapRequest, clearOwnedState, enabled, ownerId, token])

  const setDraft = React.useCallback((nextDraft: string) => {
    if (!sendLockRef.current) setDraftState(nextDraft)
  }, [])

  const seedDraft = React.useCallback((nextDraft: string) => {
    if (sendLockRef.current) return
    setDraftState(nextDraft)
    setSelection({ start: nextDraft.length, end: nextDraft.length })
    setFocusRequest(request => request + 1)
  }, [])

  const setFocus = React.useCallback((containerId: number | null) => {
    const requestedGeneration = generationRef.current
    const apply = async () => {
      const currentDesk = deskRef.current
      if (
        !enabled
        || !token
        || !currentDesk
        || requestedGeneration !== generationRef.current
        || (
          currentDesk.focus.current_container_id === containerId
          && !currentDesk.focus.pending
        )
      ) return
      const controller = new AbortController()
      focusAbortRef.current = controller
      try {
        const result = await updateMasterFocus(
          token,
          containerId,
          currentDesk.focus.version,
          controller.signal,
        )
        if (
          controller.signal.aborted
          || requestedGeneration !== generationRef.current
        ) return
        mutationRevisionRef.current += 1
        const latestDesk = deskRef.current
        if (!latestDesk || latestDesk.session.id !== currentDesk.session.id) return
        const resolvedFocus = latestFocusSnapshot(
          latestDesk.focus,
          result.focus,
        )
        const nextFocus = focusState(resolvedFocus)
        focusRef.current = nextFocus
        deskRef.current = {
          ...latestDesk,
          focus: resolvedFocus,
        }
        setDesk(deskRef.current)
        setFocusState(nextFocus)
        setConnectionError('')
      } catch (error) {
        if (
          !controller.signal.aborted
          && requestedGeneration === generationRef.current
        ) {
          setConnectionError(errorMessage(error))
          void reconcileRef.current('manual', cursorRef.current)
          throw error
        }
      } finally {
        if (focusAbortRef.current === controller) {
          focusAbortRef.current = null
        }
      }
    }
    const request = focusPromiseRef.current.then(apply, apply)
    focusPromiseRef.current = request.catch(() => {})
    return request
  }, [enabled, token])

  const setTargetContainer = React.useCallback((containerId: number | null) => {
    const next: MasterTargetState = containerId == null
      ? DEFAULT_TARGET
      : { mode: 'explicit', containerId, areaId: null }
    targetRef.current = next
    setTargetState(next)
    writePreference(MASTER_TARGET_KEY, ownerId, next)
  }, [ownerId])

  const setTargetArea = React.useCallback((areaId: number | null) => {
    const current = targetRef.current
    if (current.mode !== 'explicit' || current.containerId == null) return
    const next = { ...current, areaId }
    targetRef.current = next
    setTargetState(next)
    writePreference(MASTER_TARGET_KEY, ownerId, next)
  }, [ownerId])

  const loadTargetAreas = React.useCallback(async (containerId: number) => {
    if (fleetRef.current.areasByContainer[containerId]) return
    const existing = areaRequestsRef.current.get(containerId)
    if (existing) return existing
    const container = fleetRef.current.containers.find(
      item => item.id === containerId,
    )
    if (!container) return
    const generation = generationRef.current
    const request = listContainerAreas(token, container.slug)
      .then(areas => {
        if (generation !== generationRef.current) return
        const validAreaIds = new Set([
          ...areas.code_areas.map(area => area.id),
          areas.ops_area.id,
        ])
        fleetRef.current = {
          ...fleetRef.current,
          error: '',
          areasByContainer: {
            ...fleetRef.current.areasByContainer,
            [containerId]: areas,
          },
        }
        setFleet(fleetRef.current)
        const currentTarget = targetRef.current
        if (
          currentTarget.containerId === containerId
          && currentTarget.areaId != null
          && !validAreaIds.has(currentTarget.areaId)
        ) {
          const next = { ...currentTarget, areaId: null }
          targetRef.current = next
          setTargetState(next)
          writePreference(MASTER_TARGET_KEY, ownerId, next)
        }
      })
      .catch(error => {
        if (generation !== generationRef.current) return
        fleetRef.current = {
          ...fleetRef.current,
          error: errorMessage(error),
        }
        setFleet(fleetRef.current)
      })
      .finally(() => {
        if (areaRequestsRef.current.get(containerId) === request) {
          areaRequestsRef.current.delete(containerId)
        }
      })
    areaRequestsRef.current.set(containerId, request)
    return request
  }, [ownerId, token])

  const openPopup = React.useCallback(() => {
    popupOpenRef.current = true
    setPopupState(current => ({ ...current, open: true }))
    setUnreadCount(0)
  }, [])

  const closePopup = React.useCallback(() => {
    popupOpenRef.current = false
    setPopupState(current => ({ ...current, open: false }))
  }, [])

  const togglePopup = React.useCallback(() => {
    if (popupOpenRef.current) {
      closePopup()
    } else {
      openPopup()
    }
  }, [closePopup, openPopup])

  const setPopupCorner = React.useCallback((preferredCorner: 'left' | 'right') => {
    setPopupState(current => ({ ...current, preferredCorner }))
    try {
      localStorage.setItem(
        ownerPreferenceKey(MASTER_POPUP_CORNER_KEY, ownerId),
        preferredCorner,
      )
    } catch {
      // UI preference persistence is best effort.
    }
  }, [ownerId])

  const dismissToast = React.useCallback((id: number) => {
    setToasts(current => current.filter(toast => toast.id !== id))
  }, [])

  const activeRun = activeMasterRun(desk?.master_run ?? null)

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
    const selectedTarget = targetRef.current
    let effectiveFocus = focusRef.current
    if (
      selectedTarget.mode === 'explicit'
      && selectedTarget.containerId != null
      && (
        effectiveFocus.mode !== 'container'
        || effectiveFocus.containerId !== selectedTarget.containerId
      )
    ) {
      effectiveFocus = {
        mode: 'container',
        containerId: selectedTarget.containerId,
      }
    }
    const messageContext: MasterMessageContext = {
      focus: effectiveFocus.mode === 'container'
        ? { mode: 'container', container_id: effectiveFocus.containerId || undefined }
        : { mode: 'fleet' },
      target: selectedTarget.mode === 'explicit'
        ? {
            mode: 'explicit',
            container_id: selectedTarget.containerId || undefined,
            area_id: selectedTarget.areaId || undefined,
          }
        : { mode: 'auto' },
    }
    const masterTarget = {
      focus_mode: effectiveFocus.mode,
      focus_container_id: effectiveFocus.containerId,
      target_mode: selectedTarget.mode,
      target_container_id: selectedTarget.containerId,
      target_area_id: selectedTarget.areaId,
    } as const
    messagesRef.current = orderMasterMessages([...messagesRef.current, {
      role: 'user',
      content: text,
      author: 'You',
      clientId,
      pending: true,
      master_target: masterTarget,
    }])
    setMessages(messagesRef.current)
    try {
      const result = await sendMasterMessage(
        token,
        text,
        messageContext,
        controller.signal,
      )
      if (
        controller.signal.aborted
        || generation !== generationRef.current
      ) return
      mutationRevisionRef.current += 1
      messagesRef.current = messagesRef.current.map(message => message.clientId === clientId
        ? { ...result.message, pending: false }
        : message)
      messagesRef.current = orderMasterMessages(messagesRef.current)
      setMessages(messagesRef.current)
      const latestDesk = deskRef.current
      if (!latestDesk) return
      const resolvedFocus = latestFocusSnapshot(
        latestDesk.focus,
        result.focus,
      )
      const nextFocus = focusState(resolvedFocus)
      focusRef.current = nextFocus
      deskRef.current = {
        ...latestDesk,
        master_run: { id: result.run_id, status: result.status },
        focus: resolvedFocus,
      }
      setDesk(deskRef.current)
      setFocusState(nextFocus)
      setDraftState('')
      setSelection({ start: 0, end: 0 })
    } catch (error) {
      if (
        !controller.signal.aborted
        && generation === generationRef.current
      ) {
        messagesRef.current = messagesRef.current.filter(
          message => message.clientId !== clientId,
        )
        setMessages(messagesRef.current)
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
      mutationRevisionRef.current += 1
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
    focus,
    target,
    popup,
    toasts,
    fleet,
    actions: {
      setDraft,
      setSelection,
      seedDraft,
      send,
      setHomeActive,
      markRead: () => setUnreadCount(0),
      setSideCollapsed,
      setScrollState,
      setFocus,
      setTargetContainer,
      setTargetArea,
      loadTargetAreas,
      openPopup,
      closePopup,
      togglePopup,
      setPopupCorner,
      dismissToast,
      refresh: () => {
        if (
          !deskRef.current
          || connectionState === 'disconnected'
          || sourceRef.current?.readyState === EventSource.CLOSED
        ) {
          setBootstrapRequest(request => request + 1)
          return Promise.resolve()
        }
        return reconcile('manual')
      },
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
    dismissToast,
    draft,
    enabled,
    focusRequest,
    focus,
    fleet,
    homeActive,
    loading,
    messages,
    loadTargetAreas,
    openPopup,
    closePopup,
    popup,
    reconnectCount,
    resumeCursor,
    scrollState,
    selection,
    send,
    sendError,
    sending,
    setDraft,
    setFocus,
    setHomeActive,
    setPopupCorner,
    setScrollState,
    setSideCollapsed,
    setTargetArea,
    setTargetContainer,
    sideCollapsed,
    target,
    toasts,
    togglePopup,
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
    ? String(props.ownerId)
    : 'feature-off'
  return <MasterStateHost key={identity} {...props} />
}

export function useMasterState(): MasterStateValue {
  const value = React.useContext(MasterStateContext)
  if (!value) throw new Error('useMasterState must be used inside MasterStateProvider')
  return value
}
