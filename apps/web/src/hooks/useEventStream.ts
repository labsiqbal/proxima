import React from 'react'
import type { RunEvent } from '../types'

export function useEventStream(token: string, sessionId: number | null, onEvent: (event: RunEvent) => void) {
  const [connected, setConnected] = React.useState(false)
  // Resume cursor is events.id (session-monotonic). seq is per-run, so it can't
  // order or resume a session that spans multiple runs.
  const lastId = React.useRef(0)

  // Keep the latest handler in a ref so the EventSource is created ONCE per
  // (token, sessionId) instead of reconnecting on every render when `onEvent`
  // changes identity. Reconnecting each render made the stream flap, which
  // showed up as the connection dot flickering.
  const handlerRef = React.useRef(onEvent)
  React.useEffect(() => { handlerRef.current = onEvent }, [onEvent])

  React.useEffect(() => {
    lastId.current = 0
    if (!token || !sessionId) return
    let closed = false
    const emit = (data: string) => {
      if (closed) return
      const parsed = JSON.parse(data) as RunEvent
      lastId.current = Math.max(lastId.current, parsed.id)
      handlerRef.current(parsed)
    }
    // Auth via the HttpOnly proxima_session cookie (same-origin), not a ?token= in
    // the URL — so the token no longer leaks via history/referrer/proxy logs.
    const source = new EventSource(`/api/sessions/${sessionId}/events/stream?after_id=${lastId.current}`, { withCredentials: true })
    source.onopen = () => { if (!closed) setConnected(true) }
    source.onerror = () => { if (!closed) setConnected(false) }
    source.onmessage = event => emit(event.data)
    // NOTE: the server names every SSE event (`event: <type>`), so a type
    // missing here is silently dropped — it only shows up after a full
    // events refetch. Add new event families here or they won't be live.
    const types = [
      'run.queued', 'run.started', 'message.delta', 'reasoning.delta', 'tool.start', 'tool.complete',
      'approval.request', 'approval.auto', 'message.complete', 'run.completed', 'run.failed', 'run.cancelled',
      'warning', 'wiki.draft', 'workflow.draft', 'goal.update',
      'collaboration.child.queued', 'collaboration.child.started', 'collaboration.child.delta',
      'collaboration.child.completed', 'collaboration.child.failed', 'collaboration.child.cancelled',
      'message_review.queued', 'message_review.started', 'message_review.completed',
      'message_review.failed', 'message_review.applied', 'message_review.restored',
    ]
    for (const type of types) {
      source.addEventListener(type, event => emit((event as MessageEvent).data))
    }
    return () => {
      closed = true
      source.close()
      setConnected(false)
    }
  }, [token, sessionId])

  return { connected }
}
