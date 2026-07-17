import React from 'react'
import type { AppFeatures, Profile, Project, Schedule, Workflow, WorkflowDraft, WorkflowInput } from '../types'
import { listWorkflows, createWorkflow, updateWorkflow, archiveWorkflow, deleteWorkflow, type StepInput } from '../api/workflows'
import { WorkflowChat, type WorkflowChatHandle } from '../components/workflows/WorkflowChat'
import type { RecipePatch } from '../components/workflows/recipePrompt'
import { createJob, startJob } from '../api/jobs'
import { listSchedules, createSchedule, updateSchedule, deleteSchedule } from '../api/schedules'
import { Dropdown } from '../components/ui/Dropdown'
import { confirmDialog } from '../components/ui/Dialog'
import { BackButton } from '../components/ui/BackButton'
import { ScheduleManager, isValidCron } from '../components/workflows/ScheduleManager'
import { RunModal } from '../components/workflows/RunModal'

const clean = (n: string) => n.replace(/\s*\(private\)\s*$/i, '')
type StepDraft = StepInput
const blankStep = (): StepDraft => ({ name: '', instruction: '', expected_output: '', type: 'task', rules: '', skill_ids: [], review_required: false })
const blankInput = (): WorkflowInput => ({ id: '', label: '', kind: 'text', required: false })
const slugify = (s: string) => s.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
const INPUT_KINDS: WorkflowInput['kind'][] = ['text', 'url', 'number', 'file']

// The editor works on a plain draft (name/description/category + inputs + steps) so
// both a brand-new recipe, an existing workflow, and a promoted-chat draft share one form.
type EditorState = { id: number | null; name: string; description: string; category: string; inputs: WorkflowInput[]; steps: StepDraft[] }

