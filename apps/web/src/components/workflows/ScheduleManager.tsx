import React from 'react'
import type { GraphScheduleConfig, Job, Schedule, WorkflowGraph, WorkflowInput } from '../../types'
import { createSchedule, deleteSchedule, listSchedules, runScheduleNow, updateSchedule } from '../../api/schedules'
import { confirmDialog } from '../ui/Dialog'
import { Dropdown } from '../ui/Dropdown'

export const CRON_PRESETS = [
  { value: 'hourly', label: 'Every hour', cron: '0 * * * *' },
  { value: 'daily9', label: 'Every day at 9am', cron: '0 9 * * *' },
  { value: 'q15', label: 'Every 15 minutes', cron: '*/15 * * * *' },
  { value: 'mon9', label: 'Every Monday at 9am', cron: '0 9 * * 1' },
  { value: 'custom', label: 'Custom…', cron: '' },
] as const

export const browserTimezone = () => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

const timezoneOptions = (selected: string) => {
  const intl = Intl as typeof Intl & { supportedValuesOf?: (key: 'timeZone') => string[] }
  const values = intl.supportedValuesOf?.('timeZone') ?? ['UTC']
  return Array.from(new Set(['UTC', selected, ...values]))
    .filter(Boolean)
    .map(value => ({ value, label: value }))
}

export const DEFAULT_GRAPH_SCHEDULE: GraphScheduleConfig = {
  cron: '0 9 * * *',
  timezone: browserTimezone(),
  overlap_policy: 'skip',
  enabled: false,
}

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

export function ScheduleSettingsEditor({ value, disabled = false, onChange }: {
  value: GraphScheduleConfig
  disabled?: boolean
  onChange: (value: GraphScheduleConfig) => void
}) {
  const matchedPreset = CRON_PRESETS.find(p => p.cron && p.cron === value.cron.trim())
  const [preset, setPreset] = React.useState<string>(matchedPreset?.value ?? 'custom')

  React.useEffect(() => {
    const match = CRON_PRESETS.find(p => p.cron && p.cron === value.cron.trim())
    setPreset(match?.value ?? 'custom')
  }, [value.cron])

  const pickPreset = (nextPreset: string) => {
    setPreset(nextPreset)
    const hit = CRON_PRESETS.find(p => p.value === nextPreset)
    if (hit?.cron) onChange({ ...value, cron: hit.cron })
  }

  return <div className="schedule-trigger-settings">
    <label>Cadence<Dropdown
      value={preset}
      onChange={pickPreset}
      options={CRON_PRESETS.map(p => ({ value: p.value, label: p.label }))}
      disabled={disabled}
    /></label>
    <label>Cron<input
      value={value.cron}
      disabled={disabled}
      onChange={event => onChange({ ...value, cron: event.target.value })}
      placeholder="0 9 * * *"
      spellCheck={false}
    /></label>
    <label>Timezone<Dropdown
      value={value.timezone}
      onChange={timezone => onChange({ ...value, timezone })}
      options={timezoneOptions(value.timezone)}
      disabled={disabled}
    /></label>
    <label>Overlap<div className="seg sched-seg">
      <button type="button" disabled={disabled} className={value.overlap_policy === 'skip' ? 'active' : ''} onClick={() => onChange({ ...value, overlap_policy: 'skip' })}>Skip</button>
      <button type="button" disabled={disabled} className={value.overlap_policy === 'allow' ? 'active' : ''} onClick={() => onChange({ ...value, overlap_policy: 'allow' })}>Allow</button>
    </div></label>
    <label className="wf-step-check"><input
      type="checkbox"
      checked={value.enabled}
      disabled={disabled}
      onChange={event => onChange({ ...value, enabled: event.target.checked })}
    /> Enabled</label>
  </div>
}

export type SchedulableWorkflow = {
  id: number
  name: string
  status?: string
  inputs?: WorkflowInput[]
  graph?: WorkflowGraph
  /** Owning project. Schedules inherit this and cannot target another project. */
  project_slug?: string | null
}

const workflowInputs = (workflow: SchedulableWorkflow | null): WorkflowInput[] => {
  const trigger = workflow?.graph?.nodes.find(node => node.type === 'trigger')
  return trigger?.inputs?.length ? trigger.inputs : workflow?.inputs ?? []
}

const nonblankBindings = (values: Record<string, string>) => Object.fromEntries(
  Object.entries(values)
    .map(([key, value]) => [key, value.trim()])
    .filter(([, value]) => value),
)

