import { beforeEach, describe, expect, it } from 'vitest'
import {
  artifactReviewStorageKey,
  formatArtifactReviewDraft,
  loadArtifactReview,
  saveArtifactReview,
  sourceFingerprint,
  splitMermaidSections,
  whiteboardPathFor,
  type ArtifactReviewState,
} from './artifactReview'

const review: ArtifactReviewState = {
  annotations: [{
    id: 'pin-1',
    x: 0.245,
    y: 0.7,
    note: 'Move this label above the chart.',
    createdAt: '2026-07-24T10:00:00Z',
  }],
  generalNote: 'Keep the visual hierarchy, but simplify the copy.',
  whiteboardPaths: ['artifacts/whiteboards/flow-a1b2c3.excalidraw'],
}

beforeEach(() => window.localStorage.clear())

describe('artifact review state', () => {
  it('persists annotations per project and artifact', () => {
    saveArtifactReview('alpha', 'artifacts/report.md', review)

    expect(loadArtifactReview('alpha', 'artifacts/report.md')).toEqual(review)
    expect(loadArtifactReview('beta', 'artifacts/report.md').annotations).toEqual([])
    expect(window.localStorage.getItem(artifactReviewStorageKey('alpha', 'artifacts/report.md'))).toContain('Move this label')
  })

  it('formats actionable chat feedback with pin positions and whiteboard paths', () => {
    const draft = formatArtifactReviewDraft({
      title: 'Quarterly report',
      path: 'artifacts/report.md',
      review,
    })

    expect(draft).toContain('Review feedback for [Quarterly report](artifacts/report.md):')
    expect(draft).toContain('Pin 1 (25% from left, 70% from top): Move this label above the chart.')
    expect(draft).toContain('General: Keep the visual hierarchy')
    expect(draft).toContain('[flow-a1b2c3.excalidraw](artifacts/whiteboards/flow-a1b2c3.excalidraw)')
  })
})

describe('Mermaid review helpers', () => {
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
})
