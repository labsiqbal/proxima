import React from 'react'
import type { Schedule, Workflow } from '../../types'
import { createSchedule, deleteSchedule, listSchedules, updateSchedule } from '../../api/schedules'
import { confirmDialog } from '../ui/Dialog'
import { Dropdown } from '../ui/Dropdown'

export const CRON_PRESETS = [
  { value: 'hourly', label: 'Every hour', cron: '0 * * * *' },
  { value: 'daily9', label: 'Every day at 9am', cron: '0 9 * * *' },
  { value: 'q15', label: 'Every 15 minutes', cron: '*/15 * * * *' },
  { value: 'mon9', label: 'Every Monday at 9am', cron: '0 9 * * 1' },
  { value: 'custom', label: 'Custom…', cron: '' },
] as const

export const cronHint = (cron: string) => CRON_PRESETS.find(p => p.cron && p.cron === cron.trim())?.label || cron

const CRON_BOUNDS = [[0, 59], [0, 23], [1, 31], [1, 12], [0, 7]] as const
const validCronField = (field: string, lower: number, upper: number) => field.split(',').every(part => {
  if (!part) return false
  const pieces = part.split('/')
  if (pieces.length > 2) return false
  const [body, step] = pieces
  if (step !== undefined && (!/^\d+$/.test(step) || Number(step) <= 0)) return false
  if (body === '*') return true
  const range = body.split('-')
  if (range.length === 1 && /^\d+$/.test(body)) {
    const value = Number(body)
    return value >= lower && value <= upper
  }
  if (range.length === 2 && range.every(value => /^\d+$/.test(value))) {
    const [start, end] = range.map(Number)
    return start >= lower && start <= end && end <= upper
  }
  return false
})

/** Matches the backend's supported five-field cron grammar and field bounds. */
export const isValidCron = (cron: string) => {
  const fields = cron.trim().split(/\s+/)
  return fields.length === 5 && fields.every((field, index) => {
    const [lower, upper] = CRON_BOUNDS[index]
    return validCronField(field, lower, upper)
  })
}

