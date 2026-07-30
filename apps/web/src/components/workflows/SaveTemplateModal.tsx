import React from 'react'
import type { WorkflowInput } from '../../types'

const INPUT_KINDS: WorkflowInput['kind'][] = ['text', 'url', 'number', 'file']
const INPUT_ID = /^[A-Za-z][A-Za-z0-9_]*$/

type EditState = { dirty: boolean; valid: boolean }

function defaultError(item: WorkflowInput) {
  const value = item.default?.trim()
  if (!value) return ''
  if (item.kind === 'number' && !Number.isFinite(Number(value))) return 'Default must be a valid number.'
  if (item.kind === 'url') {
    try {
      const url = new URL(value)
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error('unsupported protocol')
    } catch {
      return 'Default must be a complete http:// or https:// URL.'
    }
  }
  return ''
}

function WorkflowInputRow({ item, index, inputs, disabled, onPatch, onRemove, onEditState }: {
  item: WorkflowInput
  index: number
  inputs: WorkflowInput[]
  disabled: boolean
  onPatch: (next: WorkflowInput) => void
  onRemove: () => void
  onEditState: (state: EditState) => void
}) {
  const [draft, setDraft] = React.useState(item)
  React.useEffect(() => setDraft(item), [item])
  const trimmedId = draft.id.trim()
  const trimmedLabel = draft.label.trim()
  const idError = !trimmedId
    ? 'ID is required.'
    : !INPUT_ID.test(trimmedId)
      ? 'Use letters, numbers, and underscores, starting with a letter.'
      : inputs.some((other, otherIndex) => otherIndex !== index && other.id === trimmedId)
        ? 'ID must be unique.'
        : ''
  const labelError = trimmedLabel ? '' : 'Label is required.'
  const valueError = defaultError(draft)
  const valid = !idError && !labelError && !valueError
  const normalized: WorkflowInput = {
    id: trimmedId,
    label: trimmedLabel,
    kind: draft.kind,
    required: draft.required,
    ...(draft.default?.trim() ? { default: draft.default.trim() } : {}),
  }
  const dirty = JSON.stringify(normalized) !== JSON.stringify(item)

  React.useEffect(() => {
    onEditState({ dirty, valid })
  }, [dirty, valid, onEditState])
  const editStateRef = React.useRef(onEditState)
  editStateRef.current = onEditState
  React.useEffect(() => () => {
    editStateRef.current({ dirty: false, valid: true })
  }, [])

  const skipCommit = React.useRef(false)
  const commit = () => {
    if (skipCommit.current) {
      skipCommit.current = false
      return
    }
    if (!dirty || !valid) return
    setDraft(normalized)
    onPatch(normalized)
  }
  const reset = () => setDraft(item)
  const keyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') event.currentTarget.blur()
    if (event.key === 'Escape') {
      skipCommit.current = true
      reset()
      event.currentTarget.blur()
    }
  }

  return <div className="wf-input-row">
    <label><span>Label</span><input
      className="wf-input-cell"
      value={draft.label}
      disabled={disabled}
      placeholder="e.g. Topic"
      aria-label={`Input ${index + 1} label`}
      aria-invalid={!!labelError}
      onChange={event => setDraft(current => ({ ...current, label: event.target.value }))}
      onBlur={commit}
      onKeyDown={keyDown}
    />{labelError && <small className="wf-field-error">{labelError}</small>}</label>
    <label><span>ID</span><span className="wf-input-id"><span>{'{{'}</span><input
      className="wf-input-cell"
      value={draft.id}
      disabled={disabled}
      placeholder="topic"
      aria-label={`Input ${index + 1} ID`}
      aria-invalid={!!idError}
      onChange={event => setDraft(current => ({ ...current, id: event.target.value }))}
      onBlur={commit}
      onKeyDown={keyDown}
    /><span>{'}}'}</span></span>{idError && <small className="wf-field-error">{idError}</small>}</label>
    <label><span>Type</span><select
      className="wf-input-cell"
      value={draft.kind}
      disabled={disabled}
      aria-label={`Input ${index + 1} type`}
      onChange={event => setDraft(current => ({ ...current, kind: event.target.value as WorkflowInput['kind'] }))}
      onBlur={commit}
    >
      {INPUT_KINDS.map(kind => <option key={kind} value={kind}>{kind}</option>)}
    </select></label>
    <label><span>Default <span className="muted">(optional)</span></span><input
      className="wf-input-cell"
      type={draft.kind === 'number' ? 'number' : draft.kind === 'url' ? 'url' : 'text'}
      value={draft.default ?? ''}
      disabled={disabled}
      placeholder="Value used when left blank"
      aria-label={`Input ${index + 1} default`}
      aria-invalid={!!valueError}
      onChange={event => setDraft(current => ({ ...current, default: event.target.value }))}
      onBlur={commit}
      onKeyDown={keyDown}
    />{valueError && <small className="wf-field-error">{valueError}</small>}</label>
    <label className="wf-input-req"><span>Required</span><input
      type="checkbox"
      checked={draft.required}
      disabled={disabled}
      aria-label={`Input ${index + 1} required`}
      onChange={event => {
        const next = { ...draft, required: event.target.checked }
        setDraft(next)
        const canonical: WorkflowInput = {
          ...next,
          id: next.id.trim(),
          label: next.label.trim(),
          ...(next.default?.trim() ? { default: next.default.trim() } : {}),
        }
        if (!next.default?.trim()) delete canonical.default
        if (!idError && !labelError && !defaultError(next)) onPatch(canonical)
      }}
    /></label>
    <button className="row-action danger" title="Remove input" aria-label="Remove input" disabled={disabled} onClick={onRemove}>×</button>
  </div>
}

