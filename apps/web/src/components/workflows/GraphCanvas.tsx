import React from 'react'
import type { GraphJob, GraphNodeState, Profile, WorkflowGraph } from '../../types'
import { lastOutputLine } from '../tasks/planProjection'
import { layoutGraph } from '../../screens/graphLayout'
import type { GraphLayout } from '../../screens/graphLayout'

// The one dependency canvas (extracted from GraphScreen for slice 3): the
// Workflows editor renders it editable, and the Tasks screen reuses it as the
// read-only "graph projection" of a branching plan — two projections, one
// object, one canvas.

export function stateFor(job: GraphJob, nodeId: string): GraphNodeState | undefined {
  return job.node_states.find(state => state.node_id === nodeId)
}

/** Human label for a job/node status chip. Proper-cased so CSS need not
 *  capitalize every word (which mangled multi-word plan labels like
 *  "Ready to approve" into "Ready To Approve"). */
export function statusLabel(status: GraphJob['status'] | GraphNodeState['status']): string {
  switch (status) {
    case 'pending': return 'Pending'
    case 'ready': return 'Ready'
    case 'queued': return 'Queued'
    case 'running': return 'Running'
    case 'review': return 'Review'
    case 'done': return 'Done'
    case 'failed': return 'Failed'
    case 'cancelled': return 'Cancelled'
    case 'stale': return 'Stale'
    default: {
      const raw = String(status).replaceAll('_', ' ')
      return raw ? raw.charAt(0).toUpperCase() + raw.slice(1) : raw
    }
  }
}

const ZOOM_MIN = 0.35
const ZOOM_MAX = 2.5
const FIT_ZOOM_MIN = 0.01
const HANDLE_RADIUS = 6

export type CanvasView = { x: number; y: number; k: number }
type Point = { x: number; y: number }
type Viewport = { width: number; height: number }
export type CanvasIntent =
  | { mode: 'fit' }
  | { mode: 'manual'; k: number; focus: Point }

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

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value))
}

function fitScale(viewport: Viewport, layout: GraphLayout): number {
  if (!viewport.width || !viewport.height || !layout.width || !layout.height) {
    return 1
  }
  return clamp(
    Math.min(viewport.width / layout.width, viewport.height / layout.height),
    FIT_ZOOM_MIN,
    ZOOM_MAX,
  )
}

function centeredFit(viewport: Viewport, layout: GraphLayout, k: number): CanvasView {
  return {
    k,
    x: (viewport.width - layout.width * k) / 2 - layout.x * k,
    y: (viewport.height - layout.height * k) / 2 - layout.y * k,
  }
}

/**
 * Remember the graph point under the viewport centre, plus the owner's preferred
 * scale. Automatic refits may temporarily constrain either one to keep every node
 * visible, but the stored intent is unchanged and returns when space allows.
 */
export function captureCanvasIntent(view: CanvasView, viewport: Viewport): Extract<CanvasIntent, { mode: 'manual' }> {
  return {
    mode: 'manual',
    k: view.k,
    focus: {
      x: (viewport.width / 2 - view.x) / view.k,
      y: (viewport.height / 2 - view.y) / view.k,
    },
  }
}

/**
 * Derive the transform for a resized canvas. Fit mode frames the whole graph.
 * Manual mode preserves the owner's focal point and preferred zoom whenever the
 * new viewport permits it, constraining only as much as full visibility requires.
 */
export function refitGraphView(
  viewport: Viewport,
  layout: GraphLayout,
  intent: CanvasIntent,
): CanvasView {
  const fitK = fitScale(viewport, layout)
  if (intent.mode === 'fit') return centeredFit(viewport, layout, fitK)

  const k = Math.min(intent.k, fitK)
  const preferredX = viewport.width / 2 - intent.focus.x * k
  const preferredY = viewport.height / 2 - intent.focus.y * k
  const minX = -layout.x * k
  const maxX = viewport.width - (layout.x + layout.width) * k
  const minY = -layout.y * k
  const maxY = viewport.height - (layout.y + layout.height) * k

  return {
    k,
    x: clamp(preferredX, Math.min(minX, maxX), Math.max(minX, maxX)),
    y: clamp(preferredY, Math.min(minY, maxY), Math.max(minY, maxY)),
  }
}