function WorkflowEditor({ token, init, projectSlug, features, profiles, activeProfile, onBack, onSaved, onDeleted }: {
  token: string; init: EditorState; projectSlug: string | null
  features: AppFeatures; profiles: Profile[]; activeProfile: Profile | null
  onBack: () => void; onSaved: (w: Workflow) => void; onDeleted: () => void
}) {
  const [form, setForm] = React.useState<EditorState>(init)
  const [saving, setSaving] = React.useState(false)
  const [deleting, setDeleting] = React.useState(false)
  const [error, setError] = React.useState('')
  const [highlightStep, setHighlightStep] = React.useState<number | null>(null)
  const mountedRef = React.useRef(true)
  const actionSeq = React.useRef(0)
  const chatRef = React.useRef<WorkflowChatHandle>(null)
  const highlightTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null)

  // The outline is a map for a form that gets long: jump the form to a step and flash it,
  // so a click on "step 3" lands you on step 3 rather than scrolling to hunt for it.
  const jumpToStep = (i: number) => {
    document.getElementById(`wf-step-${i}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    setHighlightStep(i)
    if (highlightTimer.current) clearTimeout(highlightTimer.current)
    highlightTimer.current = setTimeout(() => { if (mountedRef.current) setHighlightStep(null) }, 1400)
  }

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      actionSeq.current += 1
    }
  }, [])

  const setStep = (i: number, patch: Partial<StepDraft>) => setForm(f => ({ ...f, steps: f.steps.map((s, j) => j === i ? { ...s, ...patch } : s) }))
  // Fold a recipe the chat produced into the form. Full-fidelity (rules/skills/gates
  // included) and per-field: fields the agent left out keep their current value rather
  // than blanking, so "rename step 2" can't wipe the description.
  const applyRecipe = (patch: RecipePatch) => setForm(f => ({
    ...f,
    name: patch.name ?? f.name,
    description: patch.description ?? f.description,
    category: patch.category ?? f.category,
    inputs: patch.inputs ?? f.inputs,
    steps: patch.steps
      ? patch.steps.map(s => ({
          name: s.name, instruction: s.instruction, expected_output: s.expected_output ?? '',
          type: s.type || 'task', rules: s.rules ?? '', skill_ids: s.skill_ids ?? [], review_required: !!s.review_required,
        }))
      : f.steps,
  }))
  const addStep = () => setForm(f => ({ ...f, steps: [...f.steps, blankStep()] }))
  const removeStep = (i: number) => setForm(f => ({ ...f, steps: f.steps.filter((_, j) => j !== i) }))
  const setInput = (i: number, patch: Partial<WorkflowInput>) => setForm(f => ({ ...f, inputs: f.inputs.map((x, j) => j === i ? { ...x, ...patch } : x) }))
  const addInput = () => setForm(f => ({ ...f, inputs: [...f.inputs, blankInput()] }))
  const removeInput = (i: number) => setForm(f => ({ ...f, inputs: f.inputs.filter((_, j) => j !== i) }))
  const moveStep = (i: number, dir: -1 | 1) => setForm(f => {
    const j = i + dir
    if (j < 0 || j >= f.steps.length) return f
    const steps = f.steps.slice();[steps[i], steps[j]] = [steps[j], steps[i]]
    return { ...f, steps }
  })

  /** Save without leaving the editor, so the test bench can persist a draft in place.
   *  Returns the saved workflow, or null when validation or the request failed (the
   *  reason is already in the error bar). */
  async function persist(fallbackName?: string): Promise<Workflow | null> {
    if (saving || deleting) return null
    // fallbackName is the chat's "seed a blank draft so it can open" path; it also
    // relaxes the ≥1-step rule with a placeholder, since the chat exists to write the
    // real steps. A manual Save (no fallback) stays strict.
    const seeding = !!fallbackName
    const name = form.name.trim() || (fallbackName || '').trim()
    if (!name) { setError('Name is required.'); return null }
    let steps: StepInput[] = form.steps.filter(s => s.name.trim() || s.instruction.trim())
      .map(s => ({ name: s.name.trim(), instruction: s.instruction.trim(), expected_output: s.expected_output?.trim() || undefined, type: s.type || undefined, rules: s.rules?.trim() || null, skill_ids: (s.skill_ids && s.skill_ids.length) ? s.skill_ids : null, review_required: !!s.review_required }))
    if (steps.length === 0) {
      if (!seeding) { setError('Add at least one step.'); return null }
      steps = [{ name: 'Step 1', instruction: 'Describe this step, or ask the chat to write it.' }]
    }
    // Drop blank input rows; derive id from label if the user left it empty.
    const inputs: WorkflowInput[] = form.inputs.filter(x => x.label.trim() || x.id.trim())
      .map(x => ({ id: (x.id.trim() || slugify(x.label)), label: x.label.trim() || x.id.trim(), kind: x.kind, required: !!x.required }))
    const seq = ++actionSeq.current
    setSaving(true); setError('')
    try {
      const body = { name, description: form.description.trim() || undefined, category: form.category.trim() || undefined, inputs, steps }
      const w = form.id != null ? await updateWorkflow(token, form.id, body) : await createWorkflow(token, { ...body, project_slug: projectSlug })
      if (!mountedRef.current || seq !== actionSeq.current) return null
      // Adopt the new id, or a save after "Start test" would fork a second copy of the
      // same recipe instead of updating this one.
      setForm(f => ({ ...f, id: w.id }))
      return w
    } catch (e) { if (mountedRef.current && seq === actionSeq.current) setError(String(e)); return null }
    finally { if (mountedRef.current && seq === actionSeq.current) setSaving(false) }
  }

  async function save() {
    const w = await persist()
    if (w && mountedRef.current) onSaved(w)
  }

  async function del() {
    if (form.id == null || saving || deleting) return
    if (!(await confirmDialog({ title: `Delete "${form.name || 'this workflow'}"?`, message: 'The recipe and its schedules are permanently removed. Past runs keep their own copy of the steps, so they stay intact.', confirmLabel: 'Delete permanently', danger: true }))) return
    if (!mountedRef.current) return
    const seq = ++actionSeq.current
    setDeleting(true); setError('')
    try {
      await deleteWorkflow(token, form.id)
      if (!mountedRef.current || seq !== actionSeq.current) return
      onDeleted()
    }
    catch (e) { if (mountedRef.current && seq === actionSeq.current) setError(String(e)) }
    finally { if (mountedRef.current && seq === actionSeq.current) setDeleting(false) }
  }

  return <div className="wf-editor">
    <div className="wf-editor-head">
      <BackButton label="Workflows" onClick={onBack} />
      <strong className="wf-editor-title">{form.id != null ? 'Edit workflow' : init.name ? 'Review draft' : 'New workflow'}</strong>
      {form.id != null && <button className="ghost-button danger" onClick={() => void del()} disabled={saving || deleting}>{deleting ? 'Deleting…' : 'Delete'}</button>}
      <button className="primary-button" onClick={() => void save()} disabled={saving || deleting}>{saving ? 'Saving…' : 'Save workflow'}</button>
    </div>
    {error && <div className="error-bar">{error}</div>}
    <div className="wf-editor-split">
    <WorkflowChat
      ref={chatRef}
      token={token}
      features={features}
      profiles={profiles}
      activeProfile={activeProfile}
      projectSlug={projectSlug}
      workflowId={form.id}
      recipe={{ name: form.name, description: form.description, category: form.category, inputs: form.inputs, steps: form.steps }}
      // The chat exists partly to fill in a blank recipe, but saving needs a name. Seed a
      // placeholder so the first turn can open — the agent's first reply renames it.
      onEnsureSaved={async () => {
        if (!form.name.trim()) setForm(f => ({ ...f, name: 'Untitled workflow' }))
        return (await persist('Untitled workflow'))?.id ?? null
      }}
      onApplyRecipe={applyRecipe}
    />
    <div className="wf-editor-body">
      <div className="wf-meta">
        <label>Name<input autoFocus value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="e.g. Ship a feature" /></label>
        <label>Category <span className="muted">(optional)</span><input value={form.category} onChange={e => setForm({ ...form, category: e.target.value })} placeholder="e.g. engineering" /></label>
        <label className="wf-meta-wide">Description <span className="muted">(optional)</span><textarea rows={2} value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="What this recipe does" /></label>
      </div>
      <div className="wf-steps-head"><p className="eyebrow">Inputs <span className="muted">(optional)</span></p><span className="kanban-count">{form.inputs.length}</span></div>
      <div className="wf-inputs">
        {form.inputs.length > 0 && <div className="wf-input-row wf-input-head">
          <span>Label</span><span>ID</span><span>Kind</span><span>Required</span><span />
        </div>}
        {form.inputs.map((x, i) => <div className="wf-input-row" key={i}>
          <input className="wf-input-cell" value={x.label} onChange={e => setInput(i, { label: e.target.value, id: x.id.trim() ? x.id : slugify(e.target.value) })} placeholder="e.g. Topic" />
          <input className="wf-input-cell" value={x.id} onChange={e => setInput(i, { id: slugify(e.target.value) })} placeholder="topic" />
          <select className="wf-input-cell" value={x.kind} onChange={e => setInput(i, { kind: e.target.value as WorkflowInput['kind'] })}>
            {INPUT_KINDS.map(k => <option key={k} value={k}>{k}</option>)}
          </select>
          <label className="wf-input-req"><input type="checkbox" checked={x.required} onChange={e => setInput(i, { required: e.target.checked })} /> required</label>
          <button className="row-action danger" title="Remove input" aria-label="Remove input" onClick={() => removeInput(i)}>×</button>
        </div>)}
        <button className="ghost-button wf-add-step" onClick={addInput}>+ Add input</button>
      </div>
      <div className="wf-steps-head"><p className="eyebrow">Steps</p><span className="kanban-count">{form.steps.length}</span></div>
      <div className="wf-steps">
        {form.steps.map((s, i) => <div className={`wf-step${highlightStep === i ? ' wf-step-flash' : ''}`} id={`wf-step-${i}`} key={i}>
          <div className="wf-step-head">
            <span className="wf-step-num">{i + 1}</span>
            <input className="wf-step-name" value={s.name} onChange={e => setStep(i, { name: e.target.value })} placeholder="Step name" />
            <span className="wf-step-actions">
              <button className="row-action" title="Move up" aria-label="Move up" disabled={i === 0} onClick={() => moveStep(i, -1)}>↑</button>
              <button className="row-action" title="Move down" aria-label="Move down" disabled={i === form.steps.length - 1} onClick={() => moveStep(i, 1)}>↓</button>
              <button className="row-action danger" title="Remove step" aria-label="Remove step" onClick={() => removeStep(i)}>×</button>
            </span>
          </div>
          <label className="wf-step-field">Instruction<textarea rows={2} value={s.instruction} onChange={e => setStep(i, { instruction: e.target.value })} placeholder="What the agent should do in this step" /></label>
          <label className="wf-step-field">Expected output <span className="muted">(optional)</span><textarea rows={2} value={s.expected_output} onChange={e => setStep(i, { expected_output: e.target.value })} placeholder="What this step should produce" /></label>
          <label className="wf-step-field">Rules <span className="muted">(optional)</span><textarea rows={2} value={s.rules ?? ''} onChange={e => setStep(i, { rules: e.target.value })} placeholder="Hard constraints for this step" /></label>
          <label className="wf-step-field">Skills <span className="muted">(optional, comma-separated)</span><input className="wf-step-name" value={(s.skill_ids ?? []).join(', ')} onChange={e => setStep(i, { skill_ids: e.target.value.split(',').map(t => t.trim()).filter(Boolean) })} placeholder="e.g. web-search, code-review" /></label>
          <label className="wf-step-check"><input type="checkbox" checked={!!s.review_required} onChange={e => setStep(i, { review_required: e.target.checked })} /> Pause for my review after this step</label>
        </div>)}
        <button className="ghost-button wf-add-step" onClick={addStep}>+ Add step</button>
      </div>
    </div>
    <aside className="wf-outline">
      <div className="wf-outline-head"><p className="eyebrow">Steps</p><span className="kanban-count">{form.steps.length}</span></div>
      <div className="wf-outline-list">
        {form.steps.map((s, i) => <div className={`wf-outline-row${highlightStep === i ? ' active' : ''}`} key={i}>
          <button className="wf-outline-jump" onClick={() => jumpToStep(i)} title="Jump to this step">
            <span className="wf-outline-num">{i + 1}</span>
            <span className="wf-outline-name">{s.name.trim() || <span className="muted">Untitled step</span>}</span>
            {s.review_required && <span className="wf-outline-gate" title="Pauses for review">gate</span>}
          </button>
          <button className="ghost-button wf-outline-test" onClick={() => chatRef.current?.runThrough(i, s.name.trim() || undefined)} title={`Run steps 1–${i + 1} and show step ${i + 1}'s result in the chat`}>Run test</button>
        </div>)}
        {form.steps.length === 0 && <p className="muted">No steps yet.</p>}
      </div>
    </aside>
    </div>
  </div>
}

