import React from 'react'
import {
  getMasterDesk,
  saveMasterSettings,
  sendMasterMessage,
  type MasterDesk,
  type MasterSettings,
} from '../api/master'
import { listEvents } from '../api/runs'
import { listMessages } from '../api/sessions'
import { SESSION_EVENT_TYPES } from '../lib/eventTypes'
import type { RunEvent } from '../types'
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
const EVENT_DEDUPE_LIMIT = 2000
const RECONCILE_RACE_LIMIT = 3

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
  const reconcileAbortRef = React.useRef<AbortController | null>(null)
  const reconcilePromiseRef = React.useRef<Promise<void> | null>(null)
  const reconcileRequestRef = React.useRef<{
    reason: string
    afterId: number
  } | null>(null)
  const mutationRevisionRef = React.useRef(0)
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
  }, [])

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
          setDesk(projected.desk)
          setMessages(nextMessages)
          if (!homeActiveRef.current && unreadMessageIds.size) {
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
      setDesk(projected.desk)
    }
    if (projected.messages !== messagesRef.current) {
      messagesRef.current = projected.messages
      setMessages(projected.messages)
    }
    if (projected.insertedMessageId != null && !homeActiveRef.current) {
      setUnreadCount(count => count + 1)
    }

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
          const cursor = initialDesk.event_cursor
          const thread = await listMessages(token, sessionId, controller.signal)
          if (
            controller.signal.aborted
            || generation !== generationRef.current
          ) return
          eventIdsRef.current = new Set()
          runSeqRef.current = new Map()
          deskRef.current = initialDesk
          messagesRef.current = orderMasterMessages(thread.messages)
          cursorRef.current = cursor
          setDesk(initialDesk)
          setMessages(messagesRef.current)
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
    messagesRef.current = orderMasterMessages([...messagesRef.current, {
      role: 'user',
      content: text,
      author: 'You',
      clientId,
      pending: true,
    }])
    setMessages(messagesRef.current)
    try {
      const result = await sendMasterMessage(token, text, controller.signal)
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
    ? String(props.ownerId)
    : 'feature-off'
  return <MasterStateHost key={identity} {...props} />
}

export function useMasterState(): MasterStateValue {
  const value = React.useContext(MasterStateContext)
  if (!value) throw new Error('useMasterState must be used inside MasterStateProvider')
  return value
}