export function WorkflowInputsEditor({ inputs, disabled = false, onChange, onEditStateChange }: {
  inputs: WorkflowInput[]
  disabled?: boolean
  onChange: (inputs: WorkflowInput[]) => void
  onEditStateChange?: (state: EditState) => void
}) {
  const [rowStates, setRowStates] = React.useState<Record<string, EditState>>({})
  const updateRowState = React.useCallback((key: string, state: EditState) => {
    setRowStates(current => {
      if (!state.dirty && state.valid) {
        if (!(key in current)) return current
        const next = { ...current }
        delete next[key]
        return next
      }
      const previous = current[key]
      if (previous?.dirty === state.dirty && previous.valid === state.valid) return current
      return { ...current, [key]: state }
    })
  }, [])
  React.useEffect(() => {
    const states = Object.values(rowStates)
    onEditStateChange?.({
      dirty: states.some(state => state.dirty),
      valid: states.every(state => state.valid),
    })
  }, [rowStates, onEditStateChange])
  const add = () => {
    const ids = new Set(inputs.map(item => item.id))
    let id = 'field'
    let suffix = 2
    while (ids.has(id)) {
      id = `field_${suffix}`
      suffix += 1
    }
    onChange([...inputs, { id, label: 'New field', kind: 'text', required: false }])
  }

  return <div className="wf-inputs">
    {inputs.map((item, index) => <WorkflowInputRow
      key={item.id}
      item={item}
      index={index}
      inputs={inputs}
      disabled={disabled}
      onPatch={next => onChange(inputs.map((current, i) => i === index ? next : current))}
      onRemove={() => onChange(inputs.filter((_, i) => i !== index))}
      onEditState={state => updateRowState(item.id, state)}
    />)}
    <button className="ghost-button wf-add-step" disabled={disabled}
      onClick={add}>+ Add field</button>
  </div>
}

// Legacy Tasks-row promotion still uses this lightweight metadata dialog. The reusable
// input contract is never edited here: it already lives on the graph's trigger node.
export function SaveTemplateModal({ title, initial, busy, onCancel, onSave }: {
  title: string
  initial?: { description?: string; category?: string }
  busy: boolean
  onCancel: () => void
  onSave: (meta: { name: string; description: string; category: string }) => void
}) {
  const [name, setName] = React.useState(title)
  const [description, setDescription] = React.useState(initial?.description ?? '')
  const [category, setCategory] = React.useState(initial?.category ?? '')

  const close = () => { if (!busy) onCancel() }

  return <div className="modal-scrim" onClick={close}><div className="modal-card graph-template-card" onClick={event => event.stopPropagation()} role="dialog" aria-modal="true">
    <h3>Save as Workflow</h3>
    <label>Name<input autoFocus value={name} disabled={busy} onChange={event => setName(event.target.value)} /></label>
    <label>Category <span className="muted">(optional)</span><input value={category} disabled={busy} placeholder="e.g. content" onChange={event => setCategory(event.target.value)} /></label>
    <label>Description <span className="muted">(optional)</span><textarea rows={2} value={description} disabled={busy} placeholder="What this workflow does" onChange={event => setDescription(event.target.value)} /></label>

    <div className="modal-actions">
      <button className="ghost-button" onClick={close} disabled={busy}>Cancel</button>
      <button className="primary-button" disabled={busy || !name.trim()} onClick={() => onSave({
        name: name.trim(),
        description: description.trim(),
        category: category.trim() || 'other',
      })}>{busy ? 'Saving…' : 'Save as Workflow'}</button>
    </div>
  </div></div>
}
