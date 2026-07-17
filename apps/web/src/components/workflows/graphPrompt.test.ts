import { describe, expect, it } from 'vitest'
import { buildGraphPrompt, parseGraphDraft, stripGraphBlock, type GraphSnapshot } from './graphPrompt'

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
