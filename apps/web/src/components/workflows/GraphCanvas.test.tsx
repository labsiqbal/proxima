import '@testing-library/jest-dom/vitest'
import React from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { GraphJob, WorkflowGraph } from '../../types'
import {
  captureCanvasIntent,
  GraphCanvas,
  refitGraphView,
  statusLabel,
} from './GraphCanvas'
import { layoutGraph } from '../../screens/graphLayout'

const chain = (count = 3): WorkflowGraph => {
  const nodes = Array.from({ length: count }, (_, index) => ({
    id: `node-${index + 1}`,
    name: `Node ${index + 1}`,
    instruction: `Do ${index + 1}`,
    output_kind: 'text' as const,
  }))
  return {
    nodes,
    edges: nodes.slice(1).map((node, index) => ({
      from: nodes[index].id,
      to: node.id,
    })),
  }
}

const job = {
  id: 1,
  title: 'Canvas refit plan',
  status: 'queued',
  node_states: [
    { id: 1, job_id: 1, node_id: 'node-1', status: 'pending' },
    { id: 2, job_id: 1, node_id: 'node-2', status: 'pending' },
    { id: 3, job_id: 1, node_id: 'node-3', status: 'pending' },
    { id: 4, job_id: 1, node_id: 'node-4', status: 'pending' },
  ],
} as GraphJob

let resizeCallback: ResizeObserverCallback | null = null
let nextFrame = 1
let frameQueue = new Map<number, FrameRequestCallback>()

function rect(width: number, height: number): DOMRect {
  return {
    x: 0,
    y: 0,
    width,
    height,
    top: 0,
    right: width,
    bottom: height,
    left: 0,
    toJSON: () => ({}),
  } as DOMRect
}

function flushFrames() {
  const queued = [...frameQueue.values()]
  frameQueue.clear()
  queued.forEach(callback => callback(0))
}

function triggerResize() {
  resizeCallback?.([], {} as ResizeObserver)
}

function CanvasHarness({ plan }: { plan: WorkflowGraph }) {
  const [selectedId, setSelectedId] = React.useState<string | null>(null)
  return <GraphCanvas
    job={job}
    plan={plan}
    profiles={[]}
    selectedId={selectedId}
    onSelect={setSelectedId}
    onDeselect={() => setSelectedId(null)}
    editable
    onMoveNode={vi.fn()}
    onConnect={vi.fn()}
    onDisconnect={vi.fn()}
    onAddNode={vi.fn()}
    onAddScript={vi.fn()}
    onAddTrigger={vi.fn()}
    hasTrigger={false}
  />
}

function DragCanvasHarness({ initialPlan }: { initialPlan: WorkflowGraph }) {
  const [plan, setPlan] = React.useState(initialPlan)
  const [selectedId, setSelectedId] = React.useState<string | null>(null)
  return <GraphCanvas
    job={job}
    plan={plan}
    profiles={[]}
    selectedId={selectedId}
    onSelect={setSelectedId}
    onDeselect={() => setSelectedId(null)}
    editable
    onMoveNode={(nodeId, x, y) => {
      setPlan(current => ({
        ...current,
        nodes: current.nodes.map(node => (
          node.id === nodeId ? { ...node, x, y } : node
        )),
      }))
    }}
    onConnect={vi.fn()}
    onDisconnect={vi.fn()}
    onAddNode={vi.fn()}
    onAddScript={vi.fn()}
    onAddTrigger={vi.fn()}
    hasTrigger={false}
  />
}

function readTransform(canvas: Element): { x: number; y: number; k: number } {
  const raw = canvas.querySelector(':scope > g')?.getAttribute('transform') ?? ''
  const match = /translate\(([^ ]+) ([^)]+)\) scale\(([^)]+)\)/.exec(raw)
  if (!match) throw new Error(`unexpected transform: ${raw}`)
  return { x: Number(match[1]), y: Number(match[2]), k: Number(match[3]) }
}

