import React from 'react'
import { projectFs } from '../api/fsAdapter'
import { deleteSessionArtifact, fileUrl, listSessionArtifacts, type Artifact } from '../api/files'
import { listMessages } from '../api/sessions'
import { cancelRun, deleteRun } from '../api/runs'
import { getWorkflow, updateWorkflow, type StepInput } from '../api/workflows'
import { useEventStream } from '../hooks/useEventStream'
import { MiniPreview } from '../components/design/MiniPreview'
import { AppRunner } from '../components/files/AppRunner'
import { MessageContent } from '../components/chat/MessageContent'
import { confirmDialog } from '../components/ui/Dialog'
import type { ChatMessage, RunEvent, Workflow, WorkflowStep } from '../types'

type DesignCard = { id: string; title: string; type: string; path: string; w: number; h: number; art?: any }
const blankStep = (): WorkflowStep => ({ id: Math.random().toString(36).slice(2, 10), name: '', instruction: '', expected_output: '', type: 'other', rules: null, skill_ids: null, review_required: false, depends_on: null })
const ICON: Record<string, string> = { app: '▶', page: '🌐', doc: '📄', file: '📎' }
type RunCard = {
  runId: number
  label: string
  status: 'running' | 'done' | 'failed' | 'cancelled'
  content?: string
  error?: string
  artifacts: Artifact[]
  historyCount: number
  runIds: number[]
}

