import React from 'react'
import {
  approveGraphJob,
  approveGraphNode,
  createGraphJob,
  deleteGraphJob,
  deleteGraphTemplate,
  editGraphNodeOutput,
  setGraphTemplateStatus,
  getGraphJob,
  listGraphJobs,
  listGraphTemplates,
  rerunGraphNode,
  saveGraphTemplate,
  startGraphJob,
  updateGraphPlan,
} from '../api/graph'
import { Dropdown } from '../components/ui/Dropdown'
import { runnerCapabilities } from '../api/profiles'
import { listProjectAreas } from '../api/projects'
import { IconTrash } from '../components/shell/icons'
import { GraphCanvas, stateFor, statusLabel } from '../components/workflows/GraphCanvas'
import { SaveTemplateModal } from '../components/workflows/SaveTemplateModal'
import { MentionTextarea } from '../components/ui/MentionTextarea'
import { confirmDialog } from '../components/ui/Dialog'
import { RunModal } from '../components/workflows/RunModal'
import { AuthoringChat, type WorkflowChatHandle } from '../components/workflows/AuthoringChat'
import { buildGraphPrompt, buildNodeTestPrompt, parseGraphDraft, stripGraphBlock } from '../components/workflows/graphPrompt'
import { usePolling } from '../hooks/usePolling'
import { useProjectMentionItems } from '../hooks/useProjectMentionItems'
import type {
  AppFeatures,
  GraphJob,
  GraphNodeDefinition,
  GraphNodeState,
  GraphOutputKind,
  GraphTemplate,
  GraphWorkflowDraft,
  DetectedSkill,
  Profile,
  Project,
  WorkflowGraph,
  WorkflowInput,
} from '../types'
import { layoutGraph } from './graphLayout'

const OUTPUT_KINDS: GraphOutputKind[] = ['text', 'json', 'artifact-ref']

function outputText(state?: GraphNodeState): string {
  if (state?.output == null) return ''
  return typeof state.output === 'string' ? state.output : JSON.stringify(state.output, null, 2)
}

/** Plan statuses phrased as what the owner can do next — "kok gak bisa diedit?" should
 *  be answered by the label itself, not by trial and error. */
function planStatusLabel(status: GraphJob['status']): string {
  switch (status) {
    case 'queued': return 'Draft — editable'
    case 'running': return 'Running…'
    case 'review': return 'Needs your review'
    case 'done': return 'Done'
    case 'failed': return 'Failed'
    default: return statusLabel(status)
  }
}

const clampWidth = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value))

/** A draggable panel width, persisted per panel — so the owner can widen whichever
 *  pane they are focused on (the node inspector, most of all) and keep it. */
function useDragWidth(key: string, fallback: number, min: number, max: number): [number, (event: React.PointerEvent) => void] {
  const [width, setWidth] = React.useState(() => {
    const raw = typeof localStorage !== 'undefined' ? Number(localStorage.getItem(key)) : NaN
    return Number.isFinite(raw) && raw > 0 ? clampWidth(raw, min, max) : fallback
  })
  React.useEffect(() => { localStorage.setItem(key, String(width)) }, [key, width])
  const start = React.useCallback((event: React.PointerEvent) => {
    event.preventDefault()
    const pointerId = event.pointerId
    const startX = event.clientX
    // Handles sit on the panel's right edge except the inspector's, which sits on its
    // left — the handle says which way growth goes.
    const direction = (event.currentTarget as HTMLElement).dataset.grow === 'left' ? -1 : 1
    let base = 0
    setWidth(current => { base = current; return current })
    const onMove = (move: PointerEvent) => {
      if (move.pointerId !== pointerId) return
      setWidth(clampWidth(base + direction * (move.clientX - startX), min, max))
    }
    const onUp = (up: PointerEvent) => {
      if (up.pointerId !== pointerId) return
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
    }
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
  }, [min, max])
  return [width, start]
}

/** True when `from` can already be reached from `to`, i.e. the edge would cycle. */
function wouldCycle(graph: WorkflowGraph, from: string, to: string): boolean {
  const seen = new Set<string>()
  const stack = [to]
  while (stack.length) {
    const current = stack.pop() as string
    if (current === from) return true
    if (seen.has(current)) continue
    seen.add(current)
    for (const edge of graph.edges) if (edge.from === current) stack.push(edge.to)
  }
  return false
}


