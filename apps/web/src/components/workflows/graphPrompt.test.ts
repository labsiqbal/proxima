import { describe, expect, it } from 'vitest'
import { buildGraphPrompt, buildNodeTestPrompt, parseGraphDraft, stripGraphBlock, testChainFor, type GraphSnapshot } from './graphPrompt'

const snapshot: GraphSnapshot = {
  name: 'Repurpose',
  description: '',
  category: 'content',
  inputs: [{ id: 'brief', label: 'Brief', kind: 'text', required: true }],
  graph: {
    nodes: [{ id: 'research', type: 'agent', name: 'Research', instruction: 'Collect facts', output_kind: 'text' }],
    edges: [],
  },
}

const reply = (body: string) => `Added two parallel posts.\n<workflow-graph>\n${body}\n</workflow-graph>`

describe('buildGraphPrompt', () => {
  it('sends the live graph and asks for a complete one back', () => {
    const prompt = buildGraphPrompt(snapshot, 'add a LinkedIn post')

    expect(prompt).toContain('WORKFLOW GRAPH AUTHORING')
    expect(prompt).toContain('add a LinkedIn post')
    expect(prompt).toContain(JSON.stringify(snapshot))
    expect(prompt).toContain('<workflow-graph>')
    // The whole reason to leave a list behind.
    expect(prompt).toContain('AT THE SAME TIME')
  })
})

describe('parseGraphDraft', () => {
  it('parses nodes, edges and declared inputs', () => {
    const patch = parseGraphDraft(reply(JSON.stringify({
      name: 'Repurpose',
      inputs: [{ id: 'brief', label: 'Brief', kind: 'text', required: true }],
      graph: {
        nodes: [
          { id: 'research', name: 'Research', instruction: 'Collect facts', expected_output: 'Five bullets', rules: 'Cite each' },
          { id: 'post', name: 'Post', instruction: 'Write it', review_required: true, output_kind: 'json' },
        ],
        edges: [{ from: 'research', to: 'post' }],
      },
    })))

    expect(patch?.name).toBe('Repurpose')
    expect(patch?.inputs).toEqual([{ id: 'brief', label: 'Brief', kind: 'text', required: true }])
    expect(patch?.graph?.nodes).toHaveLength(2)
    expect(patch?.graph?.nodes[0]).toMatchObject({ expected_output: 'Five bullets', rules: 'Cite each', type: 'agent' })
    expect(patch?.graph?.nodes[1]).toMatchObject({ review_required: true, output_kind: 'json' })
    expect(patch?.graph?.edges).toEqual([{ from: 'research', to: 'post' }])
  })

  it('drops what the server would reject rather than losing the whole reply', () => {
    const patch = parseGraphDraft(reply(JSON.stringify({
      graph: {
        nodes: [
          { id: 'a', name: 'A', instruction: 'Do it' },
          { id: 'a', name: 'Dupe', instruction: 'Again' },   // duplicate id
          { id: '', name: 'Nameless', instruction: 'Hi' },   // no id
          { id: 'b', name: 'B' },                            // agent with no instruction
        ],
        edges: [
          { from: 'a', to: 'a' },        // self-edge
          { from: 'a', to: 'ghost' },    // dangling
          { from: 'a', to: 'a' },
        ],
      },
    })))

    expect(patch?.graph?.nodes.map(n => n.id)).toEqual(['a'])
    expect(patch?.graph?.edges).toEqual([])
  })

  it('normalises a trigger and accepts source/target edge aliases', () => {
    const patch = parseGraphDraft(reply(JSON.stringify({
      graph: {
        nodes: [
          { id: 'start', type: 'trigger', name: 'When I run it', instruction: 'ignored', output_kind: 'text' },
          { id: 'work', name: 'Work', instruction: 'Do it' },
        ],
        edges: [{ source: 'start', target: 'work' }],
      },
    })))

    expect(patch?.graph?.nodes[0]).toMatchObject({ type: 'trigger', trigger_kind: 'manual', output_kind: 'json', instruction: '' })
    expect(patch?.graph?.edges).toEqual([{ from: 'start', to: 'work' }])
  })

  it('accepts a bare {nodes,edges} as well as {graph:{...}}', () => {
    const patch = parseGraphDraft(reply(JSON.stringify({
      nodes: [{ id: 'a', name: 'A', instruction: 'Do it' }],
      edges: [],
    })))

    expect(patch?.graph?.nodes.map(n => n.id)).toEqual(['a'])
  })

  it('leaves the canvas alone for an ordinary reply', () => {
    expect(parseGraphDraft('Sure — what should the first step do?')).toBeNull()
    expect(parseGraphDraft(reply(JSON.stringify({ graph: { nodes: [], edges: [] } })))).toBeNull()
    expect(parseGraphDraft(reply('not json at all'))).toBeNull()
  })
})

