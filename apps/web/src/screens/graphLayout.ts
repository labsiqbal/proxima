import type { WorkflowGraph } from '../types'

export type PositionedGraphNode = {
  id: string
  x: number
  y: number
  width: number
  height: number
}

export type GraphLayout = {
  nodes: PositionedGraphNode[]
  width: number
  height: number
}

const NODE_WIDTH = 220
const NODE_HEIGHT = 92
const COLUMN_GAP = 110
const ROW_GAP = 42
const PADDING = 40

/** Deterministic topological layering for the dependency canvas. */
export function layoutGraph(graph: WorkflowGraph): GraphLayout {
  const ids = graph.nodes.map(node => node.id)
  const incoming = new Map(ids.map(id => [id, [] as string[]]))
  const outgoing = new Map(ids.map(id => [id, [] as string[]]))
  for (const edge of graph.edges) {
    incoming.get(edge.to)?.push(edge.from)
    outgoing.get(edge.from)?.push(edge.to)
  }

  const indegree = new Map(ids.map(id => [id, incoming.get(id)?.length ?? 0]))
  const ready = ids
    .filter(id => indegree.get(id) === 0)
    .sort((left, right) => left.localeCompare(right))
  const layer = new Map<string, number>()
  let cursor = 0
  while (cursor < ready.length) {
    const id = ready[cursor++]
    const parentLayer = layer.get(id) ?? 0
    for (const child of [...(outgoing.get(id) ?? [])].sort((left, right) => left.localeCompare(right))) {
      layer.set(child, Math.max(layer.get(child) ?? 0, parentLayer + 1))
      const next = (indegree.get(child) ?? 1) - 1
      indegree.set(child, next)
      if (next === 0) ready.push(child)
    }
  }
  for (const id of ids) if (!layer.has(id)) layer.set(id, 0)

  const columns = new Map<number, string[]>()
  for (const id of ids) {
    const index = layer.get(id) ?? 0
    const column = columns.get(index) ?? []
    column.push(id)
    columns.set(index, column)
  }
  for (const column of columns.values()) column.sort((left, right) => left.localeCompare(right))

  const maxLayer = Math.max(0, ...columns.keys())
  const maxRows = Math.max(1, ...Array.from(columns.values(), column => column.length))
  const nodes: PositionedGraphNode[] = []
  for (let columnIndex = 0; columnIndex <= maxLayer; columnIndex += 1) {
    const column = columns.get(columnIndex) ?? []
    const columnHeight = column.length * NODE_HEIGHT + Math.max(0, column.length - 1) * ROW_GAP
    const fullHeight = maxRows * NODE_HEIGHT + Math.max(0, maxRows - 1) * ROW_GAP
    const offset = (fullHeight - columnHeight) / 2
    column.forEach((id, rowIndex) => {
      nodes.push({
        id,
        x: PADDING + columnIndex * (NODE_WIDTH + COLUMN_GAP),
        y: PADDING + offset + rowIndex * (NODE_HEIGHT + ROW_GAP),
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
      })
    })
  }

  return {
    nodes,
    width: PADDING * 2 + (maxLayer + 1) * NODE_WIDTH + maxLayer * COLUMN_GAP,
    height: PADDING * 2 + maxRows * NODE_HEIGHT + Math.max(0, maxRows - 1) * ROW_GAP,
  }
}
