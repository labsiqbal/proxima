import { describe, expect, it } from 'vitest'
import {
  sourceFingerprint,
  splitMermaidSections,
  whiteboardPathFor,
} from './whiteboard'

describe('Mermaid whiteboard helpers', () => {
  it('extracts multiple Mermaid fences while preserving surrounding Markdown', () => {
    const sections = splitMermaidSections('# Plan\n\n```mermaid\ngraph LR\n A-->B\n```\n\nNotes\n\n```mermaid\nsequenceDiagram\n A->>B: Hi\n```')

    expect(sections).toEqual([
      { type: 'markdown', content: '# Plan\n\n' },
      { type: 'mermaid', content: 'graph LR\n A-->B', diagramIndex: 0 },
      { type: 'markdown', content: '\n\nNotes\n\n' },
      { type: 'mermaid', content: 'sequenceDiagram\n A->>B: Hi', diagramIndex: 1 },
    ])
  })

  it('uses a stable source path for saved whiteboards and fingerprints source changes', () => {
    expect(whiteboardPathFor('reports/System Flow.md', 1)).toMatch(/^artifacts\/whiteboards\/system-flow-[a-f0-9]{8}\.excalidraw$/)
    expect(whiteboardPathFor('reports/System Flow.md', 1)).toBe(whiteboardPathFor('reports/System Flow.md', 1))
    expect(sourceFingerprint('graph LR; A-->B')).not.toBe(sourceFingerprint('graph LR; A-->C'))
  })

  it('roots whiteboards in the mapped artifacts folder (prune #138)', () => {
    // The base is the project's real artifacts location from the layout map.
    expect(whiteboardPathFor('reports/System Flow.md', 1, 'ops/artifacts')).toMatch(
      /^ops\/artifacts\/whiteboards\/system-flow-[a-f0-9]{8}\.excalidraw$/,
    )
  })
})