// The "Panggung" — the BIG pane beside the iterate chat. The recipe is editable here
// directly; the Result tab shows a UNIVERSAL view of whatever the dry-run produced —
// designs, runnable apps / pages (live preview), articles, and files — grouped by type.
export function IterateStage({ token, workflowId, sessionId, projectSlug, running = false, designStudioEnabled = false, onOpenDesign, onRunRecipe }: {
  token: string; workflowId: number; sessionId: number; projectSlug: string | null
  running?: boolean; designStudioEnabled?: boolean; onOpenDesign?: (id: string) => void; onRunRecipe?: (prompt?: string, label?: string, instantResult?: string) => void
}) {
  const [wf, setWf] = React.useState<Workflow | null>(null)
  const [steps, setSteps] = React.useState<WorkflowStep[]>([])
  const [sel, setSel] = React.useState(0)
  const [dirty, setDirty] = React.useState(false)
  const [saving, setSaving] = React.useState(false)
  const [designs, setDesigns] = React.useState<DesignCard[]>([])
  const [arts, setArts] = React.useState<Artifact[]>([])   // non-design artifacts
  const [tab, setTab] = React.useState<'recipe' | 'result'>('recipe')
  const [runnerOpen, setRunnerOpen] = React.useState(false)
  const [doc, setDoc] = React.useState<{ title: string; content: string } | null>(null)
  const [messages, setMessages] = React.useState<ChatMessage[]>([])
  const [events, setEvents] = React.useState<RunEvent[]>([])
  const [error, setError] = React.useState('')
  const seen = React.useRef<number | null>(null)
  const dirtyRef = React.useRef(false)
  dirtyRef.current = dirty
  const saveSeq = React.useRef(0)  // bumped on save; discards in-flight poll responses
  const loadSeq = React.useRef(0)
  const resultSeq = React.useRef(0)
  const docSeq = React.useRef(0)
  const [deletingArtifact, setDeletingArtifact] = React.useState<string | null>(null)
  const mountedRef = React.useRef(true)
  const projFs = React.useMemo(() => projectSlug ? projectFs(token, projectSlug, '') : null, [token, projectSlug])
  const resolveSrc = React.useCallback((s: string) => /^(https?:|data:|blob:)/.test(s) ? s : (projectSlug ? fileUrl(projectSlug, s) : s), [token, projectSlug])

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      loadSeq.current += 1
      saveSeq.current += 1
      resultSeq.current += 1
      docSeq.current += 1
    }
  }, [])

  React.useEffect(() => {
    const load = () => {
      const seq = ++loadSeq.current
      const saveVersion = saveSeq.current
      getWorkflow(token, workflowId).then(w => {
        if (!mountedRef.current || seq !== loadSeq.current) return
        setWf(w)
        if (!dirtyRef.current && saveSeq.current === saveVersion) setSteps(w.steps)
      }).catch(() => {})
    }
    load(); const t = setInterval(load, 3000)
    return () => { loadSeq.current += 1; clearInterval(t) }
  }, [token, workflowId])

  // Result is scoped to THIS iterate session's own output (not the whole project):
  // the backend attributes artifacts to the session per run; we just render them.
  const loadResult = React.useCallback(async () => {
    const seq = ++resultSeq.current
    let list: Artifact[] = []
    try { list = (await listSessionArtifacts(token, sessionId)).artifacts } catch { return }
    if (!mountedRef.current || seq !== resultSeq.current) return
    const designArts = list.filter(a => a.type === 'design')
    const others = list.filter(a => a.type !== 'design')
    const activeFs = projFs
    // Enrich designs with their artboard for the live thumbnail.
    const dCards = (await Promise.all(designArts.map(async a => {
      if (!activeFs) return null
      try { const f = await activeFs.read(`${a.path}/scene.json`); const s = JSON.parse(f.content); const ab = s.artboards?.[0] || {}; return { id: a.id || s.id, title: a.title, type: s.type || 'graphic', path: a.path, w: ab.width || 1080, h: ab.height || 1080, art: ab } as DesignCard } catch { return null }
    }))).filter(Boolean) as DesignCard[]
    if (!mountedRef.current || seq !== resultSeq.current) return
    setDesigns(dCards); setArts(others)
    const total = dCards.length + others.length
    if (seen.current != null && total > seen.current) setTab('result')
    seen.current = total
  }, [token, sessionId, projFs])
  const loadRunMessages = React.useCallback(async () => {
    try {
      const body = await listMessages(token, sessionId)
      if (mountedRef.current) setMessages(body.messages)
    } catch { /* ignore */ }
  }, [token, sessionId])
  React.useEffect(() => {
    resultSeq.current += 1
    docSeq.current += 1
    seen.current = null
    setDesigns([])
    setArts([])
    setDoc(null)
    setRunnerOpen(false)
    setMessages([])
    setEvents([])
    setError('')
  }, [token, sessionId, projectSlug])
  React.useEffect(() => {
    loadResult()
    loadRunMessages()
    const t = setInterval(loadResult, 4000)
    return () => {
      resultSeq.current += 1
      clearInterval(t)
    }
  }, [loadResult, loadRunMessages])
  const onEvent = React.useCallback((event: RunEvent) => {
    setEvents(e => [...e.filter(x => x.id !== event.id), event])
    if (event.type === 'run.started' || event.type === 'run.queued') setTab('result')
    if (event.type === 'message.complete' || event.type === 'run.failed' || event.type === 'run.completed' || event.type === 'run.cancelled') {
      setTab('result')
      void loadRunMessages()
      void loadResult()
    }
  }, [loadRunMessages, loadResult])
  useEventStream(token, sessionId, onEvent)

  const patch = (i: number, p: Partial<WorkflowStep>) => { setSteps(s => s.map((x, j) => j === i ? { ...x, ...p } : x)); setDirty(true) }
  const addStep = () => { setSteps(s => [...s, blankStep()]); setSel(steps.length); setDirty(true) }
  const delStep = (i: number) => { setSteps(s => s.filter((_, j) => j !== i)); setSel(s => Math.max(0, Math.min(i < s ? s - 1 : s, steps.length - 2))); setDirty(true) }
  const move = (i: number, dir: -1 | 1) => { const j = i + dir; if (j < 0 || j >= steps.length) return; setSteps(s => { const n = [...s];[n[i], n[j]] = [n[j], n[i]]; return n }); setSel(j); setDirty(true) }

  async function save(): Promise<boolean> {
    if (saving) return false
    const seq = ++saveSeq.current
    setSaving(true)
    try {
      const payload: StepInput[] = steps.filter(s => s.name.trim() || s.instruction.trim()).map(s => ({ name: s.name.trim() || 'Step', instruction: s.instruction.trim(), expected_output: s.expected_output?.trim() || undefined, type: s.type || undefined, rules: s.rules?.trim() || null, skill_ids: s.skill_ids?.length ? s.skill_ids : null, review_required: !!s.review_required }))
      const w = await updateWorkflow(token, workflowId, { steps: payload })
      if (!mountedRef.current || seq !== saveSeq.current) return false
      setWf(w); setSteps(w.steps); setDirty(false)
      return true
    } catch { return false /* keep dirty so the user can retry */ } finally { if (mountedRef.current && seq === saveSeq.current) setSaving(false) }
  }

  async function runRecipe() {
    if (saving || running) return
    if (dirty && !(await save())) return
    setTab('result')
    onRunRecipe?.()
  }

  async function runStep(i = sel) {
    if (saving || running) return
    const step = steps[i]
    if (!step) return
    if (dirty && !(await save())) return
    const label = `Run step ${i + 1}: ${step.name || 'Untitled'}`
    const exact = step.instruction.match(/^\s*Reply with exactly:\s*(.+?)\s*$/i)?.[1]?.trim()
    const textOnly = exact && /do not (create|modify|write|edit) files?/i.test(step.rules || '')
    setTab('result')
    onRunRecipe?.(`Dry-test only workflow step ${i + 1}: ${step.name || `Step ${i + 1}`}.
Do this step now, using prior chat outputs only if relevant.
Instruction: ${step.instruction || '-'}
Expected output: ${step.expected_output || '-'}
Rules: ${step.rules || '-'}
Finish with a short result summary and artifact/file links if created.`, label, textOnly ? exact : undefined)
  }

  async function cancelStageRun(runId: number) {
    try {
      await cancelRun(token, runId)
      await loadRunMessages()
    } catch { /* keep the running card visible if cancel failed */ }
  }

  async function openDoc(a: Artifact) {
    const activeFs = projFs
    if (!activeFs) return
    const seq = ++docSeq.current
    try {
      const f = await activeFs.read(a.path)
      if (mountedRef.current && seq === docSeq.current) setDoc({ title: a.title, content: f.content })
    } catch { /* ignore */ }
  }
  const openFile = (a: Artifact) => { if (projectSlug) window.open(fileUrl(projectSlug, a.type === 'design' ? `${a.path.replace(/\/$/, '')}/scene.json` : a.path), '_blank') }
  const openArtifact = (a: Artifact) => {
    if (a.type === 'doc') void openDoc(a)
    else if (a.type === 'design' && a.id && designStudioEnabled) onOpenDesign?.(a.id)
    else if (a.type === 'app' || a.type === 'page') setRunnerOpen(true)
    else openFile(a)
  }
  const dropArtifactRefs = React.useCallback((path: string) => {
    setArts(current => current.filter(x => x.path !== path))
    setDesigns(current => current.filter(x => (x as any).path !== path))
    setMessages(current => current.map(m => m.output_links?.length ? { ...m, output_links: m.output_links.filter(a => a.path !== path) } : m))
    setEvents(current => current.map(e => {
      const links = e.payload.output_links
      return Array.isArray(links) ? { ...e, payload: { ...e.payload, output_links: links.filter(a => (a as Artifact).path !== path) } } : e
    }))
  }, [])
  async function deleteArtifact(a: Artifact) {
    if (!(await confirmDialog({ title: `Delete ${a.title}?`, message: 'The artifact file and its result reference will be removed. This cannot be undone.', confirmLabel: 'Delete', danger: true }))) return
    setError('')
    setDeletingArtifact(a.path)
    try {
      await deleteSessionArtifact(token, sessionId, a.path)
      dropArtifactRefs(a.path)
      await loadResult()
      window.dispatchEvent(new CustomEvent('proxima:files-changed'))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      if (mountedRef.current) setDeletingArtifact(null)
    }
  }
  async function deleteResult(r: RunCard) {
    if (r.status === 'running') return
    if (!(await confirmDialog({ title: `Delete result "${r.label}"?`, message: 'This removes this step result, including rerun history, chat messages, and run events. Artifact files are kept unless you delete them separately.', confirmLabel: 'Delete result', danger: true }))) return
    setError('')
    try {
      await Promise.all(r.runIds.map(id => deleteRun(token, id)))
      const deleted = new Set(r.runIds)
      setEvents(current => current.filter(e => !deleted.has(e.run_id)))
      await loadRunMessages()
      await loadResult()
      window.dispatchEvent(new CustomEvent('proxima:files-changed'))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const cur = steps[sel]
  const runCards = React.useMemo<RunCard[]>(() => {
    const byRun = new Map<number, RunCard & { createdAt?: string }>()
    const labelByRun = new Map<number, string>()
    const latestByLabel = new Map<string, RunCard & { createdAt?: string }>()
    const artifactsByLabel = new Map<string, Map<string, Artifact>>()
    const historyByLabel = new Map<string, number>()
    const addArtifacts = (label: string, links?: Artifact[]) => {
      if (!links?.length) return
      const bucket = artifactsByLabel.get(label) || new Map<string, Artifact>()
      for (const a of links) bucket.set(`${a.type}:${a.path}`, a)
      artifactsByLabel.set(label, bucket)
    }
    for (let i = 0; i < messages.length; i++) {
      const m = messages[i]
      if (m.role === 'user') {
        const next = messages.slice(i + 1).find(x => x.run_id)
        if (next?.run_id) labelByRun.set(next.run_id, m.content)
      }
      if ((m.role === 'assistant' || m.role === 'error') && m.run_id) {
        const label = labelByRun.get(m.run_id) || `Run ${m.run_id}`
        addArtifacts(label, m.output_links as Artifact[] | undefined)
        byRun.set(m.run_id, {
          runId: m.run_id,
          label,
          status: m.role === 'error' ? 'failed' : 'done',
          content: m.content,
          error: m.role === 'error' ? m.content : undefined,
          artifacts: [],
          historyCount: 1,
          runIds: [m.run_id],
          createdAt: m.created_at,
        })
      }
    }
    for (const e of events) {
      if (!e.run_id) continue
      const label = String(e.payload.label || labelByRun.get(e.run_id) || `Run ${e.run_id}`)
      const cur = byRun.get(e.run_id) || { runId: e.run_id, label, status: 'running' as const, artifacts: [], historyCount: 1, runIds: [e.run_id], createdAt: e.created_at }
      cur.label = cur.label.startsWith('Run ') && e.payload.label ? label : cur.label
      cur.createdAt = cur.createdAt || e.created_at
      addArtifacts(cur.label, e.payload.output_links as Artifact[] | undefined)
      if (e.type === 'run.started' || e.type === 'run.queued') cur.status = 'running'
      if (e.type === 'run.failed') { cur.status = 'failed'; cur.error = String(e.payload.error || 'Run failed') }
      if (e.type === 'message.complete' && typeof e.payload.text === 'string' && !cur.content) cur.content = e.payload.text
      if (e.type === 'run.cancelled') cur.status = 'cancelled'
      if (e.type === 'run.completed' && cur.status === 'running') cur.status = 'done'
      byRun.set(e.run_id, cur)
    }
    for (const r of byRun.values()) {
      const key = r.label
      historyByLabel.set(key, (historyByLabel.get(key) || 0) + 1)
      const prev = latestByLabel.get(key)
      if (!prev || r.runId > prev.runId) latestByLabel.set(key, r)
    }
    return [...latestByLabel.values()]
      .sort((a, b) => b.runId - a.runId)
      .slice(0, 8)
      .map(r => {
        const runIds = [...byRun.values()].filter(x => x.label === r.label).map(x => x.runId)
        return {
          ...r,
          artifacts: [...(artifactsByLabel.get(r.label)?.values() || [])],
          historyCount: historyByLabel.get(r.label) || 1,
          runIds,
        }
      })
  }, [messages, events])
  const attachedArtifactKeys = new Set(runCards.flatMap(r => r.artifacts.map(a => `${a.type}:${a.path}`)))
  const unlinkedDesigns = designs.filter(d => !attachedArtifactKeys.has(`design:${d.id}`) && !attachedArtifactKeys.has(`design:${d.path}`))
  const unlinkedArts = arts.filter(a => !attachedArtifactKeys.has(`${a.type}:${a.path}`))
  const unlinkedPages = unlinkedArts.filter(a => a.type === 'app' || a.type === 'page')
  const unlinkedDocs = unlinkedArts.filter(a => a.type === 'doc')
  const unlinkedFiles = unlinkedArts.filter(a => a.type === 'file')
  const resultCount = runCards.length + unlinkedDesigns.length + unlinkedArts.length

  return <section className="iterate-stage">
    <header className="stage-head">
      <div className="stage-tabs">
        <button className={tab === 'recipe' ? 'on' : ''} onClick={() => setTab('recipe')}>Recipe<span className="stage-tab-n">{steps.length}</span></button>
        <button className={tab === 'result' ? 'on' : ''} onClick={() => setTab('result')}>Result{resultCount > 0 && <span className="stage-tab-n">{resultCount}</span>}</button>
      </div>
      <div className="stage-head-actions">
        {running && <span className="stage-run-state"><span className="stage-run-dot" />Running</span>}
        {tab === 'recipe' && <button className="ghost-button stage-save" onClick={() => void save()} disabled={!dirty || saving}>{saving ? 'Saving…' : dirty ? '● Save recipe' : 'Saved'}</button>}
        <button className="primary-button stage-run" onClick={() => void runRecipe()} disabled={!steps.length || saving || running} title="Run the current recipe end-to-end (dry-test) — the result is what it produces">▸ Run recipe</button>
      </div>
    </header>

    {tab === 'recipe' ? (
      <div className="stage-editor">
        <div className="stage-steplist">
          {steps.map((s, i) => (
            <button key={s.id || i} className={`stage-stepitem ${i === sel ? 'on' : ''}`} onClick={() => setSel(i)}>
              <span className="stage-step-n">{i + 1}</span>
              <span className="stage-stepitem-name">{s.name || <em className="muted">Untitled step</em>}</span>
              {s.rules && <span className="stage-tag rule" title={s.rules}>⚑</span>}
              {s.review_required && <span className="stage-tag" title="Pauses for review">⏸</span>}
            </button>
          ))}
          <button className="stage-addstep" onClick={addStep}>+ Add step</button>
        </div>
        <div className="stage-stepedit">
          {!cur ? <p className="stage-empty">No steps yet — add one to start building the recipe.</p> : <>
            <div className="stage-field-row">
              <input className="stage-stepname" value={cur.name} placeholder={`Step ${sel + 1} name`} onChange={e => patch(sel, { name: e.target.value })} />
              <div className="stage-step-actions">
                <button className="stage-step-run" title="Run only this step" onClick={() => void runStep(sel)} disabled={saving || running || !cur.instruction.trim()}>Run step</button>
                <button className="icon-btn" title="Move up" onClick={() => move(sel, -1)} disabled={sel === 0}>↑</button>
                <button className="icon-btn" title="Move down" onClick={() => move(sel, 1)} disabled={sel === steps.length - 1}>↓</button>
                <button className="icon-btn danger" title="Delete step" onClick={() => delStep(sel)}>✕</button>
              </div>
            </div>
            <label className="stage-label">Instruction (the prompt for this step)</label>
            <textarea className="stage-ta" rows={5} value={cur.instruction} placeholder="What the agent should do in this step…" onChange={e => patch(sel, { instruction: e.target.value })} />
            <label className="stage-label">Expected output <span className="muted">(optional)</span></label>
            <textarea className="stage-ta" rows={2} value={cur.expected_output || ''} placeholder="What this step should produce…" onChange={e => patch(sel, { expected_output: e.target.value })} />
            <label className="stage-label">Rules <span className="muted">(optional — hard constraints)</span></label>
            <textarea className="stage-ta" rows={2} value={cur.rules || ''} placeholder="Must-follow constraints for this step…" onChange={e => patch(sel, { rules: e.target.value || null })} />
            <label className="stage-check"><input type="checkbox" checked={!!cur.review_required} onChange={e => patch(sel, { review_required: e.target.checked })} /> Pause for my review after this step</label>
          </>}
        </div>
      </div>
    ) : (
      <div className="stage-body stage-result">
        {error && <p className="error-text">{error}</p>}
        {resultCount === 0
          ? <div className="stage-empty">
              <p>No results yet.<br />Run the recipe to see what it produces.</p>
              <button className="primary-button" onClick={() => void runRecipe()} disabled={!steps.length || saving || running}>▸ Run recipe</button>
            </div>
          : <>
            {runCards.length > 0 && <div className="result-group">
              <h4 className="result-grouplabel">Run results</h4>
              <div className="run-result-list">{runCards.map(r => (
                <div className={`run-result-card ${r.status}`} key={r.runId}>
                  <div className="run-result-head">
                    <strong>{r.label}</strong>
                    {r.historyCount > 1 && <small className="run-result-history">{r.historyCount} runs · latest shown</small>}
                    <span className="run-result-status">{r.status}</span>
                    {r.status === 'running' && <button className="run-result-cancel" onClick={() => void cancelStageRun(r.runId)}>Cancel</button>}
                    {r.status !== 'running' && <button className="run-result-delete" onClick={() => void deleteResult(r)}>Delete</button>}
                  </div>
                  {r.status === 'running' && <p className="muted">Agent is running this step...</p>}
                  {r.error && <p className="error-text">{r.error}</p>}
                  {r.content && <MessageContent content={r.content} token={token} slug={projectSlug || undefined} />}
                  {r.artifacts.length > 0 && <div className="run-result-artifacts">
                    <span className="run-result-subtitle">Attached output</span>
                    <div className="art-list">{r.artifacts.map(a => (
                      <div className="art-row" key={`${r.runId}:${a.type}:${a.path}`}>
                        <button className="art-card" onClick={() => openArtifact(a)} title={a.type === 'doc' ? 'Read' : a.type === 'design' && designStudioEnabled ? 'Open in Design Studio' : a.type === 'app' || a.type === 'page' ? 'Preview live' : 'Open file'}>
                          <span className="art-ic">{ICON[a.type] || ICON.file}</span>
                          <span className="art-meta"><strong>{a.title}</strong><small>{a.type === 'app' ? a.command : a.path}</small></span>
                          <span className="art-go">{a.type === 'doc' ? 'Read' : a.type === 'design' ? 'Open' : a.type === 'app' || a.type === 'page' ? 'Preview' : 'Open'} ▸</span>
                        </button>
                        <button className="art-delete" onClick={() => void deleteArtifact(a)} disabled={deletingArtifact === a.path}>Delete</button>
                      </div>
                    ))}</div>
                  </div>}
                </div>
              ))}</div>
            </div>}
            {(unlinkedDesigns.length > 0 || unlinkedArts.length > 0) && <div className="result-group">
              <h4 className="result-grouplabel">Unlinked artifacts</h4>
              {unlinkedDesigns.length > 0 && <div className="stage-designs">{unlinkedDesigns.map(d => (
                <button className="stage-design" key={d.id} onClick={() => designStudioEnabled ? onOpenDesign?.(d.id) : openFile({ type: 'design', id: d.id, title: d.title, path: d.path })} title={designStudioEnabled ? 'Open in Design Studio' : 'Open artifact'}>
                  <div className="ds-tpl-canvas stage-thumb"><MiniPreview art={d.art} resolveSrc={resolveSrc} /></div>
                  <span className="stage-d-title">{d.title}</span>
                  <span className="stage-d-meta">{d.type} · {d.w}×{d.h}</span>
                </button>
              ))}</div>
              }
              {unlinkedPages.length > 0 && <div className="art-list">{unlinkedPages.map(a => (
                <div className="art-row" key={a.path}>
                  <button className="art-card" onClick={() => setRunnerOpen(true)} title="Preview live">
                    <span className="art-ic">{ICON[a.type]}</span>
                    <span className="art-meta"><strong>{a.title}</strong><small>{a.type === 'app' ? a.command : a.path}</small></span>
                    <span className="art-go">Preview ▸</span>
                  </button>
                  <button className="art-delete" onClick={() => void deleteArtifact(a)} disabled={deletingArtifact === a.path}>Delete</button>
                </div>
              ))}</div>
              }
              {unlinkedDocs.length > 0 && <div className="art-list">{unlinkedDocs.map(a => (
                <div className="art-row" key={a.path}>
                  <button className="art-card" onClick={() => void openDoc(a)} title="Read">
                    <span className="art-ic">{ICON.doc}</span>
                    <span className="art-meta"><strong>{a.title}</strong><small>{a.path}</small></span>
                    <span className="art-go">Read ▸</span>
                  </button>
                  <button className="art-delete" onClick={() => void deleteArtifact(a)} disabled={deletingArtifact === a.path}>Delete</button>
                </div>
              ))}</div>
              }
              {unlinkedFiles.length > 0 && <div className="art-list">{unlinkedFiles.map(a => (
                <div className="art-row" key={a.path}>
                  <button className="art-card" onClick={() => openFile(a)} title="Open file">
                    <span className="art-ic">{ICON.file}</span>
                    <span className="art-meta"><strong>{a.title}</strong><small>{a.path}</small></span>
                    <span className="art-go">Open ▸</span>
                  </button>
                  <button className="art-delete" onClick={() => void deleteArtifact(a)} disabled={deletingArtifact === a.path}>Delete</button>
                </div>
              ))}</div>
              }
            </div>}
          </>}
      </div>
    )}

    {runnerOpen && projectSlug && <AppRunner token={token} slug={projectSlug} onClose={() => setRunnerOpen(false)} />}
    {doc && <div className="art-doc-scrim" onClick={() => setDoc(null)}>
      <div className="art-doc" onClick={e => e.stopPropagation()}>
        <header className="art-doc-head"><strong>{doc.title}</strong><button className="icon-btn" onClick={() => setDoc(null)}>✕</button></header>
        <div className="art-doc-body"><MessageContent content={doc.content} token={token} slug={projectSlug || undefined} /></div>
      </div>
    </div>}
  </section>
}
