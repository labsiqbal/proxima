import React from 'react'
import type { WorkflowInput } from '../../types'

const INPUT_KINDS: WorkflowInput['kind'][] = ['text', 'url', 'number', 'file']
const slugifyId = (value: string) =>
  value.toLowerCase().trim().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')

export function WorkflowInputsEditor({ inputs, disabled = false, onChange }: {
  inputs: WorkflowInput[]
  disabled?: boolean
  onChange: (inputs: WorkflowInput[]) => void
}) {
  const patch = (index: number, next: Partial<WorkflowInput>) =>
    onChange(inputs.map((item, i) => i === index ? { ...item, ...next } : item))

  return <div className="wf-inputs">
    {inputs.map((item, index) => <div className="wf-input-row" key={index}>
      <label><span>Label</span><input className="wf-input-cell" value={item.label} disabled={disabled} placeholder="e.g. Topic"
        aria-label={`Input ${index + 1} label`}
        onChange={event => patch(index, { label: event.target.value, id: item.id.trim() ? item.id : slugifyId(event.target.value) })} /></label>
      <label><span>ID</span><span className="wf-input-id"><span>{'{{'}</span><input className="wf-input-cell" value={item.id} disabled={disabled} placeholder="topic"
        aria-label={`Input ${index + 1} ID`}
        onChange={event => patch(index, { id: slugifyId(event.target.value) })} /><span>{'}}'}</span></span></label>
      <label><span>Type</span><select className="wf-input-cell" value={item.kind} disabled={disabled}
        aria-label={`Input ${index + 1} type`}
        onChange={event => patch(index, { kind: event.target.value as WorkflowInput['kind'] })}>
        {INPUT_KINDS.map(kind => <option key={kind} value={kind}>{kind}</option>)}
      </select></label>
      <label className="wf-input-req"><span>Required</span><input type="checkbox" checked={item.required} disabled={disabled}
        aria-label={`Input ${index + 1} required`}
        onChange={event => patch(index, { required: event.target.checked })} /></label>
      <button className="row-action danger" title="Remove input" aria-label="Remove input" disabled={disabled}
        onClick={() => onChange(inputs.filter((_, i) => i !== index))}>×</button>
    </div>)}
    <button className="ghost-button wf-add-step" disabled={disabled}
      onClick={() => onChange([...inputs, { id: '', label: '', kind: 'text', required: false }])}>+ Add field</button>
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