// Friendly cadence presets → raw 5-field cron. "Custom" keeps whatever's typed.
const CRON_PRESETS: { value: string; label: string; cron: string }[] = [
  { value: 'hourly', label: 'Every hour', cron: '0 * * * *' },
  { value: 'daily9', label: 'Every day at 9am', cron: '0 9 * * *' },
  { value: 'q15', label: 'Every 15 minutes', cron: '*/15 * * * *' },
  { value: 'mon9', label: 'Every Monday 9am', cron: '0 9 * * 1' },
  { value: 'custom', label: 'Custom…', cron: '' },
]
// A tiny human hint for the common presets; falls back to raw cron otherwise.
function cronHint(cron: string): string {
  const hit = CRON_PRESETS.find(p => p.cron && p.cron === cron.trim())
  return hit ? hit.label : cron
}
function ScheduleModal({ token, workflow, onClose }: { token: string; workflow: Workflow; onClose: () => void }) {
  const declared = workflow.inputs || []
  const hasInputs = declared.length > 0
  const [preset, setPreset] = React.useState('daily9')
  const [cron, setCron] = React.useState('0 9 * * *')
  const [overlap, setOverlap] = React.useState<'skip' | 'allow'>('skip')
  const [enabled, setEnabled] = React.useState(true)
  const [brief, setBrief] = React.useState('')
  const [values, setValues] = React.useState<Record<string, string>>({})
  const [schedules, setSchedules] = React.useState<Schedule[]>([])
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState('')
  const mountedRef = React.useRef(true)
  const loadSeq = React.useRef(0)
  const actionSeq = React.useRef(0)

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      loadSeq.current += 1
      actionSeq.current += 1
    }
  }, [])

  const reload = React.useCallback(async () => {
    const seq = ++loadSeq.current
    try {
      const rows = await listSchedules(token, workflow.id)
      if (mountedRef.current && seq === loadSeq.current) setSchedules(rows)
    } catch (e) {
      if (mountedRef.current && seq === loadSeq.current) setError(String(e))
    }
  }, [token, workflow.id])
  React.useEffect(() => { void reload() }, [reload])

  function pickPreset(v: string) {
    setPreset(v)
    const hit = CRON_PRESETS.find(p => p.value === v)
    if (hit && hit.cron) setCron(hit.cron)
  }

  function buildInput(): any {
    if (hasInputs) {
      const input: Record<string, string> = {}
      for (const x of declared) { const val = (values[x.id] || '').trim(); if (val) input[x.id] = val }
      return Object.keys(input).length ? input : undefined
    }
    const b = brief.trim()
    return b ? { brief: b } : undefined
  }

  async function create() {
    if (busy) return
    if (!isValidCron(cron)) { setError('Cron must have exactly 5 space-separated fields (min hour day-of-month month day-of-week).'); return }
    if (hasInputs) {
      const missing = declared.find(x => x.required && !(values[x.id] || '').trim())
      if (missing) { setError(`"${missing.label}" is required.`); return }
    }
    const seq = ++actionSeq.current
    setBusy(true); setError('')
    try {
      await createSchedule(token, { workflow_id: workflow.id, cron: cron.trim(), input: buildInput(), overlap_policy: overlap, enabled })
      if (mountedRef.current && seq === actionSeq.current) await reload()
    } catch (e) {
      if (mountedRef.current && seq === actionSeq.current) setError(String(e))
    } finally {
      if (mountedRef.current && seq === actionSeq.current) setBusy(false)
    }
  }

  async function toggle(s: Schedule) {
    if (busy) return
    const seq = ++actionSeq.current
    setBusy(true); setError('')
    try {
      await updateSchedule(token, s.id, { enabled: !s.enabled })
      if (mountedRef.current && seq === actionSeq.current) await reload()
    } catch (e) {
      if (mountedRef.current && seq === actionSeq.current) setError(String(e))
    } finally {
      if (mountedRef.current && seq === actionSeq.current) setBusy(false)
    }
  }
  async function remove(s: Schedule) {
    if (busy) return
    if (!(await confirmDialog({ title: 'Delete schedule?', message: `Stop running on "${cronHint(s.cron)}".`, confirmLabel: 'Delete', danger: true }))) return
    if (!mountedRef.current || busy) return
    const seq = ++actionSeq.current
    setBusy(true); setError('')
    try {
      await deleteSchedule(token, s.id)
      if (mountedRef.current && seq === actionSeq.current) await reload()
    } catch (e) {
      if (mountedRef.current && seq === actionSeq.current) setError(String(e))
    } finally {
      if (mountedRef.current && seq === actionSeq.current) setBusy(false)
    }
  }

  const close = () => { if (!busy) onClose() }

  return <div className="modal-scrim" onClick={close}><div className="modal-card sched-card" onClick={e => e.stopPropagation()}>
    <h3>Schedule “{workflow.name}”</h3>
    {error && <div className="error-bar">{error}</div>}
    <label>Cadence
      <Dropdown value={preset} onChange={pickPreset} options={CRON_PRESETS.map(p => ({ value: p.value, label: p.label }))} />
    </label>
    <label>Cron <span className="muted">(min hour day month weekday)</span>
      <input value={cron} onChange={e => { setCron(e.target.value); setPreset('custom') }} placeholder="0 9 * * *" spellCheck={false} />
    </label>
    <label>If a previous run is still going
      <div className="seg sched-seg">
        <button type="button" className={overlap === 'skip' ? 'active' : ''} onClick={() => setOverlap('skip')}>Skip</button>
        <button type="button" className={overlap === 'allow' ? 'active' : ''} onClick={() => setOverlap('allow')}>Allow overlap</button>
      </div>
    </label>
    <label className="wf-step-check"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} /> Enabled</label>
    {hasInputs
      ? declared.map(x => <label key={x.id}>{x.label}{x.required && <span className="muted"> (required)</span>}
          <input type={x.kind === 'number' ? 'number' : x.kind === 'url' ? 'url' : 'text'} value={values[x.id] || ''} onChange={e => setValues(v => ({ ...v, [x.id]: e.target.value }))} placeholder={x.kind === 'file' ? 'Path or URL' : x.label} />
        </label>)
      : <label>Brief <span className="muted">(optional context for each run)</span><textarea rows={3} value={brief} onChange={e => setBrief(e.target.value)} placeholder="What should each run focus on?" /></label>}
    <div className="modal-actions">
      <button className="ghost-button" onClick={close} disabled={busy}>Close</button>
      <button className="primary-button" disabled={busy} onClick={() => void create()}>{busy ? 'Saving…' : 'Add schedule'}</button>
    </div>
    {schedules.length > 0 && <div className="sched-list">
      <p className="eyebrow">Existing schedules</p>
      {schedules.map(s => <div className="sched-row" key={s.id}>
        <span className="sched-cron" title={s.cron}>{cronHint(s.cron)}</span>
        <span className="muted sched-policy">{s.overlap_policy === 'allow' ? 'overlap' : 'skip'}</span>
        <label className="sched-toggle" title={s.enabled ? 'Enabled' : 'Disabled'}><input type="checkbox" checked={s.enabled} disabled={busy} onChange={() => void toggle(s)} /> {s.enabled ? 'on' : 'off'}</label>
        <button className="row-action danger" title="Delete schedule" aria-label="Delete schedule" onClick={() => void remove(s)} disabled={busy}>×</button>
      </div>)}
    </div>}
  </div></div>
}