export function ScheduleManager({ token, workflows, workflowId, compact = false, onClose }: {
  token: string
  workflows: Workflow[]
  workflowId?: number
  compact?: boolean
  onClose?: () => void
}) {
  const available = workflowId ? workflows.filter(w => w.id === workflowId) : workflows
  const [selectedId, setSelectedId] = React.useState(workflowId || available[0]?.id || 0)
  const selected = available.find(w => w.id === selectedId) || available[0] || null
  const [preset, setPreset] = React.useState('daily9')
  const [cron, setCron] = React.useState('0 9 * * *')
  const [overlap, setOverlap] = React.useState<'skip' | 'allow'>('skip')
  const [enabled, setEnabled] = React.useState(true)
  const [brief, setBrief] = React.useState('')
  const [values, setValues] = React.useState<Record<string, string>>({})
  const [schedules, setSchedules] = React.useState<Schedule[]>([])
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState('')
  const mounted = React.useRef(true)
  const loadSeq = React.useRef(0)
  const actionSeq = React.useRef(0)

  React.useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; loadSeq.current += 1; actionSeq.current += 1 }
  }, [])
  React.useEffect(() => { if (workflowId) setSelectedId(workflowId) }, [workflowId])

  const reload = React.useCallback(async () => {
    const seq = ++loadSeq.current
    try {
      const rows = await listSchedules(token, workflowId)
      if (mounted.current && seq === loadSeq.current) { setSchedules(rows); setError('') }
    } catch (e) { if (mounted.current && seq === loadSeq.current) setError(String(e)) }
  }, [token, workflowId])
  React.useEffect(() => { void reload() }, [reload])

  const act = async (work: () => Promise<unknown>) => {
    if (busy) return
    const seq = ++actionSeq.current
    setBusy(true); setError('')
    try { await work(); if (mounted.current && seq === actionSeq.current) await reload() }
    catch (e) { if (mounted.current && seq === actionSeq.current) setError(String(e)) }
    finally { if (mounted.current && seq === actionSeq.current) setBusy(false) }
  }

  const add = () => {
    if (!selected) { setError('Choose a workflow first.'); return }
    if (!isValidCron(cron)) { setError('Enter a valid five-field cron using numbers, *, steps, ranges, or comma-separated parts.'); return }
    const declared = selected.inputs || []
    const missing = declared.find(input => input.required && !(values[input.id] || '').trim())
    if (missing) { setError(`"${missing.label}" is required.`); return }
    const declaredInput = Object.fromEntries(declared.map(input => [input.id, (values[input.id] || '').trim()]).filter(([, value]) => value))
    const input = declared.length > 0 ? (Object.keys(declaredInput).length ? declaredInput : undefined) : (brief.trim() ? { brief: brief.trim() } : undefined)
    void act(() => createSchedule(token, {
      workflow_id: selected.id,
      cron: cron.trim(),
      input,
      overlap_policy: overlap,
      enabled,
    }))
  }
  const toggle = (schedule: Schedule) => void act(() => updateSchedule(token, schedule.id, { enabled: !schedule.enabled }))
  const remove = async (schedule: Schedule) => {
    const name = workflows.find(w => w.id === schedule.workflow_id)?.name || 'this workflow'
    if (!(await confirmDialog({ title: 'Delete schedule?', message: `Stop running “${name}” on ${cronHint(schedule.cron)}.`, confirmLabel: 'Delete', danger: true }))) return
    void act(() => deleteSchedule(token, schedule.id))
  }
  const pickPreset = (value: string) => {
    setPreset(value)
    const hit = CRON_PRESETS.find(p => p.value === value)
    if (hit?.cron) setCron(hit.cron)
  }

  return <section className={`schedule-manager ${compact ? 'compact' : ''}`} aria-labelledby="schedule-manager-title">
    <header className="schedule-manager-head">
      <div><p className="eyebrow">Automation</p><h1 id="schedule-manager-title">Scheduled</h1><p className="muted">Run real workflows on a five-field cron cadence.</p></div>
      {onClose && <button className="ghost-button" onClick={onClose} disabled={busy}>Close</button>}
    </header>
    {error && <div className="error-bar" role="alert">{error}</div>}
    <div className="schedule-create-card">
      {!workflowId && <label>Workflow<Dropdown value={selected?.id ? String(selected.id) : ''} onChange={v => setSelectedId(Number(v))} options={available.map(w => ({ value: String(w.id), label: w.name }))} /></label>}
      <label>Cadence<Dropdown value={preset} onChange={pickPreset} options={CRON_PRESETS.map(p => ({ value: p.value, label: p.label }))} /></label>
      <label>Cron<input value={cron} onChange={e => { setCron(e.target.value); setPreset('custom') }} placeholder="0 9 * * *" spellCheck={false} /></label>
      <label>Overlap<div className="seg sched-seg"><button type="button" className={overlap === 'skip' ? 'active' : ''} onClick={() => setOverlap('skip')}>Skip</button><button type="button" className={overlap === 'allow' ? 'active' : ''} onClick={() => setOverlap('allow')}>Allow</button></div></label>
      {(selected?.inputs || []).length > 0
        ? selected?.inputs.map(input => <label className="schedule-brief" key={input.id}>{input.label}{input.required && <span className="muted"> (required)</span>}<input type={input.kind === 'number' ? 'number' : input.kind === 'url' ? 'url' : 'text'} value={values[input.id] || ''} onChange={event => setValues(current => ({ ...current, [input.id]: event.target.value }))} placeholder={input.kind === 'file' ? 'Path or URL' : input.label} /></label>)
        : <label className="schedule-brief">Input brief <span className="muted">(optional)</span><textarea rows={2} value={brief} onChange={e => setBrief(e.target.value)} placeholder="Context supplied to every run" /></label>}
      <label className="wf-step-check"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} /> Enabled</label>
      <button className="primary-button" disabled={busy || !selected} onClick={add}>{busy ? 'Saving…' : 'Add schedule'}</button>
    </div>
    <div className="schedule-list" aria-live="polite">
      {schedules.length === 0 ? <p className="schedule-empty muted">No schedules yet.</p> : schedules.map(schedule => {
        const workflow = workflows.find(w => w.id === schedule.workflow_id)
        return <article className="schedule-row" key={schedule.id}>
          <div><strong>{workflow?.name || `Workflow ${schedule.workflow_id}`}</strong><small>{cronHint(schedule.cron)} · <code>{schedule.cron}</code> · {schedule.overlap_policy === 'allow' ? 'overlap allowed' : 'skip overlap'}</small></div>
          <label className="schedule-toggle"><input type="checkbox" checked={schedule.enabled} disabled={busy} onChange={() => toggle(schedule)} /> {schedule.enabled ? 'On' : 'Off'}</label>
          <button className="ghost-button danger" disabled={busy} onClick={() => void remove(schedule)}>Delete</button>
        </article>
      })}
    </div>
  </section>
}
