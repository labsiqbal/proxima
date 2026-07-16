import React from 'react'
import type { AppFeatures, ChatMessage, ChatSession, Profile } from '../../types'
import { iterateWorkflow } from '../../api/workflows'
import { listMessages } from '../../api/sessions'
import { createRun } from '../../api/runs'
import { useRunStream } from '../../hooks/useRunStream'
import { ChatThread } from '../chat/ChatThread'
import { Composer } from '../chat/Composer'
import { buildRecipePrompt, buildRunThroughPrompt, parseRecipeDraft, stripRecipeBlock, type RecipePatch, type RecipeSnapshot } from './recipePrompt'

// What the step outline can drive on the chat: run the recipe up to a given step and
// show the result in the thread. The outline owns the buttons; the chat owns the session.
export type WorkflowChatHandle = { runThrough: (stepIndex: number, stepName?: string) => void }

/** The recipe editor's authoring chat: a conversation *about* the workflow, pinned to
 *  it and kept out of Code — the same relationship Design Studio's chat has to a canvas.
 *
 *  It does two jobs in one thread, disambiguated the way Design does it (mode in the
 *  prompt, not a second box):
 *   - Authoring — typing drives the form. The run carries a fat prompt (mode + schema +
 *     the live recipe), the thread shows only the short text, and any `<workflow-recipe>`
 *     the agent returns is parsed straight into the form beside it.
 *   - Testing — "Run test" saves the form, then asks the agent to run the recipe. That
 *     reply carries no recipe block, so the form is left alone.
 *
 *  Get-or-create `/iterate` means reopening the editor resumes the same conversation.
 *  It composes ChatThread + Composer + useRunStream rather than mounting ChatScreen,
 *  whose collaboration modes, review sidecar and header do not belong beside a form.
 */
