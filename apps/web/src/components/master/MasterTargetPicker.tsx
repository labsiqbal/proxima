import React from 'react'
import { useMasterState } from '../../master/MasterStateProvider'

function containerLabel(name: string, identityLabel: string | null): string {
  return identityLabel && identityLabel !== name
    ? `${identityLabel} (${name})`
    : name
}

export function MasterFocusPicker() {
  const { focus, fleet, actions } = useMasterState()
  return (
    <label className="master-focus-picker">
      <span>Focus</span>
      <select
        className="ui-select"
        value={focus.containerId ?? ''}
        disabled={fleet.loading}
        aria-label="Master Focus"
        onChange={event => {
          void actions.setFocus(
            event.target.value ? Number(event.target.value) : null,
          ).catch(() => {})
        }}
      >
        <option value="">Fleet</option>
        {fleet.containers.map(container => (
          <option value={container.id} key={container.id}>
            {containerLabel(container.name, container.identity_label)}
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
              {containerLabel(container.name, container.identity_label)}
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
          Sending will Focus Master on {selectedContainer.identity_label || selectedContainer.name}
        </p>
      )}
      {fleet.error && (
        <p className="master-target-error" role="alert">{fleet.error}</p>
      )}
    </div>
  )
}
