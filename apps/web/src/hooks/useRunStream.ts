import React from 'react'
import type { RunEvent } from '../types'
import { useEventStream } from './useEventStream'
import { activeRuns, listEvents } from '../api/runs'

export const TERMINAL_EVENTS = new Set(['run.completed', 'run.failed', 'run.cancelled'])

// Shared live-run engine for every chat surface — main chat, Design Studio, and any
// future workflow chat. It owns the parts that must behave identically everywhere:
//   • the SSE subscription (via useEventStream),
//   • coalescing high-frequency message.delta events into an `events` list at ~30fps,
//   • the in-flight `busyRun` (+ a ref so event handlers can guard without stale closures),
//   • re-attaching to a run that is still running when a session is (re)opened.
// Each surface supplies only an `onEvent` handler for ITS specifics (rendering, goal
// loops, applying a design reply). The plumbing is written and tested once here, so a
// new surface can't reintroduce the poll-vs-stream / lost-thinking / no-reconnect bugs
// that came from each chat rolling its own.
export function useRunStream(token: string, sessionId: number | null, onEvent?: (e: RunEvent) => void) {
  const [events, setEvents] = React.useState<RunEvent[]>([])
  const [busyRun, setBusyRun] = React.useState<number | null>(null)
  const busyRunRef = React.useRef<number | null>(null)
  React.useEffect(() => { busyRunRef.current = busyRun }, [busyRun])
  // Keep the handler in a ref so callers can pass a fresh closure each render without
  // resubscribing the stream — and so a handler may reference values this hook returns.
  const onEventRef = React.useRef(onEvent)
  React.useEffect(() => { onEventRef.current = onEvent })

  // Coalesce message.delta at ~30fps; control events flush immediately so the caller
  // reacts to them without a frame of lag.
  const bufferRef = React.useRef<RunEvent[]>([])
  const timerRef = React.useRef<number | null>(null)
  const flush = React.useCallback(() => {
    timerRef.current = null
    const buf = bufferRef.current
    if (!buf.length) return
    bufferRef.current = []
    setEvents(current => {
      const seen = new Set(current.map(e => e.id))
      const add = buf.filter(e => !seen.has(e.id))
      return add.length ? [...current, ...add] : current
    })
  }, [])
  React.useEffect(() => () => { if (timerRef.current != null) clearTimeout(timerRef.current) }, [])

  const handle = React.useCallback((event: RunEvent) => {
    bufferRef.current.push(event)
    if (event.type === 'message.delta' || event.type === 'collaboration.child.delta') {
      if (timerRef.current == null) timerRef.current = window.setTimeout(flush, 33)
      return
    }
    if (timerRef.current != null) { clearTimeout(timerRef.current); timerRef.current = null }
    flush()
    onEventRef.current?.(event)
  }, [flush])

  const { connected } = useEventStream(token, sessionId, handle)

  // Fetch the session's events and work out whether its latest run is still running
  // (no terminal event yet) or finished. PURE: it does not set state itself — the
  // caller applies the result under its own staleness guard (so a session that
  // changed mid-await can't be clobbered). `running` → re-attach busyRun; `completed`
  // → the caller may recover a run that finished while the surface was closed.
  const restore = React.useCallback(async (sid: number): Promise<{ events: RunEvent[]; lastRun: number | null; running: boolean; completed: boolean }> => {
    const [ev, active] = await Promise.all([
      listEvents(token, sid),
      activeRuns(token).catch(() => ({ session_ids: [] as number[] })),
    ])
    const evs = ev.events
    const runKinds = new Map<number, string>()
    for (const e of evs) {
      if (e.type === 'run.queued' && e.run_id) runKinds.set(e.run_id, String((e.payload as { kind?: string }).kind || 'chat'))
    }
    const visibleRun = (runId: number) => {
      const kind = String(runKinds.get(runId) || 'chat')
      if (kind.startsWith('message_review')) return false
      if (kind.startsWith('collab_') && kind !== 'collab_brainstorm' && kind !== 'collab_debate') return false
      return true
    }
    let lastRun: number | null = null
    for (const e of evs) {
      if (e.run_id && visibleRun(e.run_id)) lastRun = e.run_id
    }
    const running = lastRun != null && active.session_ids.includes(sid) && !evs.some(e => e.run_id === lastRun && TERMINAL_EVENTS.has(e.type))
    const completed = lastRun != null && evs.some(e => e.run_id === lastRun && e.type === 'run.completed')
    return { events: evs, lastRun, running, completed }
  }, [token])

  return { events, setEvents, busyRun, setBusyRun, busyRunRef, connected, restore }
}