describe('GraphCanvas refitting', () => {
  beforeEach(() => {
    resizeCallback = null
    nextFrame = 1
    frameQueue = new Map()
    vi.stubGlobal('ResizeObserver', class {
      constructor(callback: ResizeObserverCallback) {
        resizeCallback = callback
      }
      observe() {}
      disconnect() {}
      unobserve() {}
    })
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      const id = nextFrame++
      frameQueue.set(id, callback)
      return id
    })
    vi.stubGlobal('cancelAnimationFrame', (id: number) => {
      frameQueue.delete(id)
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('refits after the canvas viewport shrinks without losing selection or keyboard focus', () => {
    render(<CanvasHarness plan={chain()} />)
    const canvas = screen.getByLabelText('Canvas refit plan workflow graph')
    let canvasRect = rect(960, 480)
    vi.spyOn(canvas, 'getBoundingClientRect').mockImplementation(() => canvasRect)

    act(() => {
      triggerResize()
      flushFrames()
    })
    expect(canvas.querySelector(':scope > g')).toHaveAttribute(
      'transform',
      'translate(0 154) scale(1)',
    )

    const selected = screen.getByRole('button', { name: 'Node 1, Pending' })
    selected.focus()
    fireEvent.keyDown(selected, { key: 'Enter' })
    expect(selected).toHaveClass('selected')
    expect(selected).toHaveFocus()

    canvasRect = rect(480, 480)
    act(() => {
      triggerResize()
      flushFrames()
    })

    expect(canvas.querySelector(':scope > g')).toHaveAttribute(
      'transform',
      'translate(0 197) scale(0.5)',
    )
    expect(selected).toHaveClass('selected')
    expect(selected).toHaveFocus()
  })

  it('coalesces repeated panel resize observations and converges on the final size', () => {
    render(<CanvasHarness plan={chain()} />)
    const canvas = screen.getByLabelText('Canvas refit plan workflow graph')
    let canvasRect = rect(960, 480)
    vi.spyOn(canvas, 'getBoundingClientRect').mockImplementation(() => canvasRect)

    act(() => {
      triggerResize()
      flushFrames()
    })

    canvasRect = rect(840, 480)
    triggerResize()
    canvasRect = rect(700, 480)
    triggerResize()
    canvasRect = rect(480, 480)
    triggerResize()
    expect(frameQueue.size).toBe(1)

    act(() => flushFrames())
    expect(canvas.querySelector(':scope > g')).toHaveAttribute(
      'transform',
      'translate(0 197) scale(0.5)',
    )
  })

  it('defers geometry refits while a node is dragged and refits once on release', () => {
    const laidOut = layoutGraph(chain())
    const seed = {
      ...chain(),
      nodes: chain().nodes.map(node => {
        const position = laidOut.nodes.find(entry => entry.id === node.id)!
        return { ...node, x: position.x, y: position.y }
      }),
    }
    render(<DragCanvasHarness initialPlan={seed} />)
    const canvas = screen.getByLabelText('Canvas refit plan workflow graph')
    vi.spyOn(canvas, 'getBoundingClientRect').mockImplementation(() => rect(480, 480))

    act(() => {
      triggerResize()
      flushFrames()
    })
    const before = readTransform(canvas)
    expect(before.k).toBeCloseTo(0.5)

    const node = screen.getByRole('button', { name: 'Node 1, Pending' })
    const start = laidOut.nodes.find(entry => entry.id === 'node-1')!
    // Pointer at the node's top-left corner in screen space under the fitted view.
    const pointerX = before.x + start.x * before.k
    const pointerY = before.y + start.y * before.k

    // Drag the leftmost node further left so the layout bounds expand.
    const dragDx = -240
    act(() => {
      fireEvent.pointerDown(node, { button: 0, clientX: pointerX, clientY: pointerY })
    })
    act(() => {
      fireEvent.pointerMove(window, {
        clientX: pointerX + dragDx,
        clientY: pointerY,
      })
      flushFrames()
    })

    // Mid-drag geometry changes must not rewrite the live transform.
    expect(readTransform(canvas)).toEqual(before)
    expect(frameQueue.size).toBe(0)

    act(() => {
      fireEvent.pointerUp(window, {
        clientX: pointerX + dragDx,
        clientY: pointerY,
      })
    })
    expect(frameQueue.size).toBe(1)

    act(() => flushFrames())
    const after = readTransform(canvas)
    expect(after.k).toBeLessThan(before.k)
    const finalLayout = layoutGraph({
      ...seed,
      nodes: seed.nodes.map(node => (
        node.id === 'node-1'
          ? { ...node, x: start.x + Math.round(dragDx / before.k), y: start.y }
          : node
      )),
    })
    expect(after.x + finalLayout.x * after.k).toBeGreaterThanOrEqual(-0.001)
    expect(after.x + (finalLayout.x + finalLayout.width) * after.k)
      .toBeLessThanOrEqual(480.001)
  })

  it('keeps manual zoom continuous after a deep auto-fit below ZOOM_MIN', () => {
    render(<CanvasHarness plan={chain(4)} />)
    const canvas = screen.getByLabelText('Canvas refit plan workflow graph')
    // Narrow laptop-style viewport forces fitScale well below the manual ZOOM_MIN (0.35).
    vi.spyOn(canvas, 'getBoundingClientRect').mockImplementation(() => rect(200, 160))

    act(() => {
      triggerResize()
      flushFrames()
    })
    const fitted = readTransform(canvas)
    expect(fitted.k).toBeLessThan(0.35)

    fireEvent.click(screen.getByLabelText('Zoom in'))
    const zoomed = readTransform(canvas)
    expect(zoomed.k).toBeCloseTo(fitted.k * 1.25)
    expect(zoomed.k).toBeLessThan(0.35)
  })

  it('restores preferred zoom after pan while a panel constrains scale', () => {
    render(<CanvasHarness plan={chain()} />)
    const canvas = screen.getByLabelText('Canvas refit plan workflow graph')
    let canvasRect = rect(1440, 720)
    vi.spyOn(canvas, 'getBoundingClientRect').mockImplementation(() => canvasRect)

    act(() => {
      triggerResize()
      flushFrames()
    })
    // Wide desktop fits above 1; zoom out once so preferred k stays below fitK when space returns.
    expect(readTransform(canvas).k).toBeCloseTo(1.5)
    fireEvent.click(screen.getByLabelText('Zoom out'))
    const preferred = readTransform(canvas)
    expect(preferred.k).toBeCloseTo(1.2)

    // Panel open shrinks the canvas; display k is constrained, preferred k stays.
    canvasRect = rect(480, 420)
    act(() => {
      triggerResize()
      flushFrames()
    })
    const constrained = readTransform(canvas)
    expect(constrained.k).toBeCloseTo(0.5)

    // Pan while constrained must not clobber the preferred zoom of 1.2.
    act(() => {
      fireEvent.pointerDown(canvas, { button: 0, clientX: 200, clientY: 200 })
    })
    act(() => {
      fireEvent.pointerMove(window, { clientX: 260, clientY: 230 })
      fireEvent.pointerUp(window, { clientX: 260, clientY: 230 })
    })
    expect(readTransform(canvas).k).toBeCloseTo(0.5)

    // Space returns: preferred scale should restore, not the constrained display k.
    canvasRect = rect(1440, 720)
    act(() => {
      triggerResize()
      flushFrames()
    })
    expect(readTransform(canvas).k).toBeCloseTo(1.2)
  })

  it('ignores no-op zoom-out from a deep fit so later space can re-fit upward', () => {
    render(<CanvasHarness plan={chain(4)} />)
    const canvas = screen.getByLabelText('Canvas refit plan workflow graph')
    let canvasRect = rect(200, 160)
    vi.spyOn(canvas, 'getBoundingClientRect').mockImplementation(() => canvasRect)

    act(() => {
      triggerResize()
      flushFrames()
    })
    const deepFit = readTransform(canvas)
    expect(deepFit.k).toBeLessThan(0.35)

    // Zoom-out clamps to the deep-fit floor — must leave fit intent intact.
    fireEvent.click(screen.getByLabelText('Zoom out'))
    expect(readTransform(canvas).k).toBeCloseTo(deepFit.k)

    canvasRect = rect(960, 480)
    act(() => {
      triggerResize()
      flushFrames()
    })
    const restored = readTransform(canvas)
    expect(restored.k).toBeGreaterThan(deepFit.k)
    expect(restored.k).toBeCloseTo(0.7441860465116279)
  })

  it('refits when graph growth changes the layout bounds', () => {
    const { rerender } = render(<CanvasHarness plan={chain()} />)
    const canvas = screen.getByLabelText('Canvas refit plan workflow graph')
    vi.spyOn(canvas, 'getBoundingClientRect').mockImplementation(() => rect(960, 480))
    act(() => {
      triggerResize()
      flushFrames()
    })
    expect(canvas.querySelector(':scope > g')).toHaveAttribute(
      'transform',
      'translate(0 154) scale(1)',
    )

    rerender(<CanvasHarness plan={chain(4)} />)
    act(() => flushFrames())
    expect(canvas.querySelector(':scope > g')).toHaveAttribute(
      'transform',
      'translate(0 176) scale(0.7441860465116279)',
    )
  })

  it('preserves manual pan and zoom intent while constraining resized views to all nodes', () => {
    const layout = layoutGraph(chain())
    const intent = captureCanvasIntent(
      { x: 120, y: 90, k: 1.2 },
      { width: 1440, height: 720 },
    )

    const laptop = refitGraphView(
      { width: 480, height: 420 },
      layout,
      intent,
    )
    expect(laptop.k).toBe(0.5)
    expect(laptop.x + layout.x * laptop.k).toBeGreaterThanOrEqual(0)
    expect(laptop.x + (layout.x + layout.width) * laptop.k).toBeLessThanOrEqual(480)
    expect(laptop.y + layout.y * laptop.k).toBeGreaterThanOrEqual(0)
    expect(laptop.y + (layout.y + layout.height) * laptop.k).toBeLessThanOrEqual(420)

    const desktop = refitGraphView(
      { width: 1440, height: 720 },
      layout,
      intent,
    )
    expect(desktop.k).toBe(1.2)
    expect((720 - desktop.x) / desktop.k).toBeCloseTo(intent.focus.x)
    expect((360 - desktop.y) / desktop.k).toBeCloseTo(intent.focus.y)
  })

  it('refits without animation when reduced motion is requested', () => {
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
      matches: true,
      media: '(prefers-reduced-motion: reduce)',
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
    render(<CanvasHarness plan={chain()} />)
    const canvas = screen.getByLabelText('Canvas refit plan workflow graph')
    vi.spyOn(canvas, 'getBoundingClientRect').mockImplementation(() => rect(480, 480))
    act(() => {
      triggerResize()
      flushFrames()
    })
    expect(canvas.querySelector(':scope > g')).toHaveAttribute(
      'transform',
      'translate(0 197) scale(0.5)',
    )
    expect(getComputedStyle(canvas.querySelector(':scope > g')!).transitionDuration).toBe('0s')
  })
})

describe('statusLabel', () => {
  it('returns proper-cased single-word statuses for chips', () => {
    expect(statusLabel('pending')).toBe('Pending')
    expect(statusLabel('running')).toBe('Running')
    expect(statusLabel('review')).toBe('Review')
    expect(statusLabel('done')).toBe('Done')
    expect(statusLabel('failed')).toBe('Failed')
    expect(statusLabel('queued')).toBe('Queued')
    expect(statusLabel('cancelled')).toBe('Cancelled')
  })

  it('title-cases unknown underscore statuses in the fallback path', () => {
    expect(statusLabel('in_progress' as unknown as 'running')).toBe('In progress')
  })
})
