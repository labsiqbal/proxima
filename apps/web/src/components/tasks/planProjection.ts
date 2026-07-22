import type { GraphJob, GraphNodeDefinition, GraphNodeState, WorkflowGraph } from '../../types'

// The Tasks screen's view of a plan (T2): list view and graph view are two
// projections of the SAME object. This module is the pure half — which jobs a
// plan contains, in what order, and whether the plan branches (branch-less ⇒
// plain list; branching ⇒ the canvas is offered as a toggle).

export type PlanJobRow = {
  node: GraphNodeDefinition
  status: GraphNodeState['status']
  error: string | null
  // The node's validated output, for the compact result surfaces (a script
  // row shows its last output line, T6). Null while nothing has run.
  output: unknown
}

/**
 * The plan's jobs in execution order: a deterministic topological walk of the
 * agent nodes (triggers are entry points, not work, so they are not job rows).
 * Ties break on authoring order, matching the server's dispatch order.
 */
export function orderedPlanJobs(job: GraphJob): PlanJobRow[] {
  const graph = job.graph
  const position = new Map(graph.nodes.map((node, index) => [node.id, index]))
  const indegree = new Map(graph.nodes.map(node => [node.id, 0]))
  const downstream = new Map<string, string[]>(graph.nodes.map(node => [node.id, []]))
  for (const edge of graph.edges) {
    if (!indegree.has(edge.from) || !indegree.has(edge.to)) continue
    indegree.set(edge.to, (indegree.get(edge.to) ?? 0) + 1)
    downstream.get(edge.from)?.push(edge.to)
  }
  const ready = graph.nodes.filter(node => indegree.get(node.id) === 0).map(node => node.id)
  const byPosition = (left: string, right: string) => (position.get(left) ?? 0) - (position.get(right) ?? 0)
  ready.sort(byPosition)
  const ordered: string[] = []
  while (ready.length) {
    const id = ready.shift() as string
    ordered.push(id)
    const next: string[] = []
    for (const child of downstream.get(id) ?? []) {
      const left = (indegree.get(child) ?? 1) - 1
      indegree.set(child, left)
      if (left === 0) next.push(child)
    }
    next.sort(byPosition)
    // Newly-ready nodes merge in front-sorted so siblings keep authoring order.
    ready.push(...next)
    ready.sort(byPosition)
  }
  // A stored cycle cannot happen (the server rejects it), but never drop rows:
  // anything unordered appends in authoring order.
  for (const node of graph.nodes) if (!ordered.includes(node.id)) ordered.push(node.id)

  const states = new Map(job.node_states.map(state => [state.node_id, state]))
  return ordered
    .map(id => graph.nodes.find(node => node.id === id))
    .filter((node): node is GraphNodeDefinition => !!node && node.type !== 'trigger')
    .map(node => ({
      node,
      status: states.get(node.id)?.status ?? 'pending',
      error: (states.get(node.id)?.error as string | null) ?? null,
      output: states.get(node.id)?.output ?? null,
    }))
}

/** The last non-empty line of a node's output — the at-a-glance result a script
 *  step shows on its card and list row (T6). Text output only; JSON is a
 *  structure, not a line. */
export function lastOutputLine(output: unknown): string | null {
  if (typeof output !== 'string') return null
  const lines = output.split('\n').map(line => line.trim()).filter(Boolean)
  return lines.length ? lines[lines.length - 1] : null
}

/**
 * A plan branches when its dependency structure is more than one straight
 * line: some node fans out or fans in. Branch-less plans read perfectly as a
 * list; only branching ones earn the canvas toggle.
 */
export function planBranches(graph: WorkflowGraph): boolean {
  const incoming = new Map<string, number>()
  const outgoing = new Map<string, number>()
  for (const edge of graph.edges) {
    incoming.set(edge.to, (incoming.get(edge.to) ?? 0) + 1)
    outgoing.set(edge.from, (outgoing.get(edge.from) ?? 0) + 1)
  }
  return graph.nodes.some(node =>
    (incoming.get(node.id) ?? 0) > 1 || (outgoing.get(node.id) ?? 0) > 1)
}

/** The compact target badge text for a job row; null when the job is unbound. */
export function targetBadge(node: GraphNodeDefinition): string | null {
  if (node.target_ambiguous) return 'where?'
  if (!node.target) return null
  if (node.target === 'ops') return 'ops'
  return node.target === '.' ? 'repo' : node.target
}

/** How many of the plan's jobs are done, as "done/total". */
export function planProgress(job: GraphJob): string {
  const jobs = job.graph.nodes.filter(node => node.type !== 'trigger')
  const states = new Map(job.node_states.map(state => [state.node_id, state.status]))
  const done = jobs.filter(node => states.get(node.id) === 'done').length
  return `${done}/${jobs.length}`
}