export const WorkflowChat = React.forwardRef<WorkflowChatHandle, {
  token: string
  features: AppFeatures
  profiles: Profile[]
  activeProfile: Profile | null
  projectSlug: string | null
  /** null while the recipe is still an unsaved draft. */
  workflowId: number | null
  /** The live form, injected into every authoring prompt so the agent edits what is on screen. */
  recipe: RecipeSnapshot
  /** Saves the draft and resolves its id, so the chat works before a first manual save. */
  onEnsureSaved: () => Promise<number | null>
  /** Folds an agent-returned recipe into the form beside the chat. The form, never the
   *  database directly: the recipe is on screen, so a background write would leave the
   *  editor stale and let the next Save undo the agent's work. */
  onApplyRecipe: (patch: RecipePatch) => void
}>(function WorkflowChat({ token, features, profiles, activeProfile, projectSlug, workflowId, recipe, onEnsureSaved, onApplyRecipe }, ref) {
  const [session, setSession] = React.useState<ChatSession | null>(null)
  const sessionRef = React.useRef<ChatSession | null>(null)
  const [messages, setMessages] = React.useState<ChatMessage[]>([])
  const [opening, setOpening] = React.useState(false)
  const [applied, setApplied] = React.useState('')
  const [error, setError] = React.useState('')
  const mounted = React.useRef(true)
  const openSeq = React.useRef(0)
  // Newest assistant message already scanned for a recipe, so we apply each reply once.
  const appliedMsgId = React.useRef(0)
  // Keep the live recipe reachable from callbacks without resubscribing them.
  const recipeRef = React.useRef(recipe)
  React.useEffect(() => { recipeRef.current = recipe }, [recipe])
  React.useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; openSeq.current += 1 }
  }, [])

  const { events, busyRun, setBusyRun } = useRunStream(token, session?.id ?? null)

  const absorb = React.useCallback((rows: ChatMessage[]) => {
    // Apply the newest assistant reply, once. A test reply has no recipe block, so
    // parseRecipeDraft returns null and the form is untouched. Parse the raw content,
    // but display it with the recipe JSON stripped so the chat stays readable.
    for (let i = rows.length - 1; i >= 0; i--) {
      const m = rows[i]
      if (m.role !== 'assistant') continue
      if ((m.id ?? 0) <= appliedMsgId.current) break
      appliedMsgId.current = m.id ?? 0
      const patch = parseRecipeDraft(m.content || '')
      if (patch) {
        onApplyRecipe(patch)
        setApplied('Applied the agent’s changes to the recipe.')
      }
      break
    }
    setMessages(rows.map(m => m.role === 'assistant' && m.content
      ? { ...m, content: stripRecipeBlock(m.content) } : m))
  }, [onApplyRecipe])

  const reload = React.useCallback(async (sessionId: number) => {
    const body = await listMessages(token, sessionId)
    if (mounted.current) absorb(body.messages)
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
    void reload(session.id).then(() => { if (mounted.current) setBusyRun(null) })
  }, [events, session, busyRun, reload, setBusyRun])

  // Open the iterate session if it isn't open yet, and return it. Both the idle
  // "Start chat" and an outline-driven Run test go through here, so a test can open the
  // chat on demand rather than making the owner start it first.
  async function ensureSession(): Promise<ChatSession | null> {
    if (sessionRef.current) return sessionRef.current
    if (opening) return null
    const seq = ++openSeq.current
    setOpening(true); setError('')
    try {
      const id = workflowId ?? await onEnsureSaved()
      if (id == null) return null          // save failed; the editor reports why
      const s = await iterateWorkflow(token, id)
      if (!mounted.current || seq !== openSeq.current) return null
      sessionRef.current = s
      setSession(s)
      const body = await listMessages(token, s.id)
      if (mounted.current && seq === openSeq.current) {
        // Adopt existing replies as already-applied — reopening must not re-apply an
        // old recipe over edits made since.
        appliedMsgId.current = body.messages.reduce((m, x) => Math.max(m, x.id ?? 0), 0)
        setMessages(body.messages)
      }
      return s
    } catch (e) {
      if (mounted.current && seq === openSeq.current) setError(String(e))
      return null
    } finally {
      if (mounted.current && seq === openSeq.current) setOpening(false)
    }
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

  // Typing edits the recipe: fat authoring prompt in, short text on screen.
  const author = async (text: string) => {
    const s = sessionRef.current
    if (s) await fire(s.id, buildRecipePrompt(recipeRef.current, text), text)
  }

  // Run the recipe up to and including a step and show its result — a step's output only
  // makes sense with its upstream context, so testing step N runs steps 1..N. The outline
  // triggers this; it saves first so the run reflects unsaved edits, and opens the chat if
  // needed. No authoring wrapper, so the reply carries no recipe block and the form stands.
  React.useImperativeHandle(ref, () => ({
    runThrough: (stepIndex, _stepName) => { void (async () => {
      const s = await ensureSession()
      if (!s) return
      // Inline the live recipe so the test reflects the form on screen, not the saved
      // copy or whatever the session last saw.
      await fire(
        s.id,
        buildRunThroughPrompt(recipeRef.current, stepIndex),
        `Run through step ${stepIndex + 1}`,
      )
    })() },
  }), [token, activeProfile, projectSlug])

  if (!session) {
    return <aside className="wf-chat wf-chat-idle">
      <p className="eyebrow">Workflow chat</p>
      <p className="muted">
        Describe the workflow and the agent fills in the steps; ask for changes and it edits
        them. {workflowId == null ? 'Starting saves the draft first. ' : ''}
        Separate from Code, scoped to this recipe.
      </p>
      {error && <div className="error-bar" role="alert">{error}</div>}
      <button className="primary-button" onClick={() => void ensureSession()} disabled={opening}>
        {opening ? 'Opening…' : 'Start chat'}
      </button>
    </aside>
  }

  return <aside className="wf-chat">
    <div className="wf-chat-head">
      <p className="eyebrow">Workflow chat</p>
      {busyRun != null && <span className="muted wf-chat-running">Running…</span>}
    </div>
    {applied && <div className="wf-chat-note" role="status">{applied}</div>}
    {error && <div className="error-bar" role="alert">{error}</div>}
    <div className="wf-chat-thread">
      <ChatThread
        messages={messages}
        events={events}
        pendingRunId={busyRun}
        pendingText={busyRun != null ? 'Working…' : ''}
        token={token}
        slug={projectSlug || undefined}
        agentName={activeProfile?.name}
        profiles={profiles}
        features={features}
        onQuickReply={text => void author(text)}
        onMessageUpdated={() => { if (session) void reload(session.id) }}
      />
    </div>
    <Composer
      token={token}
      slug={projectSlug || undefined}
      features={features}
      placeholder="Describe or change the workflow…"
      textareaLabel="Author the workflow"
      promptModes={false}
      submitIconOnly
      submitLabel="Send"
      submittingLabel="Sending…"
      onSubmit={author}
    />
  </aside>
})