function sameView(left: CanvasView, right: CanvasView): boolean {
  const epsilon = 0.001
  return Math.abs(left.x - right.x) < epsilon
    && Math.abs(left.y - right.y) < epsilon
    && Math.abs(left.k - right.k) < epsilon
}

export function GraphCanvas({ job, plan, profiles, selectedId, onSelect, onDeselect, editable, onMoveNode, onConnect, onDisconnect, onAddNode, onAddScript, onAddTrigger, hasTrigger }: {
  job: GraphJob
  plan: WorkflowGraph
  profiles: Profile[]
  selectedId: string | null
  onSelect: (nodeId: string) => void
  /** Fired when the background is clicked — closing the inspector by clicking away. */
  onDeselect: () => void
  editable: boolean
  onMoveNode: (nodeId: string, x: number, y: number) => void
  onConnect: (from: string, to: string) => void
  onDisconnect: (from: string, to: string) => void
  onAddNode: () => void
  onAddScript: () => void
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
  const intentRef = React.useRef<CanvasIntent>({ mode: 'fit' })
  const refitFrame = React.useRef(0)
  const gestureRef = React.useRef<Gesture>(null)
  gestureRef.current = gesture
  const geometryKey = React.useMemo(
    () => layout.nodes
      .map(node => `${node.id}:${node.x}:${node.y}:${node.width}:${node.height}`)
      .join('|'),
    [layout.nodes],
  )

  // A gesture reads the newest layout/view/callbacks without re-subscribing its
  // window listeners on every pointermove.
  const live = React.useRef({ view, layout, positions, plan, onMoveNode, onConnect })
  live.current = { view, layout, positions, plan, onMoveNode, onConnect }

  const viewport = React.useCallback((): Viewport | null => {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect || rect.width <= 0 || rect.height <= 0) return null
    return { width: rect.width, height: rect.height }
  }, [])

  const rememberManualView = React.useCallback((next: CanvasView) => {
    const size = viewport()
    if (size) intentRef.current = captureCanvasIntent(next, size)
  }, [viewport])

  const applyRefit = React.useCallback(() => {
    const size = viewport()
    const box = live.current.layout
    if (!size || !box.nodes.length || !box.width || !box.height) return
    const next = refitGraphView(size, box, intentRef.current)
    setView(current => sameView(current, next) ? current : next)
  }, [viewport])

  const scheduleRefit = React.useCallback(() => {
    if (gestureRef.current?.kind === 'node') return
    if (refitFrame.current) return
    refitFrame.current = requestAnimationFrame(() => {
      refitFrame.current = 0
      if (gestureRef.current?.kind === 'node') return
      applyRefit()
    })
  }, [applyRefit])

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
      const zoomMin = Math.min(ZOOM_MIN, current.k)
      const k = Math.min(ZOOM_MAX, Math.max(zoomMin, nextK))
      const rect = svgRef.current?.getBoundingClientRect()
      const at = focus ?? { x: (rect?.width ?? 0) / 2, y: (rect?.height ?? 0) / 2 }
      // Keep whatever sits under `at` pinned there while the scale changes.
      const next = {
        k,
        x: at.x - (at.x - current.x) * (k / current.k),
        y: at.y - (at.y - current.y) * (k / current.k),
      }
      rememberManualView(next)
      return next
    })
  }, [rememberManualView])

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
        const zoomMin = Math.min(ZOOM_MIN, current.k)
        const k = Math.min(ZOOM_MAX, Math.max(zoomMin, current.k * Math.exp(-event.deltaY * 0.0015)))
        const next = {
          k,
          x: focus.x - (focus.x - current.x) * (k / current.k),
          y: focus.y - (focus.y - current.y) * (k / current.k),
        }
        rememberManualView(next)
        return next
      })
    }
    element.addEventListener('wheel', onWheel, { passive: false })
    return () => element.removeEventListener('wheel', onWheel)
  }, [rememberManualView])

  const fit = React.useCallback(() => {
    intentRef.current = { mode: 'fit' }
    applyRefit()
  }, [applyRefit])

  // The canvas viewport changes when Plan Chat, workflow metadata, the inspector,
  // or the browser itself changes size. Observe the actual SVG rather than trying
  // to mirror every parent-panel state here. Resize bursts are coalesced to one
  // transform per animation frame, and graph geometry changes use the same path.
  React.useLayoutEffect(() => {
    const element = svgRef.current
    if (!element) return
    const observer = typeof ResizeObserver === 'undefined'
      ? null
      : new ResizeObserver(scheduleRefit)
    observer?.observe(element)
    window.addEventListener('resize', scheduleRefit)
    scheduleRefit()
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', scheduleRefit)
      if (refitFrame.current) cancelAnimationFrame(refitFrame.current)
      refitFrame.current = 0
    }
  }, [scheduleRefit])

  React.useLayoutEffect(() => {
    scheduleRefit()
  }, [geometryKey, layout.x, layout.y, layout.width, layout.height, scheduleRefit])

  // Listening on the window rather than capturing the pointer means a gesture
  // still completes when the pointer is released outside the canvas.
  React.useEffect(() => {
    if (!gesture) return
    const onMove = (event: PointerEvent) => {
      if (gesture.kind === 'pan') {
        setView(current => {
          const next = {
            ...current,
            x: gesture.origin.x + (event.clientX - gesture.from.x),
            y: gesture.origin.y + (event.clientY - gesture.from.y),
          }
          rememberManualView(next)
          return next
        })
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
      const endedNodeDrag = gesture.kind === 'node'
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
      if (endedNodeDrag) {
        gestureRef.current = null
        scheduleRefit()
      }
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
    }
  }, [gesture, toGraphPoint, rememberManualView, scheduleRefit])

  function beginPan(event: React.PointerEvent<SVGSVGElement>) {
    if (event.button !== 0) return
    setSelectedEdge(null)
    onDeselect()
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
      <button className="ghost-button" onClick={onAddScript} title="A step that runs a saved script from scripts/ — no AI involved">+ Script</button>
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
          const script = definition.type === 'script'
          const agent = definition.profile_id
            ? profiles.find(profile => profile.id === definition.profile_id)
            : undefined
          // The job's work binding rides on the card: where it runs (target) is
          // as load-bearing as what it emits (output kind).
          const binding = definition.target_ambiguous
            ? ' · where?'
            : definition.target
              ? ` · ${definition.target === 'ops' ? 'ops' : definition.target === '.' ? 'repo' : `repo ${definition.target}`}`
              : ''
          // A script card leads with what it runs — the command is its identity
          // the way the instruction is an agent node's.
          const subtitle = trigger
            ? (definition.trigger_kind === 'scheduled' ? 'scheduled' : 'manual intake')
            : script
              ? `⚡ scripts/${definition.command ?? ''}`
              : `${definition.output_kind}${binding}${agent ? ` · ${agent.name}` : ''}`
          const scriptResult = script ? lastOutputLine(state?.output) : null
          return <g
            key={definition.id}
            className={`graph-node st-${status}${definition.id === selectedId ? ' selected' : ''}${trigger ? ' is-trigger' : ''}${script ? ' is-script' : ''}${editable ? ' draggable' : ''}`}
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
              {scriptResult ? ` · ${scriptResult.length > 26 ? `${scriptResult.slice(0, 25)}…` : scriptResult}` : ''}
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
