import React from 'react'
import { MessageContent } from '../chat/MessageContent'
import { CompactTeachingEmpty } from '../ui/CompactTeachingEmpty'
import { useMasterState, type MasterViewMessage } from '../../master/MasterStateProvider'

const cleanMaster = (text: string) =>
  text.replace(/<proxima-tool>\s*\{[\s\S]*?\}\s*<\/proxima-tool>/g, '').trim()

const statusLabel = (status: string) =>
  status === 'review'
    ? 'Needs you'
    : status.charAt(0).toUpperCase() + status.slice(1)

type MasterJobResult = {
  id: number
  title?: string
  status: string
  engine?: string
}

type MasterToolResult = {
  ok: boolean
  tool?: string | null
  result?: {
    job?: MasterJobResult
    jobs?: MasterJobResult[]
    [key: string]: unknown
  }
  error?: { code?: string; message?: string }
}

function parseToolResults(content: string): MasterToolResult[] | null {
  const match = content.match(/^Master tool results:\s*```json\s*([\s\S]*?)\s*```\s*$/)
  if (!match) return null
  try {
    const value = JSON.parse(match[1])
    return Array.isArray(value) ? value : null
  } catch {
    return null
  }
}

function toolResultLabel(tool: MasterToolResult, jobs: MasterJobResult[]) {
  if (!tool.ok && jobs.length) {
    return `Created ${jobs.length} Task${jobs.length === 1 ? '' : 's'}; some stayed queued`
  }
  if (!tool.ok) return 'Product action could not run'
  if (tool.tool === 'delegate_tasks' || tool.tool === 'dispatch_jobs') {
    return `Delegated ${jobs.length} Task${jobs.length === 1 ? '' : 's'}`
  }
  if (tool.tool === 'start_tasks' || tool.tool === 'start_jobs') {
    return `Started ${jobs.length} Task${jobs.length === 1 ? '' : 's'}`
  }
  return ({
    list_containers: 'Containers loaded',
    get_container: 'Container checked',
    get_live_state: 'Live state checked',
    list_tasks: 'Work queue checked',
    list_task_agents: 'Task agents loaded',
    list_recipes: 'Recipes loaded',
    create_attention: 'Decision added to Attention',
    list_projects: 'Containers loaded',
    list_jobs: 'Work queue checked',
    list_worker_agents: 'Task agents loaded',
    list_plans: 'Recipes loaded',
    get_master_settings: 'Master settings checked',
    capacity: 'Capacity checked',
    start_plan: 'Recipe started',
    set_unattended: 'Unattended setting saved',
    set_budgets: 'Budgets saved',
  }[tool.tool || ''] || 'Product action completed')
}