export function ScheduleManager({ token, workflows, workflowId, compact = false, onClose, onChanged, onOpenJob, defaultTimezone = browserTimezone() }: {
  token: string
  workflows: SchedulableWorkflow[]
  workflowId?: number
  compact?: boolean
  onClose?: () => void
  defaultTimezone?: string
  /** Keep the owning workflow list in sync after create, update, or delete. */
  onChanged?: () => void
  /** Resolve only after the owning project has selected the exact returned job. */
  onOpenJob?: (job: Job) => Promise<void> | void
}) {
  const available = workflowId ? workflows.filter(w => w.id === workflowId) : workflows
  const [selectedId, setSelectedId] = React.useState(workflowId || available[0]?.id || 0)
  const selected = available.find(w => w.id === selectedId) || available[0] || null
  const [settings, setSettings] = React.useState<GraphScheduleConfig>({
    ...DEFAULT_GRAPH_SCHEDULE,
    timezone: defaultTimezone,
  })
  const [bindings, setBindings] = React.useState<Record<string, string>>({})
  const [editingId, setEditingId] = React.useState<number | null>(null)
  const [schedules, setSchedules] = React.useState<Schedule[]>([])
  const [busy, setBusy] = React.useState(false)
  const [openingId, setOpeningId] = React.useState<number | null>(null)
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

  const act = async (work: () => Promise<unknown>, after?: () => void) => {
    if (busy) return
    const seq = ++actionSeq.current
    setBusy(true); setError('')
    try {
      await work()
      if (mounted.current && seq === actionSeq.current) {
        await reload()
        onChanged?.()
        after?.()
      }
    }
    catch (e) { if (mounted.current && seq === actionSeq.current) setError(String(e)) }
    finally { if (mounted.current && seq === actionSeq.current) setBusy(false) }
  }

  const declaredInputs = workflowInputs(selected)
  const missingBindings = declaredInputs.filter(
    input => input.required && !(bindings[input.id] || '').trim(),
  )
  const resetEditor = () => {
    setEditingId(null)
    setBindings({})
    setSettings({ ...DEFAULT_GRAPH_SCHEDULE, timezone: defaultTimezone })
  }
  const save = () => {
    if (!selected) { setError('Choose a workflow first.'); return }
    if (!isValidCron(settings.cron)) { setError('Enter a valid five-field cron using numbers, *, steps, ranges, or comma-separated parts.'); return }
    if (settings.enabled && missingBindings.length) {
      setError(`Cannot enable this schedule. Add a source node or save a durable binding for: ${missingBindings.map(input => input.label).join(', ')}.`)
      return
    }
    const body = {
      cron: settings.cron.trim(),
      timezone: settings.timezone,
      bindings: nonblankBindings(bindings),
      overlap_policy: settings.overlap_policy,
      enabled: settings.enabled,
    } as const
    void act(() => editingId == null ? createSchedule(token, {
      workflow_id: selected.id,
      ...body,
    }) : updateSchedule(token, editingId, body), resetEditor)
  }
  const edit = (schedule: Schedule) => {
    setEditingId(schedule.id)
    setSettings({
      cron: schedule.cron,
      timezone: schedule.timezone || defaultTimezone,
      overlap_policy: schedule.overlap_policy,
      enabled: schedule.enabled,
    })
    setBindings(Object.fromEntries(
      Object.entries(schedule.bindings || schedule.input || {})
        .map(([key, value]) => [key, String(value ?? '')]),
    ))
    setError('')
  }
  const toggle = (schedule: Schedule) => {
    if (!schedule.enabled && !schedule.ready) {
      edit(schedule)
      setError('This schedule is missing a required source. Add a source node or durable binding before turning it on.')
      return
    }
    void act(() => updateSchedule(token, schedule.id, { enabled: !schedule.enabled }))
  }
  const runNow = async (schedule: Schedule) => {
    if (busy || schedule.ready === false) return
    const seq = ++actionSeq.current
    setBusy(true)
    setOpeningId(schedule.id)
    setError('')
    try {
      const job = await runScheduleNow(token, schedule.id)
      if (!mounted.current || seq !== actionSeq.current) return
      await onOpenJob?.(job)
    } catch (cause) {
      if (mounted.current && seq === actionSeq.current) setError(String(cause))
    } finally {
      if (mounted.current && seq === actionSeq.current) {
        setBusy(false)
        setOpeningId(null)
      }
    }
  }
  const remove = async (schedule: Schedule) => {
    const name = workflows.find(w => w.id === schedule.workflow_id)?.name || 'this workflow'
    if (!(await confirmDialog({ title: 'Delete schedule?', message: `Stop running “${name}” on ${cronHint(schedule.cron)}.`, confirmLabel: 'Delete', danger: true }))) return
    void act(() => deleteSchedule(token, schedule.id))
  }
  return <section className={`schedule-manager ${compact ? 'compact' : ''}`} aria-labelledby="schedule-manager-title">
    <header className="schedule-manager-head">
      <div><p className="eyebrow">Automation</p><h1 id="schedule-manager-title">{compact ? `Schedules for ${selected?.name || 'workflow'}` : 'Schedules'}</h1><p className="muted">Configure unattended runs with a cadence, timezone, and durable input sources.</p></div>
      {onClose && <button className="ghost-button" onClick={onClose} disabled={busy}>Close</button>}
    </header>
    {error && <div className="error-bar" role="alert">{error}</div>}
    {/* Only saved templates can be scheduled — a finished run is not schedulable until
        it is saved as one. Saying so here beats an inexplicably thin picker. */}
    {!workflowId && available.length === 0 && <div className="schedule-empty-hint">
      <strong>No Workflows to schedule yet.</strong>
      <p className="muted">Schedules run <em>saved Workflows</em>. Open a plan in the Editor — a finished run works too — press <em>Save as Workflow</em>, and it will appear here.</p>
    </div>}
    <div className="schedule-create-card">
      {!workflowId && <label>Workflow<Dropdown value={selected?.id ? String(selected.id) : ''} onChange={v => setSelectedId(Number(v))} options={available.map(w => ({ value: String(w.id), label: w.name }))} /></label>}
      <ScheduleSettingsEditor value={settings} disabled={busy} onChange={setSettings} />
      {declaredInputs.length > 0 && <div className="schedule-bindings">
        <strong>Automation bindings</strong>
        <p className="muted">Manual runs ask for these each time. Schedules must save values here or get the data from a source node.</p>
        {declaredInputs.map(input => <label key={input.id}>
          {input.label} automation binding{input.required && <span className="muted"> (required to turn on)</span>}
          <input
            aria-label={`${input.label} automation binding`}
            type={input.kind === 'number' ? 'number' : input.kind === 'url' ? 'url' : 'text'}
            value={bindings[input.id] || ''}
            onChange={event => setBindings(current => ({ ...current, [input.id]: event.target.value }))}
            placeholder={input.kind === 'file' ? 'Stable path or URL' : input.label}
            disabled={busy}
          />
        </label>)}
      </div>}
      <div className="schedule-form-actions">
        {editingId != null && <button className="ghost-button" disabled={busy} onClick={resetEditor}>Cancel edit</button>}
        <button className="primary-button" disabled={busy || !selected} onClick={save}>{busy && openingId == null ? 'Saving…' : editingId == null ? 'Add schedule' : 'Save schedule'}</button>
      </div>
    </div>
    <div className="schedule-list" aria-live="polite">
      {schedules.length === 0 ? <p className="schedule-empty muted">No schedules yet.</p> : schedules.map(schedule => {
        const workflow = workflows.find(w => w.id === schedule.workflow_id)
        const inputLabels = new Map(workflowInputs(workflow || null).map(input => [input.id, input.label]))
        const ready = schedule.ready !== false
        const unresolved = (schedule.unresolved_labels?.length
          ? schedule.unresolved_labels
          : (schedule.unresolved_inputs || []).map(input => inputLabels.get(input) || input))
        const cadence = cronHint(schedule.cron)
        return <article className="schedule-row" key={schedule.id}>
          <div>
            <strong>{workflow?.name || `Workflow ${schedule.workflow_id}`}</strong>
            <small>{cadence} · {schedule.timezone || 'UTC'} · {schedule.overlap_policy === 'allow' ? 'overlap allowed' : 'skip overlap'}</small>
            <small className={ready ? 'schedule-ready' : 'schedule-needs-source'}>
              {ready ? 'Inputs ready' : `Needs source: ${unresolved.join(', ')}`}
            </small>
          </div>
          <label className="schedule-toggle"><input aria-label={`Schedule ${schedule.enabled ? 'on' : 'off'}`} type="checkbox" checked={schedule.enabled} disabled={busy} onChange={() => toggle(schedule)} /> {schedule.enabled ? 'On' : 'Off'}</label>
          <button className="ghost-button" disabled={busy} onClick={() => edit(schedule)}>Configure</button>
          <button className="ghost-button" disabled={busy || !ready} onClick={() => void runNow(schedule)} title="Run this schedule now with its saved automation sources">{openingId === schedule.id ? 'Opening run...' : 'Run now'}</button>
          <button className="ghost-button danger" disabled={busy} onClick={() => void remove(schedule)}>Delete</button>
        </article>
      })}
    </div>
  </section>
}
