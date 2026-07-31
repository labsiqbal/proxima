import React from 'react'
import {
  deferMasterDecision,
  getMasterDecision,
  resolveMasterDecision,
  type MasterDecision,
} from '../../api/master'

type Props = {
  token: string
  decision: MasterDecision
  compact?: boolean
  onChanged: (decision: MasterDecision) => void | Promise<void>
  onOpenJob?: (id: number, engine?: string) => void
  onOpenMaster?: (originMessageId: number | null) => void
}

function stateLabel(state: MasterDecision['state']) {
  if (state === 'pending') return 'Needs your response'
  if (state === 'deferred') return 'Deferred'
  return 'Resolved'
}

function actionError(caught: unknown) {
  const message = caught instanceof Error ? caught.message : String(caught)
  return message.replace(/^[A-Z]+ \S+ failed \(\d+\): /, '')
}

/** Format API timestamps that may be SQLite (`YYYY-MM-DD HH:MM:SS`) or canonical ISO-Z. */
export function formatDecisionTime(value: string): string {
  const normalized = value.replace(' ', 'T')
  const timestamp = new Date(
    normalized.endsWith('Z') ? normalized : `${normalized}Z`,
  )
  return Number.isNaN(timestamp.getTime()) ? 'Unknown time' : timestamp.toLocaleString()
}

export function MasterDecisionCard({
  token,
  decision: incoming,
  compact = false,
  onChanged,
  onOpenJob,
  onOpenMaster,
}: Props) {
  const [decision, setDecision] = React.useState(incoming)
  const [response, setResponse] = React.useState('')
  const [busy, setBusy] = React.useState<'resolve' | 'defer' | ''>('')
  const [error, setError] = React.useState('')

  React.useEffect(() => {
    setDecision(incoming)
    if (incoming.state === 'resolved') {
      setResponse(incoming.response?.value || '')
    }
  }, [incoming])

  const refreshAfterConflict = async (message: string) => {
    try {
      const current = await getMasterDecision(token, decision.id)
      setDecision(current)
      await onChanged(current)
    } catch {
      // Preserve the original action error when reconciliation is unavailable.
    }
    setError(message)
  }

  const resolve = async (event: React.FormEvent) => {
    event.preventDefault()
    if (busy || !response.trim()) return
    setBusy('resolve')
    setError('')
    try {
      const updated = await resolveMasterDecision(
        token,
        decision.id,
        decision.version,
        response.trim(),
      )
      setDecision(updated)
      await onChanged(updated)
    } catch (caught) {
      await refreshAfterConflict(
        actionError(caught),
      )
    } finally {
      setBusy('')
    }
  }

  const defer = async () => {
    if (busy || decision.state !== 'pending') return
    setBusy('defer')
    setError('')
    try {
      const updated = await deferMasterDecision(
        token,
        decision.id,
        decision.version,
      )
      setDecision(updated)
      await onChanged(updated)
    } catch (caught) {
      await refreshAfterConflict(
        actionError(caught),
      )
    } finally {
      setBusy('')
    }
  }

  const shape = decision.response_shape
  const unresolved = decision.state !== 'resolved'
  return (
    <article
      className={`master-decision-card ${decision.state}${compact ? ' compact' : ''}`}
      aria-label={`Decision: ${decision.title}`}
    >
      <header>
        <span className={`master-decision-state ${decision.state}`}>
          {stateLabel(decision.state)}
        </span>
        <h3>{decision.title}</h3>
      </header>
      <p className="master-decision-prompt">{decision.prompt}</p>
      <p className="master-decision-context">{decision.context}</p>
      {decision.state === 'deferred' && decision.deferred_at && (
        <small className="master-decision-timestamp">
          Deferred by owner {formatDecisionTime(decision.deferred_at)}
        </small>
      )}

      {unresolved ? (
        <form onSubmit={event => void resolve(event)}>
          {shape.type === 'choice' ? (
            <fieldset>
              <legend>Choose one response</legend>
              {shape.choices.map(choice => (
                <label key={choice.id}>
                  <input
                    type="radio"
                    name={`master-decision-${decision.id}`}
                    value={choice.id}
                    checked={response === choice.id}
                    onChange={event => setResponse(event.target.value)}
                  />
                  <span>
                    <strong>{choice.label}</strong>
                    {choice.description && <small>{choice.description}</small>}
                  </span>
                </label>
              ))}
            </fieldset>
          ) : (
            <label className="master-decision-text">
              <span>Your response</span>
              <textarea
                rows={compact ? 2 : 3}
                maxLength={shape.max_length}
                placeholder={shape.placeholder}
                value={response}
                onChange={event => setResponse(event.target.value)}
              />
            </label>
          )}
          {error && <p className="master-decision-error" role="alert">{error}</p>}
          <div className="master-decision-actions">
            <button
              type="submit"
              className="primary-button"
              disabled={!!busy || !response.trim()}
            >
              {busy === 'resolve'
                ? 'Sending...'
                : decision.state === 'deferred'
                  ? 'Resolve now'
                  : 'Send decision'}
            </button>
            {decision.state === 'pending' && (
              <button
                type="button"
                className="ghost-button"
                disabled={!!busy}
                onClick={() => void defer()}
              >
                {busy === 'defer' ? 'Deferring...' : 'Decide later'}
              </button>
            )}
          </div>
        </form>
      ) : (
        <div className="master-decision-response" role="status">
          <span>Owner response</span>
          <strong>{decision.response?.label || 'Response recorded'}</strong>
          {decision.resolved_at && (
            <small>
              Recorded by owner {formatDecisionTime(decision.resolved_at)}
            </small>
          )}
        </div>
      )}

      <nav aria-label={`Links for ${decision.title}`}>
        {decision.task && onOpenJob && (
          <button
            type="button"
            className="text-button"
            onClick={() => onOpenJob(decision.task!.id, decision.task!.engine)}
          >
            Open Task #{decision.task.id}
          </button>
        )}
        {onOpenMaster && (
          <button
            type="button"
            className="text-button"
            onClick={() => onOpenMaster(decision.origin_message_id)}
          >
            Open Master conversation
          </button>
        )}
      </nav>
    </article>
  )
}
