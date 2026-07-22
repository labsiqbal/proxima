import React from 'react'
import type { WorkflowInput } from '../../types'

const INPUT_KINDS: WorkflowInput['kind'][] = ['text', 'url', 'number', 'file']
const slugifyId = (value: string) =>
  value.toLowerCase().trim().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')

// Saving a plan as a Recipe is the moment its reusable contract is defined, so it is
// also where {{inputs}} are declared: a Recipe is what a fresh run and a schedule are
// both built from, and each needs to know what to ask for. (Extracted from GraphScreen
// in slice 3 — the Tasks screen promotes plans through the same modal.)
export function SaveTemplateModal({ title, initial, busy, onCancel, onSave }: {
  title: string
  /** What the authoring chat proposed — a starting point, still fully editable here. */
  initial?: { description?: string; category?: string; inputs?: WorkflowInput[] }
  busy: boolean
  onCancel: () => void
  onSave: (meta: { name: string; description: string; category: string; inputs: WorkflowInput[] }) => void
}) {
  const [name, setName] = React.useState(title)
  const [description, setDescription] = React.useState(initial?.description ?? '')
  const [category, setCategory] = React.useState(initial?.category ?? '')
  const [inputs, setInputs] = React.useState<WorkflowInput[]>(initial?.inputs ?? [])

  const patch = (index: number, next: Partial<WorkflowInput>) =>
    setInputs(current => current.map((item, i) => i === index ? { ...item, ...next } : item))
  const close = () => { if (!busy) onCancel() }

  return <div className="modal-scrim" onClick={close}><div className="modal-card graph-template-card" onClick={event => event.stopPropagation()} role="dialog" aria-modal="true">
    <h3>Save as reusable workflow</h3>
    <label>Name<input autoFocus value={name} disabled={busy} onChange={event => setName(event.target.value)} /></label>
    <label>Category <span className="muted">(optional)</span><input value={category} disabled={busy} placeholder="e.g. content" onChange={event => setCategory(event.target.value)} /></label>
    <label>Description <span className="muted">(optional)</span><textarea rows={2} value={description} disabled={busy} placeholder="What this workflow does" onChange={event => setDescription(event.target.value)} /></label>

    <p className="eyebrow">Inputs <span className="muted">(optional)</span></p>
    <p className="muted graph-field-note">
      What each run should be asked for. Refer to one from any node with <code>{'{{id}}'}</code>.
    </p>
    <div className="wf-inputs">
      {inputs.length > 0 && <div className="wf-input-row wf-input-head">
        <span>Label</span><span>ID</span><span>Kind</span><span>Required</span><span />
      </div>}
      {inputs.map((item, index) => <div className="wf-input-row" key={index}>
        <input className="wf-input-cell" value={item.label} disabled={busy} placeholder="e.g. Topic"
          onChange={event => patch(index, { label: event.target.value, id: item.id.trim() ? item.id : slugifyId(event.target.value) })} />
        <input className="wf-input-cell" value={item.id} disabled={busy} placeholder="topic"
          onChange={event => patch(index, { id: slugifyId(event.target.value) })} />
        <select className="wf-input-cell" value={item.kind} disabled={busy}
          onChange={event => patch(index, { kind: event.target.value as WorkflowInput['kind'] })}>
          {INPUT_KINDS.map(kind => <option key={kind} value={kind}>{kind}</option>)}
        </select>
        <label className="wf-input-req"><input type="checkbox" checked={item.required} disabled={busy}
          onChange={event => patch(index, { required: event.target.checked })} /> required</label>
        <button className="row-action danger" title="Remove input" aria-label="Remove input" disabled={busy}
          onClick={() => setInputs(current => current.filter((_, i) => i !== index))}>×</button>
      </div>)}
      <button className="ghost-button wf-add-step" disabled={busy}
        onClick={() => setInputs(current => [...current, { id: '', label: '', kind: 'text', required: false }])}>+ Add input</button>
    </div>

    <div className="modal-actions">
      <button className="ghost-button" onClick={close} disabled={busy}>Cancel</button>
      <button className="primary-button" disabled={busy || !name.trim()} onClick={() => onSave({
        name: name.trim(),
        description: description.trim(),
        category: category.trim() || 'other',
        // A half-typed row is noise, not a declaration.
        inputs: inputs.filter(item => item.id.trim() && item.label.trim()),
      })}>{busy ? 'Saving…' : 'Save template'}</button>
    </div>
  </div></div>
}
