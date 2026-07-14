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
})