export function WorkflowsScreen({ mode = 'sequential', onModeChange, advancedContent, token, projects, activeProject, onActiveProject, features, profiles, activeProfile, onOpenJob, draft, onDraftConsumed }: {
  mode?: 'sequential' | 'advanced' | 'scheduled'; onModeChange?: (mode: 'sequential' | 'advanced' | 'scheduled') => void; advancedContent?: React.ReactNode; token: string; projects: Project[]; activeProject: Project | null; onActiveProject?: (p: Project) => void
  // The editor's test bench is a real agent chat, so it needs the same context Code's is given.
  features: AppFeatures; profiles: Profile[]; activeProfile: Profile | null
  onOpenJob: (jobId: number) => void
  draft?: WorkflowDraft | null; onDraftConsumed?: () => void
}) {
  const [slug, setSlug] = React.useState(activeProject?.slug || projects[0]?.slug || '')
  const [workflows, setWorkflows] = React.useState<Workflow[]>([])
  const [editor, setEditor] = React.useState<EditorState | null>(null)
  const [running, setRunning] = React.useState<Workflow | null>(null)
  const [scheduling, setScheduling] = React.useState<Workflow | null>(null)
  const [error, setError] = React.useState('')
  const [actionKey, setActionKey] = React.useState<string | null>(null)
  const loadSeq = React.useRef(0)
  const actionSeq = React.useRef(0)
  const mountedRef = React.useRef(true)
  const actionRef = React.useRef<string | null>(null)
  const project = projects.find(p => p.slug === slug) || null

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      loadSeq.current += 1
      actionSeq.current += 1
      actionRef.current = null
    }
  }, [])

  React.useEffect(() => { if (activeProject && activeProject.slug !== slug) setSlug(activeProject.slug) }, [activeProject?.slug])
  const pickProject = React.useCallback((s: string) => {
    if (actionRef.current) return
    setSlug(s)
    const p = projects.find(x => x.slug === s)
    if (p) onActiveProject?.(p)
  }, [projects, onActiveProject])

  const reload = React.useCallback(async () => {
    const seq = ++loadSeq.current
    try {
      const rows = await listWorkflows(token, { project_slug: slug || null })
      if (!mountedRef.current || seq !== loadSeq.current) return
      setError('')
      setWorkflows(rows)
    } catch (e) {
      if (mountedRef.current && seq === loadSeq.current) setError(String(e))
    }
  }, [token, slug])
  React.useEffect(() => { void reload() }, [reload])

  // A promoted-chat draft opens straight into the editor (unsaved).
  React.useEffect(() => {
    if (!mountedRef.current || !draft) return
    setEditor({ id: null, name: draft.name || '', description: draft.description || '', category: draft.category || '', inputs: draft.inputs || [], steps: (draft.steps || []).map(s => ({ name: s.name || '', instruction: s.instruction || '', expected_output: s.expected_output || '', type: s.type || 'task', rules: '', skill_ids: [], review_required: false })) })
    onDraftConsumed?.()
  }, [draft, onDraftConsumed])

  function beginAction(key: string) {
    if (!mountedRef.current || actionRef.current) return false
    actionRef.current = key
    setActionKey(key)
    setError('')
    return true
  }

  function endAction(key: string) {
    if (!mountedRef.current || actionRef.current !== key) return
    actionRef.current = null
    setActionKey(null)
  }

  async function archive(w: Workflow) {
    const key = `archive:${w.id}`
    if (!beginAction(key)) return
    const seq = ++actionSeq.current
    try {
      if (!(await confirmDialog({ title: `Archive "${w.name}"?`, message: 'It will be hidden from the list.', confirmLabel: 'Archive', danger: true }))) return
      if (!mountedRef.current || seq !== actionSeq.current) return
      await archiveWorkflow(token, w.id)
      if (mountedRef.current && seq === actionSeq.current) await reload()
    } catch (e) {
      if (mountedRef.current && seq === actionSeq.current) setError(String(e))
    } finally {
      endAction(key)
    }
  }

  async function run(w: Workflow, input: any) {
    const seq = ++actionSeq.current
    try {
      const job = await createJob(token, { workflow_id: w.id, project_slug: slug, input })
      await startJob(token, job.id)
      if (!mountedRef.current || seq !== actionSeq.current) return
      setRunning(null)
      onOpenJob(job.id)
    } catch (e) {
      const msg = String(e)
      if (mountedRef.current && seq === actionSeq.current) setError(msg)
      throw e
    }
  }

  const modeNav = <div className="workflow-mode-nav seg" role="tablist" aria-label="Workflow type"><button className={mode === 'sequential' ? 'active' : ''} role="tab" aria-selected={mode === 'sequential'} onClick={() => onModeChange?.('sequential')}>Sequential</button>{advancedContent && <button className={mode === 'advanced' ? 'active' : ''} role="tab" aria-selected={mode === 'advanced'} onClick={() => onModeChange?.('advanced')}>Advanced</button>}<button className={mode === 'scheduled' ? 'active' : ''} role="tab" aria-selected={mode === 'scheduled'} onClick={() => onModeChange?.('scheduled')}>Scheduled</button></div>

  if (mode === 'advanced' && advancedContent) return <section className="workflow-advanced-view">{modeNav}{advancedContent}</section>
  if (mode === 'scheduled') return <section className="tasks-view scheduled-view">{modeNav}<ScheduleManager token={token} workflows={workflows} onOpenJob={onOpenJob} /></section>

  if (editor) return <section className="tasks-view"><WorkflowEditor token={token} init={editor} projectSlug={slug || null}
    features={features} profiles={profiles} activeProfile={activeProfile}
    onBack={() => setEditor(null)}
    onSaved={async w => { setEditor(null); await reload(); void w }}
    onDeleted={() => { setEditor(null); void reload() }} /></section>

  return <section className="tasks-view">
    {modeNav}
    <div className="tasks-head">
      {projects.length > 0 && <Dropdown value={slug} onChange={pickProject} minWidth={200} disabled={!!actionKey} options={projects.map(p => ({ value: p.slug, label: clean(p.name) }))} />}
      <button className="primary-button" disabled={!!actionKey} onClick={() => setEditor({ id: null, name: '', description: '', category: '', inputs: [], steps: [blankStep()] })}>New workflow</button>
    </div>
    {error && <div className="error-bar">{error}</div>}
    {workflows.length === 0
      ? <div className="placeholder-view"><div className="assistant-bubble compact"><h1>Workflows</h1><p className="muted">No workflows yet. Create a reusable recipe, or turn a chat into one.</p></div></div>
      : <div className="wf-grid">{workflows.map((w, i) => <div className="wf-card stagger-item" style={{ ['--i' as string]: i } as React.CSSProperties} key={w.id}>
          <button className="kanban-del" title="Archive" aria-label="Archive workflow" onClick={() => void archive(w)} disabled={!!actionKey}>×</button>
          <button className="wf-card-main" disabled={!!actionKey} onClick={() => setEditor({ id: w.id, name: w.name, description: w.description, category: w.category, inputs: w.inputs || [], steps: w.steps.map(s => ({ name: s.name, instruction: s.instruction, expected_output: s.expected_output, type: s.type, rules: s.rules ?? '', skill_ids: s.skill_ids ?? [], review_required: s.review_required })) })}>
            <strong>{w.name}</strong>
            {w.description && <small className="wf-card-desc">{w.description}</small>}
            <span className="wf-card-meta">{w.category && <span className="pill">{w.category}</span>}<span className="muted">{w.steps.length} step{w.steps.length !== 1 ? 's' : ''}</span></span>
          </button>
          {/* No Iterate here: opening a workflow opens the editor, which has the test
              bench built in. One window to write it and try it. */}
          <div className="wf-card-foot"><button className="ghost-button" onClick={() => setScheduling(w)} disabled={!!actionKey}>Schedule</button><button className="ghost-button" onClick={() => setRunning(w)} disabled={!!actionKey}>Run</button></div>
        </div>)}</div>}
    {running && <RunModal title={running.name} inputs={running.inputs} onCancel={() => setRunning(null)} onRun={input => run(running, input)} />}
    {scheduling && <ScheduleModal token={token} workflow={scheduling} onClose={() => setScheduling(null)} />}
  </section>
}