export function GraphScreen({
  token,
  projects,
  activeProject,
  onActiveProject,
  profiles,
  profileId,
  features,
  activeProfile,
  pendingDraft,
  onDraftConsumed,
  pendingJobId,
  onPendingConsumed,
  onStageChange,
  backNonce,
}: {
  token: string
  projects: Project[]
  activeProject: Project | null
  onActiveProject?: (project: Project) => void
  profiles: Profile[]
  profileId?: number | null
  features: AppFeatures
  activeProfile: Profile | null
  pendingDraft?: GraphWorkflowDraft | null
  onDraftConsumed?: () => void
  pendingJobId?: number | null
  onPendingConsumed?: () => void
  /** Lets the shell place the back control in its own chrome (the tab row). */
  onStageChange?: (stage: 'home' | 'editor') => void
  backNonce?: number
}) {
  const [jobs, setJobs] = React.useState<GraphJob[]>([])
  const [templates, setTemplates] = React.useState<GraphTemplate[]>([])
  const [job, setJob] = React.useState<GraphJob | null>(null)
  const [selectedId, setSelectedId] = React.useState<string | null>(null)
  const [chatWidth, dragChat] = useDragWidth('proxima.graph.chatWidth', 352, 240, 620)
  const [inspectorWidth, dragInspector] = useDragWidth('proxima.graph.inspectorWidth', 336, 260, 720)
  const [draftsOpen, setDraftsOpen] = React.useState(() => localStorage.getItem('proxima.graph.col.drafts') !== '0')
  const [templatesOpen, setTemplatesOpen] = React.useState(() => localStorage.getItem('proxima.graph.col.templates') !== '0')
  const [runsOpen, setRunsOpen] = React.useState(() => localStorage.getItem('proxima.graph.col.runs') !== '0')
  React.useEffect(() => { localStorage.setItem('proxima.graph.col.drafts', draftsOpen ? '1' : '0') }, [draftsOpen])
  React.useEffect(() => { localStorage.setItem('proxima.graph.col.templates', templatesOpen ? '1' : '0') }, [templatesOpen])
  React.useEffect(() => { localStorage.setItem('proxima.graph.col.runs', runsOpen ? '1' : '0') }, [runsOpen])
  // Two stages, Design Studio's shape: a browsable home, and an editor focused on
  // one workflow. Browsing and editing are different modes of work.
  const [stage, setStage] = React.useState<'home' | 'editor'>('home')
  const [heroText, setHeroText] = React.useState('')
  // Hero hand-off: the description the chat should speak first once the editor opens.
  const [initialAuthorText, setInitialAuthorText] = React.useState<string | null>(null)
  const [chatOpen, setChatOpen] = React.useState(false)
  const chatRef = React.useRef<WorkflowChatHandle>(null)
  // A test asked for while the chat panel is closed: the panel must mount before the
  // ref exists, so the request waits one render here.
  const [pendingTest, setPendingTest] = React.useState<string | null>(null)
  const [skillsByRunner, setSkillsByRunner] = React.useState<Record<string, DetectedSkill[]>>({})
  const skillFetches = React.useRef(new Set<string>())
  // The plan project's code areas (T1) — the vocabulary of the "Works in" field
  // and the authoring chat's target instruction. Keyed per slug so switching
  // projects never shows another project's repos.
  const [areasBySlug, setAreasBySlug] = React.useState<Record<string, string[]>>({})
  const loadSkills = React.useCallback((runnerId: string | undefined) => {
    if (!runnerId || skillFetches.current.has(runnerId)) return
    skillFetches.current.add(runnerId)
    runnerCapabilities(token, runnerId)
      .then(caps => { if (mounted.current) setSkillsByRunner(current => ({ ...current, [runnerId]: caps.skills })) })
      .catch(() => { skillFetches.current.delete(runnerId) })
  }, [token])
  const [savingTemplate, setSavingTemplate] = React.useState(false)
  // A template whose declared inputs must be answered before its run is created.
  const [runningTemplate, setRunningTemplate] = React.useState<GraphTemplate | null>(null)
  // Template metadata the authoring chat proposed (name, declared inputs, …). A job has
  // nowhere to persist these — only a saved template does — so they ride along here and
  // pre-fill the Save-template modal instead of being silently dropped.
  const [draftMeta, setDraftMeta] = React.useState<{ name?: string; description?: string; category?: string; inputs?: WorkflowInput[] }>({})
  const [plan, setPlan] = React.useState<WorkflowGraph | null>(null)
  const [dirty, setDirty] = React.useState(false)
  const [outputEdit, setOutputEdit] = React.useState('')
  const [error, setError] = React.useState('')
  const [notice, setNotice] = React.useState('')
  const [busy, setBusy] = React.useState<string | null>(null)
  const mounted = React.useRef(true)
  const loadSeq = React.useRef(0)
  const draftSeq = React.useRef(0)

  React.useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      loadSeq.current += 1
      draftSeq.current += 1
    }
  }, [])

  const refreshList = React.useCallback(async () => {
    const seq = ++loadSeq.current
    try {
      const [jobResponse, templateResponse] = await Promise.all([
        listGraphJobs(token, activeProject?.slug),
        listGraphTemplates(token, activeProject?.slug),
      ])
      if (mounted.current && seq === loadSeq.current) {
        setJobs(jobResponse.items)
        setTemplates(templateResponse.items)
      }
    } catch (cause) {
      if (mounted.current && seq === loadSeq.current) setError(String(cause))
    }
  }, [token, activeProject?.slug])

  const loadJob = React.useCallback(async (jobId: number) => {
    const seq = ++loadSeq.current
    try {
      const next = await getGraphJob(token, jobId)
      if (!mounted.current || seq !== loadSeq.current) return
      setJob(next)
      setPlan(next.graph)
      setDirty(false)
      // Open on the graph, not on a node nobody asked about. Keeps the live poll
      // from clearing a selection, but drops one whose node is gone.
      setSelectedId(current => current && next.graph.nodes.some(node => node.id === current) ? current : null)
    } catch (cause) {
      if (mounted.current && seq === loadSeq.current) setError(String(cause))
    }
  }, [token])

  React.useEffect(() => { onStageChange?.(stage) }, [stage, onStageChange])
  React.useEffect(() => {
    if (!selectedId) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      const target = event.target as HTMLElement | null
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return
      setSelectedId(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selectedId])
  // The back control lives in the shell's tab row; it pokes this nonce.
  const lastBack = React.useRef(backNonce ?? 0)
  React.useEffect(() => {
    if (backNonce == null || backNonce === lastBack.current) return
    lastBack.current = backNonce
    setStage('home')
    setNotice('')
    void refreshList()
  }, [backNonce, refreshList])

  const openJob = React.useCallback((jobId: number) => {
    setStage('editor')
    void loadJob(jobId)
  }, [loadJob])

  React.useEffect(() => { void refreshList() }, [refreshList])

  React.useEffect(() => {
    if (!pendingJobId) return
    openJob(pendingJobId)
    onPendingConsumed?.()
  }, [pendingJobId, openJob, onPendingConsumed])

  React.useEffect(() => {
    if (!pendingDraft) return
    const seq = ++draftSeq.current
    onDraftConsumed?.()
    setBusy('create')
    setError('')
    void createGraphJob(token, {
      title: pendingDraft.name,
      graph: pendingDraft.graph,
      project_slug: activeProject?.slug,
      profile_id: profileId,
    }).then(created => {
      if (!mounted.current || seq !== draftSeq.current) return
      setStage('editor')
      setJob(created)
      setPlan(created.graph)
      setSelectedId(null)
      setJobs(current => [created, ...current.filter(item => item.id !== created.id)])
      setNotice('Architect draft ready. Review or edit the frozen plan before starting.')
    }).catch(cause => {
      if (mounted.current && seq === draftSeq.current) setError(String(cause))
    }).finally(() => {
      if (mounted.current && seq === draftSeq.current) setBusy(null)
    })
  }, [pendingDraft, token, activeProject?.slug, profileId, onDraftConsumed])

  usePolling(
    () => job ? loadJob(job.id) : undefined,
    1500,
    { enabled: !!job && ['running', 'review'].includes(job.status), immediate: false },
  )

  const definition = plan?.nodes.find(node => node.id === selectedId)
  const selectedState = job && selectedId ? stateFor(job, selectedId) : undefined
  React.useEffect(() => { setOutputEdit(outputText(selectedState)) }, [selectedState?.id, selectedState?.version])

  function updateSelected(patch: Partial<GraphNodeDefinition>) {
    if (!definition || !plan) return
    setPlan({ ...plan, nodes: plan.nodes.map(node => node.id === definition.id ? { ...node, ...patch } : node) })
    setDirty(true)
  }

  function toggleDependency(dependencyId: string) {
    if (!definition || !plan) return
    const exists = plan.edges.some(edge => edge.from === dependencyId && edge.to === definition.id)
    if (exists) {
      disconnect(dependencyId, definition.id)
      return
    }
    connect(dependencyId, definition.id)
  }

  function connect(from: string, to: string) {
    if (!plan) return
    if (plan.edges.some(edge => edge.from === from && edge.to === to)) return
    if (wouldCycle(plan, from, to)) {
      setError('That connection would make the workflow loop back on itself.')
      return
    }
    setError('')
    setPlan({ ...plan, edges: [...plan.edges, { from, to }] })
    setDirty(true)
  }

  function disconnect(from: string, to: string) {
    if (!plan) return
    setPlan({
      ...plan,
      edges: plan.edges.filter(edge => !(edge.from === from && edge.to === to)),
    })
    setDirty(true)
  }

  /** Fold an agent-authored graph into the plan on screen — never the database: the plan
   *  is on screen, so a background write would leave it stale and let the next Save undo
   *  the agent's work. Hand-placed positions survive by node id, so a redraw does not
   *  scatter the canvas the owner already arranged. */
  function applyGraphPatch(next: WorkflowGraph) {
    setPlan(current => {
      const placed = new Map((current?.nodes ?? []).map(node => [node.id, node]))
      return {
        ...next,
        nodes: next.nodes.map(node => {
          const previous = placed.get(node.id)
          return previous && typeof previous.x === 'number'
            ? { ...node, x: previous.x, y: previous.y }
            : node
        }),
      }
    })
    setDirty(true)
    setSelectedId(current => current && next.nodes.some(node => node.id === current) ? current : null)
  }

  function moveNode(nodeId: string, x: number, y: number) {
    setPlan(current => current && {
      ...current,
      nodes: current.nodes.map(node => node.id === nodeId ? { ...node, x, y } : node),
    })
    setDirty(true)
  }

  /** Drop a new node clear of the ones already placed, so it never lands hidden. */
  function freeSlot(current: WorkflowGraph): { x: number; y: number } {
    const placed = layoutGraph(current).nodes
    const right = Math.max(0, ...placed.map(node => node.x + node.width))
    const top = Math.min(...placed.map(node => node.y))
    return { x: right + 110, y: Number.isFinite(top) ? top : 40 }
  }

  function addNode() {
    if (!plan) return
    let index = plan.nodes.length + 1
    while (plan.nodes.some(node => node.id === `node-${index}`)) index += 1
    const node: GraphNodeDefinition = {
      id: `node-${index}`,
      type: 'agent',
      name: `Node ${index}`,
      instruction: '',
      output_kind: 'text',
      ...freeSlot(plan),
    }
    setPlan({ ...plan, nodes: [...plan.nodes, node] })
    setSelectedId(node.id)
    setDirty(true)
  }

  const mentionSlug = job?.project_slug ?? activeProject?.slug ?? undefined
  React.useEffect(() => {
    if (!mentionSlug || areasBySlug[mentionSlug]) return
    let live = true
    listProjectAreas(token, mentionSlug)
      .then(areas => { if (live && mounted.current) setAreasBySlug(current => ({ ...current, [mentionSlug]: areas.code_areas.map(area => area.rel_path) })) })
      .catch(() => { /* areas are an enhancement — the selector still offers Ops */ })
    return () => { live = false }
  }, [token, mentionSlug, areasBySlug])
  const codeAreas = (mentionSlug ? areasBySlug[mentionSlug] : undefined) ?? []
  const mentionItems = useProjectMentionItems(token, mentionSlug)

  React.useEffect(() => {
    if (!pendingTest || !chatOpen || !plan) return
    const index = plan.nodes.findIndex(node => node.id === pendingTest)
    if (index >= 0) chatRef.current?.runThrough(index, plan.nodes[index].name || pendingTest)
    setPendingTest(null)
  }, [pendingTest, chatOpen, plan])

  async function deletePlan(item: { id: number; title: string }) {
    const ok = await confirmDialog({
      title: 'Delete this plan?',
      message: `“${item.title}” and its run threads will be permanently deleted. Anything it already produced (artifacts, project files) stays.`,
      confirmLabel: 'Delete plan',
      danger: true,
    })
    if (!ok || busy) return
    setBusy('delete')
    setError('')
    try {
      await deleteGraphJob(token, item.id)
      if (!mounted.current) return
      setJobs(current => current.filter(row => row.id !== item.id))
      if (job?.id === item.id) { setJob(null); setPlan(null); setSelectedId(null); setChatOpen(false) }
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  // Pause ⇄ resume: the owner's rule is that only active templates run on a schedule,
  // so "this workflow needs fixing" is one click out of rotation, not a deletion.
  async function toggleTemplatePaused(template: GraphTemplate) {
    if (busy) return
    setBusy('template-status')
    setError('')
    try {
      const next = await setGraphTemplateStatus(token, template.id, template.status === 'active' ? 'draft' : 'active')
      if (mounted.current) setTemplates(current => current.map(row => row.id === template.id ? { ...row, status: next.status } : row))
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  async function deleteTemplate(template: GraphTemplate) {
    const ok = await confirmDialog({
      title: 'Delete this template?',
      message: `“${template.name}” will be permanently deleted, along with any schedules that run it. Past runs keep their frozen copy of the graph.`,
      confirmLabel: 'Delete template',
      danger: true,
    })
    if (!ok || busy) return
    setBusy('delete')
    setError('')
    try {
      await deleteGraphTemplate(token, template.id)
      if (mounted.current) setTemplates(current => current.filter(row => row.id !== template.id))
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  async function duplicatePlan() {
    if (!job || busy) return
    setBusy('duplicate')
    setError('')
    try {
      const created = await createGraphJob(token, {
        title: job.title,
        // The frozen snapshot, positions included — the copy starts as exactly what
        // ran, which is the whole point of revising rather than rebuilding.
        graph: job.graph,
        input: job.input,
        project_slug: job.project_slug ?? activeProject?.slug,
        profile_id: profileId,
      })
      if (!mounted.current) return
      setJob(created)
      setPlan(created.graph)
      setSelectedId(null)
      setJobs(current => [created, ...current.filter(item => item.id !== created.id)])
      setNotice('Editable copy created — the original stays as the run record.')
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  // The blank-plan entry point. Sequential's "New workflow" retired with it, and chat
  // promotion cannot be the only door into the editor — a starter trigger + first step
  // gives the canvas (or the authoring chat) something to build on.
  async function newPlan(description?: string) {
    if (busy) return
    setBusy('create')
    setError('')
    try {
      const created = await createGraphJob(token, {
        title: 'Untitled workflow',
        graph: {
          nodes: [
            { id: 'trigger', type: 'trigger', trigger_kind: 'manual', name: 'When I run it', instruction: '', output_kind: 'json' },
            { id: 'step-1', type: 'agent', name: 'Step 1', instruction: '', output_kind: 'text' },
          ],
          edges: [{ from: 'trigger', to: 'step-1' }],
        },
        project_slug: activeProject?.slug,
        profile_id: profileId,
      })
      if (!mounted.current) return
      setJob(created)
      setPlan(created.graph)
      setSelectedId(null)
      setChatOpen(true)
      setStage('editor')
      setJobs(current => [created, ...current.filter(item => item.id !== created.id)])
      if (description?.trim()) setInitialAuthorText(description.trim())
      else setNotice('New plan. Describe it in the chat, or build it on the canvas.')
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  function addTrigger() {
    if (!plan || plan.nodes.some(node => node.type === 'trigger')) return
    // Deliberately no x/y: a trigger has no dependencies, so the auto-layout puts
    // it in the first column, and connecting it shifts the rest along. Pinning it
    // by hand instead would leave a gap the moment the auto-placed nodes moved.
    const node: GraphNodeDefinition = {
      id: 'trigger',
      type: 'trigger',
      trigger_kind: 'manual',
      name: 'When I run it',
      instruction: '',
      output_kind: 'json',
    }
    setPlan({ ...plan, nodes: [node, ...plan.nodes] })
    setSelectedId(node.id)
    setDirty(true)
  }

  function removeNode() {
    if (!definition || !plan || plan.nodes.length <= 1) return
    const nodes = plan.nodes.filter(node => node.id !== definition.id)
    setPlan({
      nodes,
      edges: plan.edges.filter(edge => edge.from !== definition.id && edge.to !== definition.id),
    })
    setSelectedId(nodes[0]?.id ?? null)
    setDirty(true)
  }

  async function act(label: string, action: () => Promise<GraphJob>, message?: string) {
    if (busy) return
    setBusy(label)
    setError('')
    setNotice('')
    try {
      const next = await action()
      if (!mounted.current) return
      setJob(next)
      setPlan(next.graph)
      setDirty(false)
      setJobs(current => [next, ...current.filter(item => item.id !== next.id)])
      if (message) setNotice(message)
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  async function savePlan() {
    if (!job || !plan) return
    await act('save-plan', () => updateGraphPlan(token, job.id, plan), 'Plan changes saved.')
  }

  async function saveOutput() {
    if (!job || !definition) return
    let value: unknown = outputEdit
    if (definition.output_kind !== 'text') {
      try {
        value = JSON.parse(outputEdit)
      } catch {
        setError('JSON and artifact-reference outputs must be valid JSON.')
        return
      }
    }
    await act('save-output', () => editGraphNodeOutput(token, job.id, definition.id, value), 'Output corrected; dependent nodes were marked stale.')
  }

  async function saveTemplate(meta: { name: string; description: string; category: string; inputs: WorkflowInput[] }) {
    if (!job || busy) return
    setBusy('save-template')
    setError('')
    try {
      const template = await saveGraphTemplate(token, job.id, meta)
      if (mounted.current) {
        setSavingTemplate(false)
        setTemplates(current => [template, ...current.filter(item => item.id !== template.id)])
        setNotice(`Saved reusable workflow “${template.name}”.`)
      }
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  async function createFromTemplate(template: GraphTemplate, input?: Record<string, string>) {
    if (busy) return
    setBusy('use-template')
    setError('')
    try {
      const created = await createGraphJob(token, {
        title: template.name,
        graph: template.graph,
        input,
        workflow_id: template.id,
        project_slug: activeProject?.slug ?? template.project_slug,
        profile_id: profileId,
      })
      if (!mounted.current) return
      setRunningTemplate(null)
      setStage('editor')
      setJob(created)
      setPlan(created.graph)
      setSelectedId(null)
      setJobs(current => [created, ...current.filter(item => item.id !== created.id)])
      setNotice(`Created a queued run from “${template.name}”. Review the frozen plan before starting.`)
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  const allDone = !!job?.node_states.length && job.node_states.every(state => state.status === 'done')

  const doneCount = job?.node_states.filter(state => state.status === 'done').length ?? 0

  if (stage === 'home') {
    return <section className="graph-screen graph-home">
      <header className="graph-header">
        {projects.length > 0 && <Dropdown
          value={activeProject?.slug ?? ''}
          minWidth={200}
          onChange={value => { const project = projects.find(item => item.slug === value); if (project) onActiveProject?.(project) }}
          options={projects.map(project => ({ value: project.slug, label: project.name }))}
        />}
        <h1>Workflows</h1>
        <div className="graph-header-actions">
          <button className="ghost-button" onClick={() => void refreshList()}>Refresh</button>
        </div>
      </header>
      {error && <div className="error-bar">{error}</div>}
      {notice && <div className="graph-notice">{notice}</div>}

      <div className="graph-start">
        <div className="graph-start-inner">
          {activeProject && <p className="muted graph-project-tag">Building in <strong>{activeProject.name}</strong> · runs stay in this project</p>}
          <h1>What should this workflow do?</h1>
          <p className="muted graph-sub">Describe it and the agent draws the graph — independent branches run in parallel. Nothing's locked; you can rearrange everything on the canvas.</p>
          <div className="graph-prompt" onKeyDown={event => {
            if (!event.defaultPrevented && event.target instanceof HTMLTextAreaElement && (event.metaKey || event.ctrlKey) && event.key === 'Enter' && heroText.trim()) {
              void newPlan(heroText)
              setHeroText('')
            }
          }}>
            <MentionTextarea
              rows={3}
              items={mentionItems}
              value={heroText}
              placeholder="Describe your workflow — e.g. riset topik dari {{brief}}, tulis post X dan LinkedIn secara paralel, gabungkan jadi satu bundle"
              onChange={setHeroText}
              ariaLabel="Workflow brief"
            />
            <div className="graph-prompt-bar">
              <button className="ghost-button" disabled={!!busy} onClick={() => void newPlan()}>Blank canvas</button>
              <button className="primary-button" disabled={!!busy || !heroText.trim()} onClick={() => { void newPlan(heroText); setHeroText('') }}>{busy === 'create' ? 'Creating…' : 'Draw it →'}</button>
            </div>
          </div>

          {(() => {
            const drafts = jobs.filter(item => item.status === 'queued')
            const runs = jobs.filter(item => item.status !== 'queued')
            const attention = runs.filter(item => item.status === 'review' || item.status === 'running')
            const finished = runs.filter(item => item.status !== 'review' && item.status !== 'running')
            const planCard = (item: GraphJob) => <div key={item.id} className="graph-card">
              <button className="graph-card-main" onClick={() => openJob(item.id)}>
                <span className="graph-card-glyph" aria-hidden="true"><i /><i /><i /></span>
                <span className="graph-card-meta">
                  <strong>{item.title}</strong>
                  <small className={`graph-card-status st-${item.status}`}>{planStatusLabel(item.status)}</small>
                </span>
              </button>
              <div className="graph-card-actions">
                <button className="row-action danger" title="Delete" aria-label={`Delete ${item.title}`} disabled={!!busy} onClick={() => void deletePlan(item)}><IconTrash size={13} /></button>
              </div>
            </div>
            const column = (
              key: 'drafts' | 'templates' | 'runs',
              title: string,
              count: number,
              hint: string,
              open: boolean,
              toggle: () => void,
              body: React.ReactNode,
            ) => <div className={`graph-col${open ? ' open' : ''}`} key={key}>
              <button className="graph-col-head" onClick={toggle} aria-expanded={open}>
                <span className={`chevron ${open ? 'open' : ''}`}>▸</span>
                <span className="graph-col-title"><strong>{title} ({count})</strong><small>{hint}</small></span>
              </button>
              {open && <div className="graph-col-body">{body}</div>}
            </div>
            return <div className="graph-columns">
              {column('drafts', 'Drafts', drafts.length, 'being built — editable', draftsOpen, () => setDraftsOpen(v => !v),
                drafts.length === 0
                  ? <p className="muted graph-none">Nothing in progress.</p>
                  : drafts.map(planCard))}
              {column('templates', 'Templates', templates.length, 'run · schedule · pause', templatesOpen, () => setTemplatesOpen(v => !v),
                templates.length === 0
                  ? <p className="muted graph-none">None yet. Open a plan and press <em>Save template</em>.</p>
                  : templates.map(template => <div key={template.id} className="graph-card">
                      <button className="graph-card-main" disabled={!!busy} onClick={() => {
                        if (template.inputs?.length) setRunningTemplate(template)
                        else void createFromTemplate(template)
                      }}>
                        <span className="graph-card-glyph tpl" aria-hidden="true"><i /><i /><i /></span>
                        <span className="graph-card-meta">
                          <strong>{template.name}</strong>
                          <small className="muted">{template.status === 'active' ? 'Run → new draft' : 'Paused — schedules skip it'}</small>
                        </span>
                      </button>
                      <div className="graph-card-actions">
                        <button className="row-action" title={template.status === 'active' ? 'Pause (schedules stop firing)' : 'Resume scheduling'} aria-label={`${template.status === 'active' ? 'Pause' : 'Resume'} ${template.name}`} disabled={!!busy} onClick={() => void toggleTemplatePaused(template)}>{template.status === 'active' ? '⏸' : '▶'}</button>
                        <button className="row-action danger" title="Delete template" aria-label={`Delete template ${template.name}`} disabled={!!busy} onClick={() => void deleteTemplate(template)}><IconTrash size={13} /></button>
                      </div>
                    </div>))}
              {column('runs', 'Runs', runs.length, attention.length > 0 ? `${attention.length} need${attention.length === 1 ? 's' : ''} attention` : 'history — frozen', runsOpen, () => setRunsOpen(v => !v),
                <>
                  {runs.length === 0 && <p className="muted graph-none">No runs yet.</p>}
                  {attention.map(planCard)}
                  {finished.length > 0 && <details className="graph-finished">
                    <summary>Finished ({finished.length})</summary>
                    {finished.map(planCard)}
                  </details>}
                </>)}
            </div>
          })()}
        </div>
      </div>

      {runningTemplate && <RunModal
        title={runningTemplate.name}
        inputs={runningTemplate.inputs}
        confirmLabel="Create run"
        onCancel={() => setRunningTemplate(null)}
        onRun={async input => { await createFromTemplate(runningTemplate, input) }}
      />}
    </section>
  }

  return <section className="graph-screen">
    {/* One bar, not two: the Advanced tab already says where you are, so the
        eyebrow and the never-changing subtitle were spending 91px to repeat it. */}
    <header className="graph-header">
      {job?.status === 'queued' && <button
        className={`ghost-button graph-chat-toggle${chatOpen ? ' active' : ''}`}
        onClick={() => setChatOpen(open => !open)}
        aria-pressed={chatOpen}
      >Chat</button>}
      {/* The shared Dropdown, in the bar — it used to be a raw <select> in the rail,
          which is the very thing the shared component exists to replace. */}
      {projects.length > 0 && <Dropdown
        value={activeProject?.slug ?? ''}
        minWidth={200}
        onChange={value => {
          const project = projects.find(item => item.slug === value)
          if (project) onActiveProject?.(project)
        }}
        options={projects.map(project => ({ value: project.slug, label: project.name }))}
      />}
      <h1>{job?.title ?? 'Graph workflows'}</h1>
      {job && <>
        <span className={`graph-status st-${job.status}`} title={job.status !== 'queued' ? 'Structure is frozen after start — use Duplicate to edit' : undefined}>{planStatusLabel(job.status)}</span>
        <span className="graph-node-count">{doneCount}/{job.node_states.length} nodes</span>
      </>}
      {dirty && <span className="graph-dirty">Unsaved edits</span>}
      <div className="graph-header-actions">
        {job && plan && job.status !== 'queued' && <>
          <button className="ghost-button" onClick={() => setSavingTemplate(true)} disabled={!!busy}>Save template</button>
          <button className="ghost-button" onClick={() => void duplicatePlan()} disabled={!!busy}>
            {busy === 'duplicate' ? 'Copying…' : 'Duplicate to edit'}
          </button>
        </>}
        {job?.status === 'queued' && <>
          <button className="ghost-button" onClick={() => setSavingTemplate(true)} disabled={!!busy || dirty}>Save template</button>
          {/* Plan-level actions belong to the plan, not to whichever node happens
              to be selected — which is also what lets the inspector close. */}
          <button className="ghost-button" onClick={() => void savePlan()} disabled={!dirty || !!busy}>
            {busy === 'save-plan' ? 'Saving…' : 'Save plan'}
          </button>
          <button className="primary-button" onClick={() => void act('start', () => startGraphJob(token, job.id), 'Plan approved. Execution started.')} disabled={!!busy || dirty}>
            {busy === 'start' ? 'Starting…' : 'Approve plan & start'}
          </button>
        </>}
        {job?.status === 'review' && allDone && <button className="primary-button" onClick={() => void act('approve-job', () => approveGraphJob(token, job.id))} disabled={!!busy}>
          {busy === 'approve-job' ? 'Approving…' : 'Approve final result'}
        </button>}
      </div>
    </header>

    {error && <div className="error-bar">{error}</div>}
    {notice && <div className="graph-notice">{notice}</div>}
    {busy === 'create' && <p className="graph-loading">Materializing architect draft…</p>}

    <div className="graph-workspace" style={{
      ['--graph-chat-width' as string]: `${chatWidth}px`,
      ['--graph-inspector-width' as string]: `${inspectorWidth}px`,
    }}>
      {/* Chat left, artifact right — the house idiom (Design Studio does the same),
          and the standing rule: the agent edits the plan on screen, never the DB. */}
      {chatOpen && job && plan && <aside className="graph-chat-panel">
        <AuthoringChat
          ref={chatRef}
          token={token}
          features={features}
          profiles={profiles}
          activeProfile={activeProfile}
          projectSlug={job.project_slug ?? activeProject?.slug ?? null}
          // A graph job already owns a chat session — the one it was created with — so
          // the conversation is pinned to the plan without inventing a second thread.
          ensureSession={async () => job.session_id}
          buildPrompt={text => buildGraphPrompt({
            name: job.title,
            description: '',
            category: '',
            inputs: [],
            graph: plan,
          }, text, codeAreas)}
          applyReply={raw => {
            const patch = parseGraphDraft(raw)
            if (!patch?.graph) return false
            applyGraphPatch(patch.graph)
            setDraftMeta(current => ({
              name: patch.name ?? current.name,
              description: patch.description ?? current.description,
              category: patch.category ?? current.category,
              inputs: patch.inputs ?? current.inputs,
            }))
            return true
          }}
          stripBlock={stripGraphBlock}
          buildTestPrompt={index => buildNodeTestPrompt(
            { name: job.title, description: '', category: '', inputs: draftMeta.inputs ?? [], graph: plan },
            plan.nodes[index]?.id ?? '',
            job.input as Record<string, unknown> | undefined,
          )}
          mentionItems={mentionItems}
          initialMessage={initialAuthorText ?? undefined}
          onInitialConsumed={() => setInitialAuthorText(null)}
          idleHint="Describe the workflow and the agent draws the graph; ask for changes and it redraws it. Branches run at once. Separate from Code, scoped to this plan."
          placeholder="Describe or change the workflow…"
        />
      </aside>}
      {chatOpen && job && plan && <div className="graph-resize-handle" role="separator" aria-orientation="vertical" aria-label="Resize chat panel" onPointerDown={dragChat} />}
      <main className="graph-main">
        {!job || !plan
          ? <div className="graph-empty"><strong>Select a graph plan</strong><p className="muted">Architect drafts and graph executions appear here.</p></div>
          : <>
            <GraphCanvas
              job={job}
              plan={plan}
              profiles={profiles}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onDeselect={() => setSelectedId(null)}
              editable={job.status === 'queued'}
              onMoveNode={moveNode}
              onConnect={connect}
              onDisconnect={disconnect}
              onAddNode={addNode}
              onAddTrigger={addTrigger}
              hasTrigger={plan.nodes.some(node => node.type === 'trigger')}
            />
          </>}
      </main>

      {/* Only when there is something to inspect. An empty 294px column that says
          "select a node" is furniture the canvas could be using. */}
      {job && plan && definition && <div className="graph-resize-handle" role="separator" aria-orientation="vertical" aria-label="Resize node detail" data-grow="left" onPointerDown={dragInspector} />}
      {job && plan && definition && <aside className="graph-inspector">
            <div className="graph-inspector-head">
              <div><p className="graph-eyebrow">Node</p><h2>{definition.name}</h2></div>
              <span className={`graph-status st-${selectedState?.status ?? 'pending'}`}>{statusLabel(selectedState?.status ?? 'pending')}</span>
              <button className="row-action" onClick={() => setSelectedId(null)} aria-label="Close node inspector">×</button>
            </div>
            {job.status === 'queued' ? <div className="graph-plan-form">
              <label>Name<input value={definition.name} onChange={event => updateSelected({ name: event.target.value })} /></label>
              {definition.type === 'trigger' ? <>
                <label>Starts on<select value={definition.trigger_kind ?? 'manual'} disabled onChange={() => undefined}>
                  <option value="manual">Manual — I press start</option>
                </select></label>
                <p className="muted graph-field-note">
                  The trigger hands the workflow input to whatever it connects to. Schedules and
                  webhooks become further options here.
                </p>
              </> : <>
                <label>Instruction<MentionTextarea rows={5} items={mentionItems} value={definition.instruction} onChange={value => updateSelected({ instruction: value })} ariaLabel="Node instruction" /></label>
                <label>Expected output<MentionTextarea
                  rows={2}
                  items={mentionItems}
                  value={definition.expected_output ?? ''}
                  placeholder="What a good result looks like — @ mentions a project file"
                  onChange={value => updateSelected({ expected_output: value })}
                  ariaLabel="Expected output"
                /></label>
                <label>Rules <span className="muted">(optional)</span><MentionTextarea
                  rows={3}
                  items={mentionItems}
                  value={definition.rules ?? ''}
                  placeholder="Constraints on how to do it — @ mentions a project file"
                  onChange={value => updateSelected({ rules: value })}
                  ariaLabel="Node rules"
                /></label>
                {definition.target_ambiguous && <p className="graph-target-question" role="alert">
                  This job needs an answer before the plan can start: {definition.target_question || 'which area should it work in?'}
                </p>}
                <label>Works in<select
                  value={definition.target_ambiguous ? '' : (definition.target ?? '')}
                  onChange={event => {
                    const value = event.target.value
                    // Picking a target IS the answer to an ambiguous job (T1) —
                    // the question clears with the choice, never silently.
                    // touches_repo mirrors the server's derivation for live
                    // display only; the server recomputes it and never trusts it.
                    updateSelected({
                      target: value || null,
                      target_ambiguous: false,
                      target_question: null,
                      touches_repo: !!value && value !== 'ops',
                    })
                  }}
                >
                  {definition.target_ambiguous
                    ? <option value="">Choose where this job works…</option>
                    : <option value="">Anywhere — the project folder</option>}
                  <option value="ops">Ops — notes, reports, files</option>
                  {codeAreas.map(area => <option key={area} value={area}>
                    {area === '.' ? 'Repo — the project root' : `Repo — ${area}`}
                  </option>)}
                </select></label>
                {definition.touches_repo && <p className="muted graph-field-note">
                  A repo job: it gets its own isolated copy of the code, and you review the change before it lands.
                </p>}
                <label>Agent<select
                  value={definition.profile_id ?? ''}
                  onChange={event => updateSelected({
                    profile_id: event.target.value ? Number(event.target.value) : null,
                  })}
                >
                  <option value="">Default — this run’s agent</option>
                  {profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
                </select></label>
                {(() => {
                  const effectiveProfile = profiles.find(profile => profile.id === (definition.profile_id ?? profileId))
                    ?? profiles.find(profile => profile.id === profileId)
                  const runnerId = effectiveProfile?.runner_id
                  const detected = runnerId ? skillsByRunner[runnerId] : undefined
                  const chosen = definition.skill_ids ?? []
                  // Hints from the chat may name skills this runner does not detect —
                  // keep them visible so they can be unchecked, not silently kept.
                  const unknown = chosen.filter(id => !(detected ?? []).some(skill => skill.id === id))
                  const toggle = (id: string, on: boolean) => updateSelected({
                    skill_ids: on ? [...chosen, id] : chosen.filter(item => item !== id),
                  })
                  return <details className="graph-skills" onToggle={event => { if ((event.target as HTMLDetailsElement).open) loadSkills(runnerId) }}>
                    <summary>Skills <span className="muted">({chosen.length ? `${chosen.length} suggested` : 'optional'})</span></summary>
                    <p className="muted graph-field-note">Suggested to the agent in its prompt. What is actually enabled comes from the agent profile.</p>
                    {detected == null
                      ? <p className="muted">{runnerId ? 'Loading detected skills…' : 'Pick an agent first.'}</p>
                      : detected.length === 0 && unknown.length === 0
                        ? <p className="muted">No skills detected for this runner.</p>
                        : <>
                          {detected.map(skill => <label className="graph-check" key={skill.id} title={skill.description || undefined}>
                            <input type="checkbox" checked={chosen.includes(skill.id)} onChange={event => toggle(skill.id, event.target.checked)} />{skill.name || skill.id}
                          </label>)}
                          {unknown.map(id => <label className="graph-check graph-skill-unknown" key={id} title="Not detected for this runner">
                            <input type="checkbox" checked onChange={() => toggle(id, false)} />{id} <span className="muted">(not detected)</span>
                          </label>)}
                        </>}
                  </details>
                })()}
                <label>Output contract<select value={definition.output_kind} onChange={event => updateSelected({ output_kind: event.target.value as GraphOutputKind })}>
                  {OUTPUT_KINDS.map(kind => <option key={kind} value={kind}>{kind}</option>)}
                </select></label>
                <label className="graph-check"><input type="checkbox" checked={!!definition.review_required} onChange={event => updateSelected({ review_required: event.target.checked })} />Require human review</label>
              </>}
              {definition.type !== 'trigger' && <fieldset>
                <legend>Dependencies</legend>
                {/* The canvas gesture is drag-to-connect; this list is the same edit
                    for anyone not using a pointer. */}
                {plan.nodes.filter(node => node.id !== definition.id).map(node => <label className="graph-check" key={node.id}>
                  <input type="checkbox" checked={plan.edges.some(edge => edge.from === node.id && edge.to === definition.id)} onChange={() => toggleDependency(node.id)} />{node.name}
                </label>)}
              </fieldset>}
              <div className="graph-form-actions">
                {/* A dry run in the chat: the agent executes this node and its upstream
                    chain conversationally, so the instruction can be judged before
                    Approve & start. No job state is touched. */}
                {definition.type !== 'trigger' && <button
                  className="ghost-button"
                  disabled={!definition.instruction.trim()}
                  title={definition.instruction.trim() ? undefined : 'Write an instruction first'}
                  onClick={() => { setChatOpen(true); setPendingTest(definition.id) }}
                >Test in chat</button>}
                <button className="ghost-button danger" onClick={removeNode} disabled={plan.nodes.length <= 1}>Remove node</button>
              </div>
            </div> : <div className="graph-run-detail">
              <p>{definition.type === 'trigger'
                ? 'Manual trigger — this workflow starts when you press start.'
                : definition.instruction || 'No instruction.'}</p>
              {definition.expected_output && <div className="graph-node-detail">
                <p className="graph-eyebrow">Expected output</p><p>{definition.expected_output}</p>
              </div>}
              {definition.rules && <div className="graph-node-detail">
                <p className="graph-eyebrow">Rules</p><p>{definition.rules}</p>
              </div>}
              <dl>
                {definition.type !== 'trigger' && <div><dt>Works in</dt><dd>
                  {definition.target_ambiguous
                    ? 'Unanswered — where should it work?'
                    : definition.target == null ? 'The project folder'
                    : definition.target === 'ops' ? 'Ops'
                    : `Repo — ${definition.target === '.' ? 'the project root' : definition.target}`}
                </dd></div>}
                <div><dt>Output</dt><dd>{definition.output_kind}</dd></div>
                {definition.type !== 'trigger' && <div><dt>Agent</dt><dd>
                  {profiles.find(profile => profile.id === definition.profile_id)?.name ?? 'Run default'}
                </dd></div>}
                <div><dt>Attempt</dt><dd>{selectedState?.run_id ?? '—'}</dd></div>
              </dl>
              {selectedState?.inputs != null && <details><summary>Resolved inputs</summary><pre>{JSON.stringify(selectedState.inputs, null, 2)}</pre></details>}
              {selectedState?.error && <p className="error-text">{selectedState.error}</p>}
              {selectedState?.output != null ? <pre className="graph-output">{outputText(selectedState)}</pre> : <p className="muted">No validated output yet.</p>}
              {['review', 'done'].includes(job.status) && selectedState && ['done', 'review', 'failed'].includes(selectedState.status) && <>
                <label>Correct output<textarea rows={8} value={outputEdit} onChange={event => setOutputEdit(event.target.value)} /></label>
                <div className="graph-form-actions">
                  <button className="ghost-button" onClick={() => void act('rerun', () => rerunGraphNode(token, job.id, definition.id))} disabled={!!busy}>Rerun node</button>
                  <button className="ghost-button" onClick={() => void saveOutput()} disabled={!!busy || !outputEdit.trim()}>Save correction</button>
                  {selectedState.status === 'review' && <button className="primary-button" onClick={() => void act('approve-node', () => approveGraphNode(token, job.id, definition.id))} disabled={!!busy}>Approve node</button>}
                </div>
              </>}
            </div>}
      </aside>}
    </div>

    {savingTemplate && job && <SaveTemplateModal
      title={draftMeta.name ?? job.title}
      initial={draftMeta}
      busy={busy === 'save-template'}
      onCancel={() => setSavingTemplate(false)}
      onSave={meta => void saveTemplate(meta)}
    />}
    {runningTemplate && <RunModal
      title={runningTemplate.name}
      inputs={runningTemplate.inputs}
      confirmLabel="Create run"
      onCancel={() => setRunningTemplate(null)}
      onRun={async input => { await createFromTemplate(runningTemplate, input) }}
    />}
  </section>
}
