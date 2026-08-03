import React from 'react'
import type { Project, Runner } from '../types'
import { MasterConversation, MasterEmpty } from '../components/master/MasterConversation'
import { MasterComposer } from '../components/master/MasterComposer'
import { MasterWorkPanel } from '../components/master/MasterWorkPanel'
import { MasterFocusPicker, MasterHistoryPicker } from '../components/master/MasterTargetPicker'
import { useMasterState } from '../master/MasterStateProvider'

export { MasterEmpty }

const formatBudget = (seconds: number) =>
  seconds >= 3600
    ? `${Math.round(seconds / 3600)}h`
    : `${Math.round(seconds / 60)}m`

/** Container slug for attach and @ mentions. This never changes Master Focus. */
export function resolveMasterProjectSlug(
  activeProject: Project | null | undefined,
  jobs: { desk_status: string; project_slug?: string | null }[],
): string | undefined {
  if (activeProject?.slug) return activeProject.slug
  const fromTask = jobs.find(job =>
    job.project_slug && ['running', 'queued', 'review'].includes(job.desk_status),
  )
  return fromTask?.project_slug || undefined
}

function connectionLabel(state: ReturnType<typeof useMasterState>['connection']['state']) {
  return {
    'feature-off': 'Master is off',
    connecting: 'Connecting',
    connected: 'Live',
    retrying: 'Reconnecting',
    disconnected: 'Disconnected',
  }[state]
}