function MasterToolResults({
  tools,
  onOpenJob,
}: {
  tools: MasterToolResult[]
  onOpenJob: (id: number, engine?: string) => void
}) {
  return (
    <div className="master-tool-results" role="group" aria-label="Master tool results">
      {tools.map((tool, toolIndex) => {
        const jobs = tool.result?.jobs || (tool.result?.job ? [tool.result.job] : [])
        return (
          <div className={tool.ok ? 'ok' : 'failed'} key={`${tool.tool}-${toolIndex}`}>
            <b>{toolResultLabel(tool, jobs)}</b>
            {jobs.length ? (
              <ul>
                {jobs.map(job => (
                  <li key={job.id}>
                    <button type="button" onClick={() => onOpenJob(job.id, job.engine)}>
                      <span>{job.title || `Task #${job.id}`}</span>
                      <small>{statusLabel(job.status)}</small>
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
            {!tool.ok && (
              <p>{tool.error?.message || 'Master received an unknown product error.'}</p>
            )}
          </div>
        )
      })}
    </div>
  )
}

const MASTER_LEAD =
  'Talk through an outcome, then let Master delegate and track the durable Tasks.'

export function MasterEmpty() {
  const { actions } = useMasterState()
  const examples = [
    {
      label: 'Audit and fix',
      prompt: 'Audit this Container and delegate independent fixes.',
    },
    {
      label: 'Split the release',
      prompt: 'Split the release into implementation, docs, and verification Tasks.',
    },
    {
      label: 'Review the fleet',
      prompt: 'Review active work and tell me what needs my attention.',
    },
  ]
  return (
    <CompactTeachingEmpty
      className="master-empty"
      testId="master-empty"
      title="What should Master take care of?"
      lead={MASTER_LEAD}
      hints={[
        { label: 'talk', hint: 'Keep one durable conversation with Master' },
        { label: 'delegate', hint: 'Every piece of work becomes a tracked Task' },
        { label: 'review', hint: 'Decisions and review stay visible in the work panel' },
      ]}
      helpTitle="How Master works"
      helpLead="Master chats, delegates, and tracks Tasks. Task agents do the work in one exact Area; Master never edits files itself."
      capabilities={[
        'Describe an outcome and its constraints',
        'Track queued, running, review, completed, and failed Tasks',
        'Open a Task for progress, review, and deliverables',
      ]}
      steps={[
        <>Describe the outcome and any boundaries</>,
        <>Send it once and keep navigating while Master works</>,
        <>Return to the same thread for results and decisions</>,
      ]}
      helpBtnTitle="Conversation, delegation, durable Tasks, and review"
    >
      <div className="master-examples" aria-label="Example messages">
        {examples.map(example => (
          <button
            type="button"
            className="master-example-chip"
            key={example.prompt}
            title={example.prompt}
            onClick={() => actions.seedDraft(example.prompt)}
          >
            {example.label}
          </button>
        ))}
      </div>
    </CompactTeachingEmpty>
  )
}

function messageKey(message: MasterViewMessage, index: number) {
  return message.id ?? message.clientId ?? `master-message-${index}`
}

export function MasterConversation({
  onOpenJob,
}: {
  onOpenJob: (id: number, engine?: string) => void
}) {
  const state = useMasterState()
  // Compatibility fallback keeps older embedded hosts and test harnesses on the
  // canonical roving thread until they adopt the shared history selection.
  const history = state.history ?? { kind: 'roving' as const }
  const historyMessages = state.historyMessages ?? state.messages
  const { activeRun, view, fleet, actions } = state
  const threadRef = React.useRef<HTMLDivElement>(null)
  const restoredRef = React.useRef(false)
  const frameRef = React.useRef<number | null>(null)

  React.useLayoutEffect(() => {
    const thread = threadRef.current
    if (!thread) return
    if (!restoredRef.current) {
      restoredRef.current = true
      thread.scrollTop = view.scrollTop
      return
    }
    if (view.followTail) thread.scrollTop = thread.scrollHeight
  }, [historyMessages.length, view.followTail, view.scrollTop])

  React.useEffect(() => () => {
    if (frameRef.current != null) window.cancelAnimationFrame(frameRef.current)
  }, [])

  const recordScroll = () => {
    if (frameRef.current != null) return
    frameRef.current = window.requestAnimationFrame(() => {
      frameRef.current = null
      const thread = threadRef.current
      if (!thread) return
      const remaining = thread.scrollHeight - thread.scrollTop - thread.clientHeight
      const followTail = remaining <= Math.max(1, thread.clientHeight * 0.05)
      const visible = [...thread.querySelectorAll<HTMLElement>('[data-message-id]')]
        .find(element => element.offsetTop + element.offsetHeight >= thread.scrollTop)
      actions.setScrollState({
        scrollTop: thread.scrollTop,
        followTail,
        anchorMessageId: visible ? Number(visible.dataset.messageId) : null,
      })
    })
  }

  if (!historyMessages.length && !activeRun) {
    return history.kind === 'roving' ? <MasterEmpty /> : (
      <div className="master-history-empty" role="status">
        No Master history is attributed to this view yet.
      </div>
    )
  }

  return (
    <div
      className="master-thread"
      ref={threadRef}
      onScroll={recordScroll}
      role="log"
      aria-label="Master conversation history"
      aria-live="polite"
      aria-relevant="additions text"
      aria-atomic="false"
    >
      {historyMessages.map((message, index) => {
        const content = message.role === 'assistant'
          ? cleanMaster(message.content)
          : message.content
        if (!content) return null
        const tools = message.role === 'system' ? parseToolResults(content) : null
        if (tools) {
          return (
            <MasterToolResults
              key={messageKey(message, index)}
              tools={tools}
              onOpenJob={onOpenJob}
            />
          )
        }
        const metadata = message.master_target
        const targetContainer = fleet.containers.find(
          container => container.id === metadata?.target_container_id,
        )
        const focusContainer = fleet.containers.find(
          container => container.id === metadata?.focus_container_id,
        )
        const targetLabel = metadata?.target_mode === 'explicit'
          ? [
              'Explicit target',
              targetContainer?.identity_label
                || targetContainer?.name
                || (
                  metadata.target_container_id == null
                    ? 'Unavailable Container'
                    : `Container #${metadata.target_container_id}`
                ),
              metadata.target_area_id == null
                ? 'Master chooses Area'
                : `Area #${metadata.target_area_id}`,
            ].join(' · ')
          : metadata?.focus_mode === 'container'
            ? `Let Master route · within ${
              focusContainer?.identity_label
              || focusContainer?.name
              || (
                metadata.focus_container_id == null
                  ? 'Unavailable Container'
                  : `Container #${metadata.focus_container_id}`
              )
            }`
            : metadata
              ? 'Let Master route · Fleet'
              : null
        return (
          <article
            className={`master-message ${message.role}${message.pending ? ' pending' : ''}`}
            key={messageKey(message, index)}
            data-message-id={message.id}
          >
            <strong>
              {message.role === 'user'
                ? 'You'
                : message.role === 'assistant'
                  ? 'Master'
                  : 'Proxima'}
              {message.pending && <span className="master-message-pending">Sending</span>}
            </strong>
            {message.historyKind && (
              <small className={`master-history-kind ${message.historyKind}`}>
                {message.historyKind === 'system-event'
                  ? 'System update'
                  : message.historyKind === 'focus-boundary'
                    ? 'Focus boundary'
                    : 'Focused segment'}
              </small>
            )}
            {targetLabel && (
              <small className="master-message-target">{targetLabel}</small>
            )}
            <MessageContent content={content} />
          </article>
        )
      })}
      {activeRun && (
        <div className="master-working" role="status">
          <span className="ui-spinner" />
          {activeRun.status === 'queued'
            ? 'Master is preparing this turn...'
            : 'Master is thinking and delegating...'}
        </div>
      )}
    </div>
  )
}
