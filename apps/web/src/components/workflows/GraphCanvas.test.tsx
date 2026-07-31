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
    expect(frameQueue).toHaveLength(1)

    act(() => flushFrames())
    expect(canvas.querySelector(':scope > g')).toHaveAttribute(
      'transform',
      'translate(0 197) scale(0.5)',
    )
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