export function MasterScreen({
  token,
  runners,
  onOpenJob,
  activeProject = null,
  active = true,
  focusMessageId = null,
  onFocusMessageConsumed,
}: {
  token: string
  runners: Runner[]
  onOpenJob: (id: number, engine?: string) => void
  activeProject?: Project | null
  active?: boolean
  focusMessageId?: number | null
  onFocusMessageConsumed?: () => void
}) {
  const state = useMasterState()
  const { desk, loading, connection, actions } = state
  const setHomeActive = actions.setHomeActive
  const [settingBusy, setSettingBusy] = React.useState(false)

  React.useEffect(() => {
    setHomeActive(active)
    return () => setHomeActive(false)
  }, [active, setHomeActive])

  React.useEffect(() => {
    if (!active || focusMessageId == null || !desk) return
    actions.setHistory({ kind: 'roving' })
    let attempts = 0
    let timer: number | null = null
    const tryFocus = () => {
      const target = document.querySelector<HTMLElement>(
        `[data-message-id="${focusMessageId}"]`,
      )
      if (target) {
        if (typeof target.scrollIntoView === 'function') {
          target.scrollIntoView({ block: 'center', behavior: 'smooth' })
        }
        target.focus?.()
        onFocusMessageConsumed?.()
        return
      }
      if (attempts++ < 30) {
        timer = window.setTimeout(tryFocus, 50)
        return
      }
      onFocusMessageConsumed?.()
    }
    const frame = window.requestAnimationFrame(tryFocus)
    return () => {
      window.cancelAnimationFrame(frame)
      if (timer != null) window.clearTimeout(timer)
    }
  }, [active, actions, desk, focusMessageId, onFocusMessageConsumed])

  if (!active) return null

  const updateSettings = async (settings: Parameters<typeof actions.updateSettings>[0]) => {
    if (settingBusy) return
    setSettingBusy(true)
    actions.clearError()
    try {
      await actions.updateSettings(settings)
    } catch {
      // Provider-owned error surfaces retain the actionable API message.
    } finally {
      setSettingBusy(false)
    }
  }

  if (!state.enabled) {
    return (
      <section className="master-view master-feature-off" aria-labelledby="master-off-title">
        <div className="master-load-error" role="status">
          <h1 id="master-off-title">Master is off</h1>
          <p>This install has not enabled the Master orchestrator.</p>
        </div>
      </section>
    )
  }

  if (loading && !desk) {
    return (
      <section className="master-view master-loading" role="status" aria-label="Opening Master">
        <span className="ui-spinner" />
        <strong>Opening the Master home...</strong>
      </section>
    )
  }

  if (!desk) {
    return (
      <section className="master-view" aria-labelledby="master-load-title">
        <div className="master-load-error" role="alert">
          <h1 id="master-load-title">Master could not open</h1>
          <p>{connection.error || 'The server did not return the Master desk.'}</p>
          <button type="button" className="primary-button" onClick={() => void actions.refresh()}>
            Try again
          </button>
        </div>
      </section>
    )
  }

  const selectedRunner = runners.find(runner => runner.id === desk.backing_runner)
  const eligibleRunners = runners.filter(runner => runner.masterEligible)
  const selectedRunnerEligible = selectedRunner?.masterEligible === true
  const unavailableRunnerLabel = selectedRunner
    ? `${selectedRunner.displayName} (${selectedRunner.masterUnavailableReason || 'not qualified for Master'})`
    : `${desk.backing_runner} (runner is not available)`
  const projectSlug = resolveMasterProjectSlug(activeProject, desk.jobs)
  const connectionProblem = connection.state === 'retrying'
    || connection.state === 'disconnected'
  const masterBusy = state.composer.sending
    || ['queued', 'running'].includes(state.activeRun?.status || '')
  const shellContainer = activeProject == null
    ? null
    : state.fleet.containers.find(container => container.slug === activeProject.slug) || null
  const focusShellContainer = () => {
    if (!shellContainer) return
    void actions.setFocus(shellContainer.id)
      .then(() => actions.setHistory({ kind: 'container', containerId: shellContainer.id }))
      .catch(() => {})
  }

  return (
    <section className="master-view" aria-labelledby="master-title">
      <header className="code-header master-head">
        <div className="master-title">
          <p className="eyebrow">Your orchestrator</p>
          <h1 id="master-title">Master</h1>
        </div>
        <div className="master-controls code-context">
          <MasterFocusPicker />
          <MasterHistoryPicker />
          {shellContainer && (
            <button
              type="button"
              className="ghost-button master-focus-here"
              disabled={masterBusy || state.focus.containerId === shellContainer.id}
              onClick={focusShellContainer}
            >
              Focus Master here
            </button>
          )}
          <span
            className={`master-connection ${connection.state}`}
            role="status"
            aria-live="polite"
          >
            <i aria-hidden="true" />
            {connectionLabel(connection.state)}
          </span>
          <button
            type="button"
            className="ghost-button master-new-task"
            disabled={masterBusy}
            onClick={() => actions.seedDraft('Delegate a new Task: ')}
          >
            New Task
          </button>
          <label className="master-runner-label">
            <span className="sr-only">Backing runner</span>
            <select
              className="ui-select"
              name="master-backing-runner"
              value={desk.backing_runner}
              disabled={settingBusy || masterBusy}
              aria-label="Backing runner"
              onChange={event => void updateSettings({ runner_id: event.target.value })}
            >
              {!selectedRunnerEligible && (
                <option value={desk.backing_runner} disabled>
                  {unavailableRunnerLabel}
                </option>
              )}
              {eligibleRunners.map(runner => (
                <option
                  value={runner.id}
                  key={runner.id}
                  data-master-eligible="true"
                >
                  {runner.displayName}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className={`toggle-pill ${desk.unattended ? 'on' : ''}`}
            aria-pressed={desk.unattended}
            disabled={settingBusy}
            onClick={() => void updateSettings({ unattended: !desk.unattended })}
          >
            <span className="toggle-knob" aria-hidden="true" />
            {settingBusy
              ? 'Saving...'
              : desk.unattended
                ? 'Unattended on'
                : 'Unattended off'}
          </button>
        </div>
      </header>

      <div className="master-body">
        <div
          className="master-capacity"
          aria-label={`${desk.capacity.running} running, ${desk.capacity.free} free, ${desk.capacity.queued} queued`}
        >
          <span>
            <i className="capacity-live" />
            {desk.capacity.running} running / {desk.capacity.free} free
          </span>
          <span>{desk.capacity.queued} queued</span>
          <span>
            {desk.budgets.budget_turns} turns
            {' · '}
            {formatBudget(desk.budgets.budget_wall_seconds)} wall clock
            {desk.budgets.budget_tokens
              ? ` · ${desk.budgets.budget_tokens.toLocaleString()} tokens when reported`
              : ''}
          </span>
        </div>

        {!selectedRunnerEligible && (
          // Governance may refuse; it may never refuse silently (prune B5,
          // #133). Conformance is unchanged - only a runner that proves the
          // chat-only boundary backs Master - but the refusal now says so out
          // loud, instead of hiding inside a closed dropdown until a send 409s.
          <div className="master-error" role="alert">
            <strong>
              Master cannot run on{' '}
              {selectedRunner?.displayName || desk.backing_runner}
            </strong>
            <span>
              {selectedRunner?.masterUnavailableReason
                || 'this runner is not available'}
              . Pick an eligible runner in the backing-runner menu above, or
              install the version this one needs and reload.
            </span>
          </div>
        )}

        {(connection.error || connectionProblem) && (
          <div
            className={`master-error ${connectionProblem ? 'is-connection' : ''}`}
            role={connectionProblem ? 'status' : 'alert'}
          >
            <strong>
              {connection.state === 'retrying'
                ? 'Live updates are reconnecting'
                : connection.state === 'disconnected'
                  ? 'Live updates are disconnected'
                  : 'Master needs a retry'}
            </strong>
            <span>
              {connection.error
                || 'Existing durable state is still shown. No polling fallback is running.'}
            </span>
            <button
              type="button"
              className="ghost-button"
              onClick={() => void actions.refresh()}
            >
              Refresh now
            </button>
          </div>
        )}

        <div className="master-grid">
          <div className="master-conversation" role="region" aria-label="Master home">
            <MasterConversation onOpenJob={onOpenJob} />
            <MasterComposer token={token} projectSlug={projectSlug} />
          </div>
          <MasterWorkPanel token={token} onOpenJob={onOpenJob} />
        </div>
      </div>
    </section>
  )
}
