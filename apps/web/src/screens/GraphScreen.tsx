import React from 'react'
import {
  approveGraphJob,
  approveGraphNode,
  createGraphJob,
  deleteGraphJob,
  deleteGraphTemplate,
  editGraphNodeOutput,
  getGraphJob,
  listGraphJobs,
  listGraphTemplates,
  rerunGraphNode,
  saveGraphTemplate,
  startGraphJob,
  updateGraphPlan,
} from '../api/graph'
import { Dropdown } from '../components/ui/Dropdown'
import { confirmDialog } from '../components/ui/Dialog'
import { RunModal } from '../components/workflows/RunModal'
import { AuthoringChat } from '../components/workflows/AuthoringChat'
import { buildGraphPrompt, parseGraphDraft, stripGraphBlock } from '../components/workflows/graphPrompt'
import type {
  AppFeatures,
  GraphJob,
  GraphNodeDefinition,
  GraphNodeState,
  GraphOutputKind,
  GraphTemplate,
  GraphWorkflowDraft,
  Profile,
  Project,
  WorkflowGraph,
  WorkflowInput,
} from '../types'
import { layoutGraph } from './graphLayout'

const OUTPUT_KINDS: GraphOutputKind[] = ['text', 'json', 'artifact-ref']

function stateFor(job: GraphJob, nodeId: string): GraphNodeState | undefined {
  return job.node_states.find(state => state.node_id === nodeId)
}

function outputText(state?: GraphNodeState): string {
  if (state?.output == null) return ''
  return typeof state.output === 'string' ? state.output : JSON.stringify(state.output, null, 2)
}

function statusLabel(status: GraphJob['status'] | GraphNodeState['status']): string {
  return status.replaceAll('_', ' ')
}

const ZOOM_MIN = 0.35
const ZOOM_MAX = 2.5
const HANDLE_RADIUS = 6

type CanvasView = { x: number; y: number; k: number }
type Point = { x: number; y: number }

/** Pointer gesture in progress. The canvas can only be doing one at a time. */
type Gesture =
  | { kind: 'pan'; from: Point; origin: Point }
  | { kind: 'node'; nodeId: string; grab: Point }
  | { kind: 'link'; from: string }
  | null

