import React from 'react'
import type { ChatMessage, Profile } from '../../types'
import { listMessages } from '../../api/sessions'
import { createRun } from '../../api/runs'
import { useRunStream } from '../../hooks/useRunStream'
import { ChatThread } from '../chat/ChatThread'
import { Composer } from '../chat/Composer'
import { CompactTeachingEmpty } from '../ui/CompactTeachingEmpty'
import { IconWorkflows } from '../shell/icons'

// What an outline can drive on the chat: run the artifact up to a given step and show the
// result in the thread. The outline owns the buttons; the chat owns the session.
export type WorkflowChatHandle = { runThrough: (stepIndex: number, stepName?: string) => void }

/** SessionStorage key: last authoring run id whose graph block was folded into the canvas.
 *  Survives panel close / in-app navigation so reopening does not re-apply a reply the
 *  owner already accepted (and may have hand-edited), while still recovering a run that
 *  finished while the chat was unmounted. */
export function appliedRunKey(sessionId: number): string {
  return `proxima.authoring.appliedRun.${sessionId}`
}

function WorkflowAuthoringEmpty() {
  return <CompactTeachingEmpty
    className="workflow-authoring-empty"
    testId="workflow-authoring-empty"
    title="Shape this workflow"
    lead="Describe steps, dependencies, inputs, or review gates. The agent updates the plan beside this chat."
    hints={[
      { label: 'branch', hint: 'Ask for independent steps to run in parallel' },
      { label: '@', hint: '@-mention project files and deliverables' },
      { label: 'test', hint: 'Test a selected node here before starting the plan' },
    ]}
    helpTitle="How Plan Chat works"
    helpLead="Plan Chat edits the workflow on the canvas beside it. It does not start the plan or change a hidden copy."
    capabilities={[
      'Turn a goal into connected workflow nodes',
      'Revise branches, inputs, agents, and review gates',
      'Test a node in this chat without starting the plan',
    ]}
    steps={[
      'Describe what the workflow should do',
      'Review the updated canvas and node details',
      'Run when the plan is ready',
    ]}
    helpBtnTitle="Workflow nodes, branches, inputs, review gates, and node testing"
    mark={<IconWorkflows size={30} />}
    markTitle="Plan Chat authors the workflow beside it"
  />
}

function readAppliedRun(sessionId: number): number | null {
  try {
    const raw = sessionStorage.getItem(appliedRunKey(sessionId))
    const n = Number(raw)
    return Number.isFinite(n) && n > 0 ? n : null
  } catch {
    return null
  }
}

function writeAppliedRun(sessionId: number, runId: number) {
  try {
    sessionStorage.setItem(appliedRunKey(sessionId), String(runId))
  } catch { /* private mode / quota — live apply still works */ }
}

/** An authoring chat: a conversation *about* one workflow artifact, pinned to it and kept
 *  out of Code — the same relationship Design Studio's chat has to a canvas. It drives
 *  either a linear recipe form or a graph canvas; what differs between them is only the
 *  prompt schema and where the reply lands, so those arrive as props rather than forking
 *  the session/stream plumbing twice.
 *
 *  It does two jobs in one thread, disambiguated the way Design does it (mode in the
 *  prompt, not a second box):
 *   - Authoring — typing drives the artifact. The run carries a fat prompt (mode + schema
 *     + the live artifact), the thread shows only the short text, and any block the agent
 *     returns is parsed straight into what is on screen beside it.
 *   - Testing — an optional per-step test prompt. Its reply carries no artifact block, so
 *     the artifact is left alone.
 *
 *  `ensureSession` is get-or-create, so reopening resumes the same conversation. It
 *  composes ChatThread + Composer + useRunStream rather than mounting ChatScreen, whose
 *  collaboration modes, review sidecar and header do not belong beside an editor.
 */
