import { describe, expect, it } from 'vitest'
import { lastOutputLine, orderedPlanJobs, planBranches, planProgress, targetBadge } from './planProjection'
import type { GraphJob, GraphNodeDefinition, WorkflowGraph } from '../../types'

const node = (id: string, extra: Partial<GraphNodeDefinition> = {}): GraphNodeDefinition => ({
  id,
  type: 'agent',
  name: id.toUpperCase(),
  instruction: `do ${id}`,
  output_kind: 'text',
  ...extra,
})

const job = (graph: WorkflowGraph, statuses: Record<string, string> = {}): GraphJob => ({
  id: 1,
  session_id: 1,
  title: 'Plan',
  status: 'running',
  engine: 'graph',
  graph,
  node_states: Object.entries(statuses).map(([node_id, status], index) => ({
    id: index + 1,
    job_id: 1,
    node_id,
    status: status as GraphJob['node_states'][number]['status'],
    output_kind: 'text',
    version: 1,
  })),
})

describe('orderedPlanJobs', () => {
  it('lists jobs in dependency order and joins their live status', () => {
    const plan = job(
      {
        // Authored out of order on purpose: dependencies, not authoring, decide.
        nodes: [node('write'), node('research'), node('review')],
        edges: [
          { from: 'research', to: 'write' },
          { from: 'write', to: 'review' },
        ],
      },
      { research: 'done', write: 'running' },
    )

    const rows = orderedPlanJobs(plan)

    expect(rows.map(row => row.node.id)).toEqual(['research', 'write', 'review'])
    expect(rows.map(row => row.status)).toEqual(['done', 'running', 'pending'])
  })

  it('excludes the trigger — an entry point is not a job', () => {
    const plan = job({
      nodes: [node('go', { type: 'trigger' }), node('work')],
      edges: [{ from: 'go', to: 'work' }],
    })

    expect(orderedPlanJobs(plan).map(row => row.node.id)).toEqual(['work'])
  })

  it('keeps authoring order between independent siblings', () => {
    const plan = job({
      nodes: [node('root'), node('beta'), node('alpha')],
      edges: [
        { from: 'root', to: 'beta' },
        { from: 'root', to: 'alpha' },
      ],
    })

    expect(orderedPlanJobs(plan).map(row => row.node.id)).toEqual(['root', 'beta', 'alpha'])
  })
})

describe('planBranches', () => {
  it('is false for a straight chain — the list projection suffices', () => {
    expect(planBranches({
      nodes: [node('a'), node('b'), node('c')],
      edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'c' }],
    })).toBe(false)
  })

  it('is true on fan-out and fan-in — the canvas earns its toggle', () => {
    expect(planBranches({
      nodes: [node('a'), node('b'), node('c')],
      edges: [{ from: 'a', to: 'b' }, { from: 'a', to: 'c' }],
    })).toBe(true)
    expect(planBranches({
      nodes: [node('a'), node('b'), node('c')],
      edges: [{ from: 'a', to: 'c' }, { from: 'b', to: 'c' }],
    })).toBe(true)
  })
})

describe('targetBadge', () => {
  it('renders the T1 binding compactly and surfaces open questions', () => {
    expect(targetBadge(node('a', { target: 'ops' }))).toBe('ops')
    expect(targetBadge(node('a', { target: '.', touches_repo: true }))).toBe('repo')
    expect(targetBadge(node('a', { target: 'apps/web', touches_repo: true }))).toBe('apps/web')
    expect(targetBadge(node('a', { target_ambiguous: true }))).toBe('where?')
    expect(targetBadge(node('a'))).toBeNull()
  })
})

describe('planProgress', () => {
  it('counts jobs, not triggers', () => {
    const plan = job(
      {
        nodes: [node('go', { type: 'trigger' }), node('a'), node('b')],
        edges: [{ from: 'go', to: 'a' }, { from: 'a', to: 'b' }],
      },
      { a: 'done' },
    )
    expect(planProgress(plan)).toBe('1/2')
  })
})

describe('script rows (T6)', () => {
  it('includes script nodes as job rows with their output for the result line', () => {
    const plan = job(
      {
        nodes: [node('collect'), node('count', { type: 'script', command: 'count.py', instruction: '' })],
        edges: [{ from: 'collect', to: 'count' }],
      },
      { collect: 'done', count: 'done' },
    )
    plan.node_states[1].output = 'lines 1\nfinal: 42\n'

    const rows = orderedPlanJobs(plan)
    expect(rows.map(row => row.node.id)).toEqual(['collect', 'count'])
    expect(rows[1].node.type).toBe('script')
    expect(rows[1].output).toBe('lines 1\nfinal: 42\n')
  })

  it('scripts have no target badge — they run at the container root', () => {
    expect(targetBadge(node('s', { type: 'script', command: 'x.sh' }))).toBeNull()
  })
})

describe('lastOutputLine', () => {
  it('returns the last non-empty line of text output only', () => {
    expect(lastOutputLine('a\nb\n\n')).toBe('b')
    expect(lastOutputLine('   ')).toBeNull()
    expect(lastOutputLine({ not: 'text' })).toBeNull()
    expect(lastOutputLine(null)).toBeNull()
  })
})
