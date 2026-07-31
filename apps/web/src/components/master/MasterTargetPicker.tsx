import React from 'react'
import { useMasterState } from '../../master/MasterStateProvider'
import {
  areaDisplayLabel,
  projectOptionLabel,
  projectSecondaryContext,
} from './projectContext'

export function MasterFocusPicker() {
  const { focus, fleet, actions } = useMasterState()
  return (
    <div className="master-focus-control">
      <label className="master-focus-picker">
        <span>Focus</span>
        <select
          className="ui-select"
          name="master-focus"
          value={focus.containerId ?? ''}
          disabled={fleet.loading}
          aria-label="Master Focus"
          onChange={event => {
            const containerId = event.target.value ? Number(event.target.value) : null
            void actions.setFocus(containerId)
              .then(() => actions.setHistory(containerId == null
                ? { kind: 'fleet' }
                : { kind: 'container', containerId }))
              .catch(() => {})
          }}
        >
          <option value="">Fleet</option>
          {fleet.containers.map(container => (
            <option value={container.id} key={container.id}>
              {projectOptionLabel(container)}
            </option>
          ))}
        </select>
      </label>
      <MasterPendingFocus />
    </div>
  )
}

export function MasterPendingFocus() {
  const { desk, fleet } = useMasterState()
  const pendingContainer = fleet.containers.find(
    container => container.id === desk?.focus?.pending_container_id,
  )
  if (!desk?.focus?.pending) return null
  return (
    <small className="master-focus-pending" role="status">
      Pending Focus: {pendingContainer
        ? projectOptionLabel(pendingContainer)
        : desk.focus.pending_container_id == null ? 'Fleet' : 'another Project'}
      . Applies after this turn.
    </small>
  )
}

export function MasterHistoryPicker() {
  const state = useMasterState()
  const history = state.history ?? { kind: 'roving' as const }
  const { fleet, actions } = state
  const availableIds = new Set(fleet.containers.map(container => container.id))
  const unavailableIds = [...new Set(
    (state.messages ?? []).flatMap(message => {
      const attribution = message.message_focus
      return [
        attribution?.focus_container_id,
        attribution?.subject_container_id,
      ].filter((id): id is number => (
        typeof id === 'number'
        && Number.isSafeInteger(id)
        && id > 0
        && !availableIds.has(id)
      ))
    }),
  )].sort((left, right) => left - right)
  const value = history.kind === 'roving'
    ? 'roving'
    : history.kind === 'fleet'
      ? 'fleet'
      : `container:${history.containerId}`
  return (
    <label className="master-history-picker">
      <span>History</span>
      <select
        className="ui-select"
        name="master-history"
        aria-label="Master history folder"
        value={value}
        disabled={fleet.loading}
        onChange={event => {
          if (event.target.value === 'roving') {
            actions.setHistory({ kind: 'roving' })
            return
          }
          const containerId = event.target.value === 'fleet'
            ? null
            : Number(event.target.value.replace('container:', ''))
          if (containerId != null && !availableIds.has(containerId)) {
            actions.setHistory({ kind: 'container', containerId })
            return
          }
          void actions.setFocus(containerId)
            .then(() => actions.setHistory(containerId == null
              ? { kind: 'fleet' }
              : { kind: 'container', containerId }))
            .catch(() => {})
        }}
      >
        <option value="roving">Roving thread</option>
        <option value="fleet">Fleet history</option>
        {fleet.containers.map(container => (
          <option value={`container:${container.id}`} key={container.id}>
            {projectOptionLabel(container)}
          </option>
        ))}
        {unavailableIds.map(containerId => (
          <option value={`container:${containerId}`} key={`unavailable:${containerId}`}>
            Unavailable Project #{containerId}
          </option>
        ))}
      </select>
    </label>
  )
}

export function MasterTargetPicker() {
  const { focus, target, fleet, actions } = useMasterState()
  const loadTargetAreas = actions.loadTargetAreas
  const selectedContainer = fleet.containers.find(
    container => container.id === target.containerId,
  )
  const areas = target.containerId == null
    ? null
    : fleet.areasByContainer[target.containerId]
  const areaOptions = areas
    ? [areas.ops_area, ...areas.code_areas]
    : []

  React.useEffect(() => {
    if (target.mode === 'explicit' && target.containerId != null) {
      void loadTargetAreas(target.containerId)
    }
  }, [loadTargetAreas, target.containerId, target.mode])

  const changesFocus = target.mode === 'explicit'
    && target.containerId != null
    && (
      focus.mode !== 'container'
      || focus.containerId !== target.containerId
    )

  return (
    <div className="master-target-picker" aria-label="Master message target">
      <label>
        <span>Target</span>
        <select
          className="ui-select"
          name="master-message-target"
          value={target.containerId ?? ''}
          disabled={fleet.loading}
          aria-label="Master message target"
          onChange={event => {
            actions.setTargetContainer(
              event.target.value ? Number(event.target.value) : null,
            )
          }}
        >
          <option value="">Let Master route</option>
          {fleet.containers.map(container => (
            <option value={container.id} key={container.id}>
              {projectOptionLabel(container)}
            </option>
          ))}
        </select>
      </label>
      {target.mode === 'explicit' && selectedContainer && (
        <details className="master-target-advanced">
          <summary>Area override (advanced)</summary>
          <label>
            <span className="sr-only">Target Area override</span>
            <select
              className="ui-select"
              name="master-target-area"
              value={target.areaId ?? ''}
              aria-label="Target Area override"
              disabled={!areas}
              onChange={event => {
                actions.setTargetArea(
                  event.target.value ? Number(event.target.value) : null,
                )
              }}
            >
              <option value="">
                {areas ? 'Master chooses Area' : 'Loading Areas...'}
              </option>
              {areaOptions.map(area => (
                <option value={area.id} key={area.id}>
                  {area.kind === 'ops'
                    ? 'Operations'
                    : area.rel_path === '.'
                      ? 'Code: repository root'
                      : `Code: ${area.rel_path}`}
                </option>
              ))}
            </select>
          </label>
        </details>
      )}
      {changesFocus && selectedContainer && (
        <p className="master-target-warning" role="status">
          <strong>Sending will Focus Master on {selectedContainer.name}</strong>
          <span>
            {projectSecondaryContext(
              selectedContainer,
              areaDisplayLabel(areas, target.areaId),
            )}
          </span>
        </p>
      )}
      {fleet.error && (
        <p className="master-target-error" role="alert">{fleet.error}</p>
      )}
    </div>
  )
}