describe('stripGraphBlock', () => {
  it('keeps the summary and drops the JSON', () => {
    const text = stripGraphBlock(reply(JSON.stringify({ graph: { nodes: [], edges: [] } })))

    expect(text).toBe('Added two parallel posts.')
    expect(text).not.toContain('nodes')
  })

  it('never renders an empty bubble when the reply is only the block', () => {
    expect(stripGraphBlock('<workflow-graph>{"nodes":[]}</workflow-graph>')).toBe('Updated the graph.')
  })
})

describe('testChainFor', () => {
  const diamond: GraphSnapshot['graph'] = {
    nodes: [
      { id: 'start', type: 'trigger', name: 'Start', instruction: '', output_kind: 'json' },
      { id: 'riset', type: 'agent', name: 'Riset', instruction: 'r', output_kind: 'text' },
      { id: 'x', type: 'agent', name: 'Post X', instruction: 'x', output_kind: 'text' },
      { id: 'li', type: 'agent', name: 'Post LI', instruction: 'l', output_kind: 'text' },
      { id: 'bundle', type: 'agent', name: 'Bundle', instruction: 'b', output_kind: 'text' },
    ],
    edges: [
      { from: 'start', to: 'riset' },
      { from: 'riset', to: 'x' }, { from: 'riset', to: 'li' },
      { from: 'x', to: 'bundle' }, { from: 'li', to: 'bundle' },
    ],
  }

  it('collects only the ancestors of the node under test, in dependency order', () => {
    const chain = testChainFor(diamond, 'x').map(node => node.id)

    expect(chain).toEqual(['riset', 'x'])   // no li, no bundle, no trigger
  })

  it('runs the whole upstream diamond for the join node', () => {
    const chain = testChainFor(diamond, 'bundle').map(node => node.id)

    expect(chain[chain.length - 1]).toBe('bundle')
    expect(chain.indexOf('riset')).toBeLessThan(chain.indexOf('x'))
    expect(chain.indexOf('riset')).toBeLessThan(chain.indexOf('li'))
    expect(chain).toHaveLength(4)
  })

  it('is just the node itself when nothing feeds it', () => {
    expect(testChainFor(diamond, 'riset').map(node => node.id)).toEqual(['riset'])
  })
})

describe('buildNodeTestPrompt', () => {
  it('marks the node under test and fills known input values', () => {
    const prompt = buildNodeTestPrompt(snapshot, 'research', { brief: 'Launch plan' })

    expect(prompt).toContain('WORKFLOW TEST RUN')
    expect(prompt).toContain('← the node under test')
    expect(prompt).toContain('{{brief}} = Launch plan')
    expect(prompt).not.toContain('<workflow-graph>')  // a test reply must never redraw the canvas
    // A rehearsal must not leave real deliverables behind.
    expect(prompt).toContain('Do NOT write or modify project files')
    // Testing the join node must not re-pay for upstream nodes tested moments ago.
    expect(prompt).toContain('REUSE earlier rehearsals')
  })

  it('asks for sample values when no input is known', () => {
    const prompt = buildNodeTestPrompt(snapshot, 'research')

    expect(prompt).toContain('sensible sample values')
  })
})
