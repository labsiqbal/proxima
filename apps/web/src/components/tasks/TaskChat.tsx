import React from 'react'
import { createRun, cancelRun, listEvents } from '../../api/runs'
import { listMessages } from '../../api/sessions'
import { useEventStream } from '../../hooks/useEventStream'
import type { ChatMessage, RunEvent, Task } from '../../types'
import { ChatThread } from '../chat/ChatThread'
import { Composer } from '../chat/Composer'
import { notify } from '../../lib/notify'

// A focused agent thread bound to one task's session. Reuses the same
// streaming + tool-card rendering as the main chat.
export function TaskChat({ token, task, onTaskChanged }: { token: string; task: Task; onTaskChanged?: () => void }) {
  const sessionId = task.session_id as number
  const [messages, setMessages] = React.useState<ChatMessage[]>([])
  const [events, setEvents] = React.useState<RunEvent[]>([])
  const [busyRun, setBusyRun] = React.useState<number | null>(null)
  const [submitting, setSubmitting] = React.useState(false)
  const [error, setError] = React.useState('')
  const loadSeq = React.useRef(0)
  const actionSeq = React.useRef(0)
  const sessionIdRef = React.useRef(sessionId)
  const mountedRef = React.useRef(true)

  React.useEffect(() => {
    sessionIdRef.current = sessionId
  }, [sessionId])
  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      loadSeq.current += 1
      actionSeq.current += 1
    }
  }, [])
  React.useEffect(() => {
    actionSeq.current += 1
    setSubmitting(false)
  }, [sessionId])

  const load = React.useCallback(async () => {
    const seq = ++loadSeq.current
    const m = await listMessages(token, sessionId)
    const e = await listEvents(token, sessionId)
    if (!mountedRef.current || seq !== loadSeq.current || sessionIdRef.current !== sessionId) return
    setMessages(m.messages); setEvents(e.events)
    // Restore busyRun if the latest run has no terminal event yet — so reopening a
    // task whose agent is still working brings back the Stop button + live stream
    // (mirrors ChatScreen; previously this view went blank/idle on remount).
    const evs = e.events
    const TERMINAL = new Set(['run.completed', 'run.failed', 'run.cancelled'])
    let lastRun: number | null = null
    for (const ev of evs) if (ev.run_id) lastRun = ev.run_id
    setBusyRun(lastRun != null && !evs.some(ev => ev.run_id === lastRun && TERMINAL.has(ev.type)) ? lastRun : null)
  }, [token, sessionId])

  const bufferRef = React.useRef<RunEvent[]>([])
  const timerRef = React.useRef<number | null>(null)
  const busyRunRef = React.useRef<number | null>(null)
  React.useEffect(() => { busyRunRef.current = busyRun }, [busyRun])
  const flush = React.useCallback(() => {
    timerRef.current = null
    const buf = bufferRef.current
    if (!buf.length) return
    bufferRef.current = []
    setEvents(cur => { const seen = new Set(cur.map(e => e.id)); const add = buf.filter(e => !seen.has(e.id)); return add.length ? [...cur, ...add] : cur })
  }, [])

  const onEvent = React.useCallback((event: RunEvent) => {
    bufferRef.current.push(event)
    if (event.type === 'message.delta') { if (timerRef.current == null) timerRef.current = window.setTimeout(flush, 33); return }
    if (timerRef.current != null) { clearTimeout(timerRef.current); timerRef.current = null }
    flush()
    if (['run.completed', 'run.failed', 'run.cancelled'].includes(event.type) && event.run_id === busyRunRef.current) {
      const completedRun = event.run_id
      void load().then(() => {
        if (mountedRef.current) setBusyRun(cur => (cur === completedRun ? null : cur))
      })
      window.dispatchEvent(new CustomEvent('proxima:files-changed'))
      onTaskChanged?.()
      if (event.type === 'run.completed') notify('Task complete', `“${task.title}” is ready for review.`)
    }
  }, [flush, load, onTaskChanged, task.title])

  React.useEffect(() => () => { if (timerRef.current != null) clearTimeout(timerRef.current) }, [])
  const { connected } = useEventStream(token, sessionId, onEvent)
  React.useEffect(() => {
    const p = load()
    const seq = loadSeq.current
    void p.catch(e => {
      if (mountedRef.current && seq === loadSeq.current) setError(String(e))
    })
  }, [load])

  async function submit(text: string) {
    if (submitting || busyRunRef.current) return
    const prompt = text.trim().startsWith('//') ? text.trim().slice(1) : text.trim()
    if (!prompt) return
    const seq = ++actionSeq.current
    setSubmitting(true); setError('')
    try {
      setMessages(cur => [...cur, { role: 'user', content: prompt }])
      const run = await createRun(token, sessionId, { message: prompt, profile_id: null, model: null })
      if (!mountedRef.current || seq !== actionSeq.current || sessionIdRef.current !== sessionId) return
      setBusyRun(run.run_id)
      const eventBody = await listEvents(token, sessionId)
      if (mountedRef.current && seq === actionSeq.current && sessionIdRef.current === sessionId) setEvents(eventBody.events)
      onTaskChanged?.()  // run create flips task → 'doing' server-side; reflect it now
    } catch (e) {
      if (mountedRef.current && seq === actionSeq.current) setError(String(e))
    } finally {
      if (mountedRef.current && seq === actionSeq.current) setSubmitting(false)
    }
  }

  function kickoff() {
    const brief = `Task: ${task.title}` + (task.description ? `\n\n${task.description}` : '') +
      `\n\nComplete this task. Save all results/deliverables (files, documents, etc.) to the \`artifacts/\` folder in this project. ` +
      `Verify the result actually works/is correct before reporting done. When finished, reply briefly with what you made and how to check it.`
    void submit(brief)
  }

  async function stopRun() {
    const runId = busyRunRef.current
    if (!runId) return
    const seq = ++actionSeq.current
    setError('')
    try {
      await cancelRun(token, runId)
      if (!mountedRef.current || seq !== actionSeq.current || sessionIdRef.current !== sessionId) return
      setBusyRun(cur => (cur === runId ? null : cur))
      await load()
      window.dispatchEvent(new CustomEvent('proxima:files-changed'))
      onTaskChanged?.()
    } catch (e) {
      if (mountedRef.current && seq === actionSeq.current) setError(String(e))
    }
  }

  return <div className="task-chat">
    <ChatThread messages={messages} events={events} pendingRunId={busyRun} token={token} slug={task.project_slug || undefined} />
    {messages.length === 0 && !busyRun && <div className="task-kickoff">
      <div><strong>Not started yet.</strong><span className="muted"> Start the agent with this task brief, or type your own instructions.</span></div>
      <button className="primary-button" onClick={kickoff} disabled={submitting}>{submitting ? 'Starting...' : '▶ Run this task'}</button>
    </div>}
    {error && <div className="error-bar">{error}</div>}
    <div className="chat-dock"><div className="chat-controls"><span className={`stream-dot ${connected ? 'on' : ''}`} title={connected ? 'Stream connected' : 'Stream idle'} />{busyRun && <button className="ghost-button" onClick={() => void stopRun()}>Stop</button>}</div><Composer disabled={submitting || !!busyRun} token={token} slug={task.project_slug || undefined} onSubmit={submit} /></div>
  </div>
}
