import { describe, expect, it } from 'vitest'
import type { WorkflowGraph } from '../types'
import { layoutGraph } from './graphLayout'

const diamond: WorkflowGraph = {
  nodes: [
    { id: 'a', name: 'A', instruction: '', output_kind: 'text' },
    { id: 'b', name: 'B', instruction: '', output_kind: 'text' },
    { id: 'c', name: 'C', instruction: '', output_kind: 'text' },
    { id: 'd', name: 'D', instruction: '', output_kind: 'text' },
  ],
  edges: [
    { from: 'a', to: 'b' },
    { from: 'a', to: 'c' },
    { from: 'b', to: 'd' },
    { from: 'c', to: 'd' },
  ],
}

describe('layoutGraph', () => {
  it('places a diamond in deterministic topological columns', () => {
    const layout = layoutGraph(diamond)
    const positions = new Map(layout.nodes.map(node => [node.id, node]))

    expect(positions.get('a')?.x).toBeLessThan(positions.get('b')?.x ?? 0)
    expect(positions.get('b')?.x).toBe(positions.get('c')?.x)
    expect(positions.get('d')?.x).toBeGreaterThan(positions.get('b')?.x ?? 0)
    expect(positions.get('b')?.y).toBeLessThan(positions.get('c')?.y ?? 0)
  })

  it('keeps a hand-placed node where it was dropped and still auto-places the rest', () => {
    const dragged: WorkflowGraph = {
      ...diamond,
      nodes: diamond.nodes.map(node => node.id === 'd' ? { ...node, x: 900, y: 640 } : node),
    }

    const layout = layoutGraph(dragged)
    const positions = new Map(layout.nodes.map(node => [node.id, node]))

    expect(positions.get('d')).toMatchObject({ x: 900, y: 640 })
    // The nodes nobody moved keep their deterministic columns.
    expect(positions.get('b')?.x).toBe(layoutGraph(diamond).nodes.find(n => n.id === 'b')?.x)
    // The box grows to contain the dropped node instead of clipping it.
    expect(layout.x + layout.width).toBeGreaterThan(900 + 220)
    expect(layout.y + layout.height).toBeGreaterThan(640 + 92)
  })

  it('reports a box that contains nodes placed above and left of the origin', () => {
    const offCanvas: WorkflowGraph = {
      ...diamond,
      nodes: diamond.nodes.map(node => node.id === 'a' ? { ...node, x: -290, y: -60 } : node),
    }

    const layout = layoutGraph(offCanvas)

    // Negative coordinates are ordinary on an infinite canvas; a box anchored at
    // zero would leave this node unreachable and unframeable.
    expect(layout.x).toBeLessThanOrEqual(-290)
    expect(layout.y).toBeLessThanOrEqual(-60)
    expect(layout.x + layout.width).toBeGreaterThan(660)
  })
})