export const AuthoringChat = React.forwardRef<WorkflowChatHandle, {
  token: string
  profiles: Profile[]
  activeProfile: Profile | null
  projectSlug: string | null
  /** Get-or-create the chat this artifact is pinned to, resolving its session id. Null
   *  means it could not be opened — the caller reports why. */
  ensureSession: () => Promise<number | null>
  /** Wraps the owner's text in the artifact's authoring prompt, closing over whatever is
   *  live on screen so the agent edits that rather than a stale copy. */
  buildPrompt: (instruction: string) => string
  /** Folds an agent reply into the artifact on screen and reports whether it did. The
   *  screen, never the database directly: the artifact is on screen, so a background
   *  write would leave the editor stale and let the next Save undo the agent's work. */
  applyReply: (raw: string) => boolean
  /** Hides the artifact JSON from the thread, keeping the summary sentence. */
  stripBlock: (raw: string) => string
  /** Optional per-step test prompt; without it the chat only authors. */
  buildTestPrompt?: (stepIndex: number) => string
  idleHint: React.ReactNode
  placeholder: string
  /** Files the owner can @-mention in the composer. */
  mentionItems?: import('../ui/MentionTextarea').MentionItem[]
  /** Fired once into a fresh thread on mount — the home hero's "describe it and the
   *  agent draws it" hand-off. Ignored when the thread already has messages. */
  initialMessage?: string
  onInitialConsumed?: () => void
  /** When true (graph Plan Chat), open the pinned session immediately so leave/reopen
   *  mid-run resumes Running without an extra "Start chat" click. */
  autoOpen?: boolean
}>(function AuthoringChat({ token, profiles, activeProfile, projectSlug, ensureSession: ensure, buildPrompt, applyReply, stripBlock, buildTestPrompt, idleHint, placeholder, mentionItems, initialMessage, onInitialConsumed, autoOpen = false }, ref) {
  const [session, setSession] = React.useState<number | null>(null)
  const sessionRef = React.useRef<number | null>(null)
  const [messages, setMessages] = React.useState<ChatMessage[]>([])
  const [opening, setOpening] = React.useState(false)
  const [applied, setApplied] = React.useState('')
  const [error, setError] = React.useState('')
  const mounted = React.useRef(true)
  // Share one in-flight open so Start chat, Test in chat, and a Strict Mode
  // double-effect all await the same promise instead of racing on `opening`
  // state (a second caller used to return null while the first was abandoned
  // by openSeq++, leaving the button stuck on Opening…).
  const openPromiseRef = React.useRef<Promise<number | null> | null>(null)
  // Newest assistant message already scanned, so we apply each reply exactly once.
  const appliedMsgId = React.useRef(0)
  // Keep the newest callbacks reachable without resubscribing the stream to them.
  const live = React.useRef({ buildPrompt, applyReply, stripBlock, buildTestPrompt })
  live.current = { buildPrompt, applyReply, stripBlock, buildTestPrompt }
  React.useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const { events, setEvents, busyRun, setBusyRun, restore } = useRunStream(token, session)

  const markApplied = React.useCallback((sessionId: number, message: ChatMessage) => {
    appliedMsgId.current = Math.max(appliedMsgId.current, message.id ?? 0)
    if (message.run_id != null) writeAppliedRun(sessionId, message.run_id)
  }, [])

  const absorb = React.useCallback((sessionId: number, rows: ChatMessage[]) => {
    // Apply the newest assistant reply, once. A conversational or test reply carries no
    // artifact block, so applyReply reports false and nothing on screen moves. Parse the
    // raw content but display it stripped, so the chat stays readable.
    for (let i = rows.length - 1; i >= 0; i--) {
      const m = rows[i]
      if (m.role !== 'assistant') continue
      if ((m.id ?? 0) <= appliedMsgId.current) break
      markApplied(sessionId, m)
      if (live.current.applyReply(m.content || '')) setApplied('Applied the agent’s changes.')
      break
    }
    setMessages(rows.map(m => m.role === 'assistant' && m.content
      ? { ...m, content: live.current.stripBlock(m.content) } : m))
  }, [markApplied])

  const displayMessages = React.useCallback((rows: ChatMessage[]) => {
    setMessages(rows.map(m => m.role === 'assistant' && m.content
      ? { ...m, content: live.current.stripBlock(m.content) } : m))
  }, [])

  const reload = React.useCallback(async (sessionId: number) => {
    const body = await listMessages(token, sessionId)
    if (mounted.current) absorb(sessionId, body.messages)
  }, [token, absorb])

  // A finished run means the stored reply exists; pull it in, then drop the live bubble.
  // Guard on THIS run's id: events accumulate across runs, so reacting to any
  // run.completed would clear the live bubble the instant a new run starts (killing the
  // thinking indicator) because a prior run's completed event is still in the array.
  React.useEffect(() => {
    if (busyRun == null || !session) return
    const done = events.find(e =>
      (e.type === 'run.completed' || e.type === 'run.failed' || e.type === 'run.cancelled')
      && e.run_id === busyRun)
    if (!done) return
    void reload(session).then(() => { if (mounted.current) setBusyRun(null) })
  }, [events, session, busyRun, reload, setBusyRun])

  // A hero hand-off opens the chat itself and speaks first — but never into a thread
  // that already has history, where "my" first message would not be the first word.
  const initialFired = React.useRef(false)
  React.useEffect(() => {
    if (!initialMessage || initialFired.current) return
    initialFired.current = true
    void (async () => {
      const s = await ensureSession()
      if (s == null || !mounted.current) return
      if (appliedMsgId.current === 0 && messages.length === 0) {
        await fire(s, live.current.buildPrompt(initialMessage), initialMessage)
      }
      onInitialConsumed?.()
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialMessage])

  // Graph Plan Chat: open the pinned session on mount so reopening the panel mid-run
  // (or after a completed authoring turn) resumes without a dead "Start chat" click.
  React.useEffect(() => {
    if (!autoOpen) return
    void ensureSession()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoOpen])

  // Open the chat if it isn't open yet, and return it. Both the idle "Start chat" and an
  // outline-driven test go through here, so a test can open the chat on demand rather
  // than making the owner start it first. Concurrent callers share one promise so a
  // Test-in-chat click during Strict Mode's effect cycle cannot leave Opening… stuck.
  function ensureSession(): Promise<number | null> {
    if (sessionRef.current != null) return Promise.resolve(sessionRef.current)
    if (openPromiseRef.current) return openPromiseRef.current
    setOpening(true); setError('')
    const pending = (async (): Promise<number | null> => {
      try {
        const s = await ensure()
        if (s == null) {
          // Graph jobs normally own a session; surface a real error when one is missing
          // instead of leaving the idle card silent after Opening… ends.
          setError('Could not open the plan chat for this plan.')
          return null
        }
        sessionRef.current = s
        setSession(s)

        // restore() is pure (no state writes); apply under our own mounted guard so a
        // panel unmount mid-await cannot clobber a later open. Mirrors ChatScreen +
        // Design Studio: re-attach busyRun, recover a graph that finished while closed.
        const [body, r] = await Promise.all([
          listMessages(token, s),
          restore(s),
        ])
        if (!mounted.current || sessionRef.current !== s) return s

        setEvents(r.events)

        if (r.running && r.lastRun != null) {
          // Live stream will deliver the result; do not re-apply historical blocks.
          appliedMsgId.current = body.messages.reduce((m, x) => Math.max(m, x.id ?? 0), 0)
          displayMessages(body.messages)
          setBusyRun(r.lastRun)
          return s
        }

        // Finished while closed (or first open after a prior authoring turn): fold the
        // last completed run's graph onto the canvas once, unless we already applied it.
        const already = readAppliedRun(s)
        if (r.completed && r.lastRun != null && already !== r.lastRun) {
          const assistants = body.messages.filter(m => m.role === 'assistant' && m.run_id === r.lastRun)
          const last = assistants[assistants.length - 1]
          if (last) {
            // Allow absorb to treat this assistant message as new.
            appliedMsgId.current = Math.max(0, (last.id ?? 1) - 1)
            absorb(s, body.messages)
            return s
          }
        }

        // Historical thread with nothing new to apply — baseline ids so we do not
        // re-apply old graphs over hand edits on every panel open.
        appliedMsgId.current = body.messages.reduce((m, x) => Math.max(m, x.id ?? 0), 0)
        displayMessages(body.messages)
        setBusyRun(null)
        return s
      } catch (e) {
        setError(String(e))
        return null
      } finally {
        openPromiseRef.current = null
        // Always clear Opening… — React 18 Strict Mode flips mounted false/true around
        // the same instance; gating on mounted+seq previously abandoned the flag forever.
        setOpening(false)
      }
    })()
    openPromiseRef.current = pending
    return pending
  }

  async function fire(sessionId: number, message: string, displayMessage: string) {
    setApplied(''); setError('')
    const run = await createRun(token, sessionId, {
      message,
      display_message: displayMessage,
      profile_id: activeProfile?.id ?? null,
      project_slug: projectSlug,
    })
    if (!mounted.current) return
    setBusyRun(run.run_id)
    await reload(sessionId)
  }

  // Typing edits the artifact: fat authoring prompt in, short text on screen.
  const author = async (text: string) => {
    const s = sessionRef.current
    if (s != null) await fire(s, live.current.buildPrompt(text), text)
  }

  // Run the recipe up to and including a step and show its result — a step's output only
  // makes sense with its upstream context, so testing step N runs steps 1..N. The outline
  // triggers this; it saves first so the run reflects unsaved edits, and opens the chat if
  // needed. No authoring wrapper, so the reply carries no recipe block and the form stands.
  React.useImperativeHandle(ref, () => ({
    runThrough: (stepIndex, stepName) => { void (async () => {
      const build = live.current.buildTestPrompt
      if (!build) return
      const s = await ensureSession()
      if (s == null) return
      // The prompt inlines whatever is live on screen, so a test reflects unsaved edits
      // rather than the saved copy or whatever the session last saw.
      await fire(s, build(stepIndex), stepName ? `Test “${stepName}”` : `Run through step ${stepIndex + 1}`)
    })() },
  }), [token, activeProfile, projectSlug])

  if (session == null) {
    return <aside className="wf-chat wf-chat-idle">
      <p className="eyebrow">Plan chat</p>
      <p className="muted">{idleHint}</p>
      {error && <div className="error-bar" role="alert">{error}</div>}
      <button className="primary-button" onClick={() => void ensureSession()} disabled={opening}>
        {opening ? 'Opening…' : 'Start chat'}
      </button>
    </aside>
  }

  return <aside className="wf-chat">
    <div className="wf-chat-head">
      <p className="eyebrow">Plan chat</p>
      {busyRun != null && <span className="muted wf-chat-running">Running…</span>}
    </div>
    {applied && <div className="wf-chat-note" role="status">{applied}</div>}
    {error && <div className="error-bar" role="alert">{error}</div>}
    <div className="wf-chat-thread">
      <ChatThread
        messages={messages}
        events={events}
        pendingRunId={busyRun}
        token={token}
        slug={projectSlug || undefined}
        agentName={activeProfile?.name}
        profiles={profiles}
        emptyContent={<WorkflowAuthoringEmpty />}
        onQuickReply={text => void author(text)}
        onMessageUpdated={() => { if (session != null) void reload(session) }}
      />
    </div>
    <Composer
      token={token}
      mentionItems={mentionItems}
      slug={projectSlug || undefined}
      placeholder={placeholder}
      textareaLabel="Author the plan"
      promptModes={false}
      submitIconOnly
      submitLabel="Send"
      submittingLabel="Sending…"
      onSubmit={author}
    />
  </aside>
})