function edgePath(from: Point, to: Point): string {
  const bend = Math.max(36, (to.x - from.x) / 2)
  return `M ${from.x} ${from.y} C ${from.x + bend} ${from.y}, ${to.x - bend} ${to.y}, ${to.x} ${to.y}`
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

function GraphCanvas({ job, plan, profiles, selectedId, onSelect, editable, onMoveNode, onConnect, onDisconnect, onAddNode, onAddTrigger, hasTrigger }: {
  job: GraphJob
  plan: WorkflowGraph
  profiles: Profile[]
  selectedId: string | null
  onSelect: (nodeId: string) => void
  editable: boolean
  onMoveNode: (nodeId: string, x: number, y: number) => void
  onConnect: (from: string, to: string) => void
  onDisconnect: (from: string, to: string) => void
  onAddNode: () => void
  onAddTrigger: () => void
  hasTrigger: boolean
}) {
  const layout = React.useMemo(() => layoutGraph(plan), [plan])
  const positions = React.useMemo(
    () => new Map(layout.nodes.map(node => [node.id, node])),
    [layout.nodes],
  )
  const svgRef = React.useRef<SVGSVGElement | null>(null)
  const [view, setView] = React.useState<CanvasView>({ x: 0, y: 0, k: 1 })
  const [gesture, setGesture] = React.useState<Gesture>(null)
  const [linkAt, setLinkAt] = React.useState<Point | null>(null)
  const [selectedEdge, setSelectedEdge] = React.useState<string | null>(null)

  // A gesture reads the newest layout/view/callbacks without re-subscribing its
  // window listeners on every pointermove.
  const live = React.useRef({ view, layout, positions, plan, onMoveNode, onConnect })
  live.current = { view, layout, positions, plan, onMoveNode, onConnect }

  const toGraphPoint = React.useCallback((event: { clientX: number; clientY: number }): Point => {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect) return { x: 0, y: 0 }
    const { view: current } = live.current
    return {
      x: (event.clientX - rect.left - current.x) / current.k,
      y: (event.clientY - rect.top - current.y) / current.k,
    }
  }, [])

  const zoomTo = React.useCallback((nextK: number, focus?: Point) => {
    setView(current => {
      const k = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, nextK))
      const rect = svgRef.current?.getBoundingClientRect()
      const at = focus ?? { x: (rect?.width ?? 0) / 2, y: (rect?.height ?? 0) / 2 }
      // Keep whatever sits under `at` pinned there while the scale changes.
      return {
        k,
        x: at.x - (at.x - current.x) * (k / current.k),
        y: at.y - (at.y - current.y) * (k / current.k),
      }
    })
  }, [])

  // React attaches wheel listeners passively at the root, so a non-passive native
  // listener is the only way to zoom without the page scrolling underneath.
  React.useEffect(() => {
    const element = svgRef.current
    if (!element) return
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      const rect = element.getBoundingClientRect()
      const focus = { x: event.clientX - rect.left, y: event.clientY - rect.top }
      setView(current => {
        const k = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, current.k * Math.exp(-event.deltaY * 0.0015)))
        return {
          k,
          x: focus.x - (focus.x - current.x) * (k / current.k),
          y: focus.y - (focus.y - current.y) * (k / current.k),
        }
      })
    }
    element.addEventListener('wheel', onWheel, { passive: false })
    return () => element.removeEventListener('wheel', onWheel)
  }, [])

  const fit = React.useCallback(() => {
    const rect = svgRef.current?.getBoundingClientRect()
    const { layout: box } = live.current
    if (!rect || !box.width || !box.height) return
    const k = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.min(rect.width / box.width, rect.height / box.height)))
    // Offset by the content origin, which is negative once a node has been
    // dragged left of or above where the auto-layout started.
    setView({
      k,
      x: (rect.width - box.width * k) / 2 - box.x * k,
      y: (rect.height - box.height * k) / 2 - box.y * k,
    })
  }, [])

  // Frame the graph once it is known, so a plan never opens scrolled off-screen.
  const framed = React.useRef(false)
  React.useEffect(() => {
    if (framed.current || !layout.nodes.length) return
    framed.current = true
    fit()
  }, [fit, layout.nodes.length])

  // Listening on the window rather than capturing the pointer means a gesture
  // still completes when the pointer is released outside the canvas.
  React.useEffect(() => {
    if (!gesture) return
    const onMove = (event: PointerEvent) => {
      if (gesture.kind === 'pan') {
        setView(current => ({
          ...current,
          x: gesture.origin.x + (event.clientX - gesture.from.x),
          y: gesture.origin.y + (event.clientY - gesture.from.y),
        }))
        return
      }
      const point = toGraphPoint(event)
      if (gesture.kind === 'node') {
        live.current.onMoveNode(
          gesture.nodeId,
          Math.round(point.x - gesture.grab.x),
          Math.round(point.y - gesture.grab.y),
        )
        return
      }
      setLinkAt(point)
    }
    const onUp = (event: PointerEvent) => {
      if (gesture.kind === 'link') {
        const point = toGraphPoint(event)
        const { layout: currentLayout, plan: currentPlan, onConnect: connect } = live.current
        let target: string | null = null
        for (let index = currentLayout.nodes.length - 1; index >= 0; index -= 1) {
          const node = currentLayout.nodes[index]
          if (point.x >= node.x && point.x <= node.x + node.width
            && point.y >= node.y && point.y <= node.y + node.height) {
            target = node.id
            break
          }
        }
        const targetNode = currentPlan.nodes.find(node => node.id === target)
        // A trigger is an entry point, so it can never be the far end of an edge.
        if (target && targetNode && target !== gesture.from && targetNode.type !== 'trigger') {
          connect(gesture.from, target)
        }
      }
      setGesture(null)
      setLinkAt(null)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
    }
  }, [gesture, toGraphPoint])

  function beginPan(event: React.PointerEvent<SVGSVGElement>) {
    if (event.button !== 0) return
    setSelectedEdge(null)
    setGesture({ kind: 'pan', from: { x: event.clientX, y: event.clientY }, origin: { x: view.x, y: view.y } })
  }

  function beginNodeDrag(event: React.PointerEvent, nodeId: string) {
    if (event.button !== 0) return
    event.stopPropagation()
    onSelect(nodeId)
    if (!editable) return
    const position = positions.get(nodeId)
    if (!position) return
    const point = toGraphPoint(event)
    setGesture({ kind: 'node', nodeId, grab: { x: point.x - position.x, y: point.y - position.y } })
  }

  function beginLink(event: React.PointerEvent, nodeId: string) {
    if (event.button !== 0 || !editable) return
    event.stopPropagation()
    setLinkAt(toGraphPoint(event))
    setGesture({ kind: 'link', from: nodeId })
  }

  const linking = gesture?.kind === 'link' ? gesture : null
  const linkSource = linking ? positions.get(linking.from) : undefined

  return <div className="graph-canvas-scroll">
    {/* Adding a node is a canvas act, so it lives on the canvas — reachable
        whether or not a node happens to be selected. */}
    {editable && <div className="graph-canvas-tools">
      <button className="ghost-button" onClick={onAddNode}>+ Node</button>
      <button className="ghost-button" onClick={onAddTrigger} disabled={hasTrigger}>+ Trigger</button>
    </div>}
    <div className="graph-zoom-controls">
      <button className="row-action" onClick={() => zoomTo(view.k * 1.25)} aria-label="Zoom in">+</button>
      <button className="row-action" onClick={() => zoomTo(view.k / 1.25)} aria-label="Zoom out">−</button>
      <button className="row-action" onClick={fit} aria-label="Fit graph to view">⤢</button>
    </div>
    <svg
      ref={svgRef}
      className={`graph-canvas${gesture ? ` grabbing-${gesture.kind}` : ''}`}
      onPointerDown={beginPan}
      aria-label={`${job.title} workflow graph`}
    >
      <defs>
        <marker id="graph-arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto" markerUnits="strokeWidth">
          <path className="graph-arrow-head" d="M0,0 L0,6 L9,3 z" />
        </marker>
      </defs>
      <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
        {plan.edges.map(edge => {
          const source = positions.get(edge.from)
          const target = positions.get(edge.to)
          if (!source || !target) return null
          const key = `${edge.from}:${edge.to}`
          const from = { x: source.x + source.width, y: source.y + source.height / 2 }
          const to = { x: target.x, y: target.y + target.height / 2 }
          const selected = selectedEdge === key
          return <g key={key} className={`graph-edge-group${selected ? ' selected' : ''}`}>
            <path className="graph-edge" d={edgePath(from, to)} markerEnd="url(#graph-arrow)" />
            {/* A 2px curve is far too thin to click; this invisible one is the hit area. */}
            <path
              className="graph-edge-hit"
              d={edgePath(from, to)}
              onPointerDown={event => {
                if (!editable) return
                event.stopPropagation()
                setSelectedEdge(current => current === key ? null : key)
              }}
            />
            {selected && editable && <g
              className="graph-edge-delete"
              role="button"
              tabIndex={0}
              aria-label={`Remove connection from ${edge.from} to ${edge.to}`}
              onPointerDown={event => {
                event.stopPropagation()
                setSelectedEdge(null)
                onDisconnect(edge.from, edge.to)
              }}
              onKeyDown={event => {
                if (event.key !== 'Enter' && event.key !== ' ') return
                setSelectedEdge(null)
                onDisconnect(edge.from, edge.to)
              }}
            >
              <circle cx={(from.x + to.x) / 2} cy={(from.y + to.y) / 2} r="9" />
              <text x={(from.x + to.x) / 2} y={(from.y + to.y) / 2 + 4}>×</text>
            </g>}
          </g>
        })}

        {linking && linkSource && linkAt && <path
          className="graph-edge graph-edge-pending"
          d={edgePath(
            { x: linkSource.x + linkSource.width, y: linkSource.y + linkSource.height / 2 },
            linkAt,
          )}
        />}

        {layout.nodes.map(position => {
          const definition = plan.nodes.find(node => node.id === position.id)
          if (!definition) return null
          const state = stateFor(job, definition.id)
          const status = state?.status ?? 'pending'
          const trigger = definition.type === 'trigger'
          const agent = definition.profile_id
            ? profiles.find(profile => profile.id === definition.profile_id)
            : undefined
          const subtitle = trigger
            ? 'manual'
            : `${definition.output_kind}${agent ? ` · ${agent.name}` : ''}`
          return <g
            key={definition.id}
            className={`graph-node st-${status}${definition.id === selectedId ? ' selected' : ''}${trigger ? ' is-trigger' : ''}${editable ? ' draggable' : ''}`}
            transform={`translate(${position.x} ${position.y})`}
            role="button"
            tabIndex={0}
            aria-label={`${definition.name}, ${statusLabel(status)}`}
            onPointerDown={event => beginNodeDrag(event, definition.id)}
            onKeyDown={event => {
              if (event.key === 'Enter' || event.key === ' ') onSelect(definition.id)
            }}
          >
            <rect width={position.width} height={position.height} rx="12" />
            <circle className="graph-node-dot" cx="22" cy="24" r="6" />
            <text className="graph-node-name" x="38" y="29">
              {definition.name.length > 24 ? `${definition.name.slice(0, 23)}…` : definition.name}
            </text>
            <text className="graph-node-kind" x="22" y="55">{subtitle}</text>
            <text className="graph-node-status" x="22" y="76">
              {statusLabel(status)}{definition.review_required ? ' · review gate' : ''}
            </text>
            {editable && !trigger && <circle
              className="graph-handle graph-handle-in"
              cx="0"
              cy={position.height / 2}
              r={HANDLE_RADIUS}
            />}
            {editable && <circle
              className="graph-handle graph-handle-out"
              cx={position.width}
              cy={position.height / 2}
              r={HANDLE_RADIUS}
              onPointerDown={event => beginLink(event, definition.id)}
            />}
          </g>
        })}
      </g>
    </svg>
  </div>
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
}) {
  const [jobs, setJobs] = React.useState<GraphJob[]>([])
  const [templates, setTemplates] = React.useState<GraphTemplate[]>([])
  const [job, setJob] = React.useState<GraphJob | null>(null)
  const [selectedId, setSelectedId] = React.useState<string | null>(null)
  const [railOpen, setRailOpen] = React.useState(true)
  const [chatOpen, setChatOpen] = React.useState(false)
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

  React.useEffect(() => { void refreshList() }, [refreshList])

  React.useEffect(() => {
    if (!pendingJobId) return
    void loadJob(pendingJobId)
    onPendingConsumed?.()
  }, [pendingJobId, loadJob, onPendingConsumed])

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

  React.useEffect(() => {
    if (!job || !['running', 'review'].includes(job.status)) return
    const timer = window.setInterval(() => { void loadJob(job.id) }, 1500)
    return () => clearInterval(timer)
  }, [job?.id, job?.status, loadJob])

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
  async function newPlan() {
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
      setJobs(current => [created, ...current.filter(item => item.id !== created.id)])
      setNotice('New plan. Describe it in the chat, or build it on the canvas.')
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

  return <section className="graph-screen">
    {/* One bar, not two: the Advanced tab already says where you are, so the
        eyebrow and the never-changing subtitle were spending 91px to repeat it. */}
    <header className="graph-header">
      <button
        className="row-action graph-rail-toggle"
        onClick={() => setRailOpen(open => !open)}
        aria-label={railOpen ? 'Hide plan list' : 'Show plan list'}
        aria-expanded={railOpen}
      >☰</button>
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
        <span className={`graph-status st-${job.status}`}>{statusLabel(job.status)}</span>
        <span className="graph-node-count">{doneCount}/{job.node_states.length} nodes</span>
      </>}
      {dirty && <span className="graph-dirty">Unsaved edits</span>}
      <div className="graph-header-actions">
        {job?.status === 'queued' && <button
          className={`ghost-button${chatOpen ? ' active' : ''}`}
          onClick={() => setChatOpen(open => !open)}
          aria-pressed={chatOpen}
        >Chat</button>}
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

    <div className="graph-workspace">
      {/* Chat left, artifact right — the house idiom (Design Studio does the same),
          and the standing rule: the agent edits the plan on screen, never the DB. */}
      {chatOpen && job && plan && <aside className="graph-chat-panel">
        <AuthoringChat
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
          }, text)}
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
          idleHint="Describe the workflow and the agent draws the graph; ask for changes and it redraws it. Branches run at once. Separate from Code, scoped to this plan."
          placeholder="Describe or change the workflow…"
        />
      </aside>}
      {railOpen && <aside className="graph-job-list">
        <div className="graph-list-head first"><strong>Plans</strong><span className="graph-list-actions"><button className="row-action" onClick={() => void newPlan()} disabled={!!busy} aria-label="New plan">＋</button><button className="row-action" onClick={() => void refreshList()} aria-label="Refresh graph plans">↻</button></span></div>
        {jobs.length === 0
          ? <p className="muted graph-empty-list">No plans yet. Start one with ＋, or promote a chat.</p>
          : jobs.map(item => <div key={item.id} className={`graph-row-wrap${job?.id === item.id ? ' selected' : ''}`}>
              <button className="graph-job-row" onClick={() => void loadJob(item.id)}>
                <span>{item.title}</span><small>{statusLabel(item.status)}</small>
              </button>
              <button className="row-action danger graph-row-delete" title="Delete plan" aria-label={`Delete plan ${item.title}`} disabled={!!busy} onClick={() => void deletePlan(item)}>×</button>
            </div>)}
        <div className="graph-list-head"><strong>Templates</strong></div>
        {templates.length === 0
          ? <p className="muted graph-empty-list">No saved graph templates.</p>
          : templates.map(template => <div key={template.id} className="graph-row-wrap">
              <button
                className="graph-job-row"
                disabled={!!busy}
                onClick={() => {
                  // Ask for the declared inputs first: without them a node's {{var}}
                  // would reach the runner unfilled.
                  if (template.inputs?.length) setRunningTemplate(template)
                  else void createFromTemplate(template)
                }}
              >
                <span>{template.name}</span><small>New queued run</small>
              </button>
              <button className="row-action danger graph-row-delete" title="Delete template" aria-label={`Delete template ${template.name}`} disabled={!!busy} onClick={() => void deleteTemplate(template)}>×</button>
            </div>)}
      </aside>}

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
                <label>Instruction<textarea rows={5} value={definition.instruction} onChange={event => updateSelected({ instruction: event.target.value })} /></label>
                <label>Expected output<textarea
                  rows={2}
                  value={definition.expected_output ?? ''}
                  placeholder="What a good result looks like"
                  onChange={event => updateSelected({ expected_output: event.target.value })}
                /></label>
                <label>Rules <span className="muted">(optional)</span><textarea
                  rows={3}
                  value={definition.rules ?? ''}
                  placeholder="Constraints on how to do it"
                  onChange={event => updateSelected({ rules: event.target.value })}
                /></label>
                <label>Agent<select
                  value={definition.profile_id ?? ''}
                  onChange={event => updateSelected({
                    profile_id: event.target.value ? Number(event.target.value) : null,
                  })}
                >
                  <option value="">Default — this run’s agent</option>
                  {profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
                </select></label>
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

const INPUT_KINDS: WorkflowInput['kind'][] = ['text', 'url', 'number', 'file']
const slugifyId = (value: string) =>
  value.toLowerCase().trim().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')

// Saving a plan as a template is the moment its reusable contract is defined, so it is
// also where {{inputs}} are declared: a template is what a fresh run and a schedule are
// both built from, and each needs to know what to ask for.
function SaveTemplateModal({ title, initial, busy, onCancel, onSave }: {
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
