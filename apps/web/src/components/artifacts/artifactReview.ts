export type ArtifactAnnotation = {
  id: string
  x: number
  y: number
  note: string
  createdAt: string
}

export type ArtifactReviewState = {
  annotations: ArtifactAnnotation[]
  generalNote: string
  whiteboardPaths: string[]
}

export type MermaidSection =
  | { type: 'markdown'; content: string }
  | { type: 'mermaid'; content: string; diagramIndex: number }

const EMPTY_REVIEW: ArtifactReviewState = { annotations: [], generalNote: '', whiteboardPaths: [] }
const MAX_ANNOTATIONS = 100
const MAX_NOTE_LENGTH = 10_000
const MERMAID_FENCE = /```mermaid[^\n]*\n([\s\S]*?)```/gi

const clampPosition = (value: unknown) => Math.max(0, Math.min(1, typeof value === 'number' && Number.isFinite(value) ? value : 0))

export function artifactReviewStorageKey(slug: string, path: string): string {
  return `proxima.artifact-review.v2:${encodeURIComponent(slug)}:${encodeURIComponent(path)}`
}

export function loadArtifactReview(slug: string, path: string): ArtifactReviewState {
  try {
    const raw = window.localStorage.getItem(artifactReviewStorageKey(slug, path))
    if (!raw) return { ...EMPTY_REVIEW }
    const parsed = JSON.parse(raw) as Partial<ArtifactReviewState>
    const annotations = Array.isArray(parsed.annotations)
      ? parsed.annotations.slice(0, MAX_ANNOTATIONS).flatMap((entry): ArtifactAnnotation[] => {
        if (!entry || typeof entry !== 'object') return []
        const item = entry as Partial<ArtifactAnnotation>
        const note = typeof item.note === 'string' ? item.note.trim().slice(0, MAX_NOTE_LENGTH) : ''
        if (!note) return []
        return [{
          id: typeof item.id === 'string' ? item.id : `${Date.now()}-${Math.random()}`,
          x: clampPosition(item.x),
          y: clampPosition(item.y),
          note,
          createdAt: typeof item.createdAt === 'string' ? item.createdAt : new Date().toISOString(),
        }]
      })
      : []
    const whiteboardPaths = Array.isArray(parsed.whiteboardPaths)
      ? [...new Set(parsed.whiteboardPaths.filter((value): value is string => typeof value === 'string' && value.startsWith('artifacts/whiteboards/')))].slice(0, MAX_ANNOTATIONS)
      : []
    return {
      annotations,
      generalNote: typeof parsed.generalNote === 'string' ? parsed.generalNote.slice(0, MAX_NOTE_LENGTH) : '',
      whiteboardPaths,
    }
  } catch {
    return { ...EMPTY_REVIEW }
  }
}

export function saveArtifactReview(slug: string, path: string, review: ArtifactReviewState): void {
  try {
    window.localStorage.setItem(artifactReviewStorageKey(slug, path), JSON.stringify(review))
  } catch {
    // Review still works for this open viewer when browser storage is unavailable.
  }
}

export function splitMermaidSections(markdown: string): MermaidSection[] {
  const sections: MermaidSection[] = []
  let cursor = 0
  let diagramIndex = 0
  for (const match of markdown.matchAll(MERMAID_FENCE)) {
    const start = match.index ?? 0
    if (start > cursor) sections.push({ type: 'markdown', content: markdown.slice(cursor, start) })
    sections.push({ type: 'mermaid', content: (match[1] || '').trim(), diagramIndex })
    diagramIndex += 1
    cursor = start + match[0].length
  }
  if (cursor < markdown.length) sections.push({ type: 'markdown', content: markdown.slice(cursor) })
  return sections.length ? sections : [{ type: 'markdown', content: markdown }]
}

export function sourceFingerprint(source: string): string {
  let hash = 0x811c9dc5
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(16).padStart(8, '0')
}

export function whiteboardPathFor(sourcePath: string, diagramIndex = 0): string {
  const name = sourcePath.split('/').pop()?.replace(/\.[^.]+$/, '') || 'diagram'
  const safeName = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 48) || 'diagram'
  const suffix = sourceFingerprint(`${sourcePath}:${diagramIndex}`)
  return `artifacts/whiteboards/${safeName}-${suffix}.excalidraw`
}

export function formatArtifactReviewDraft(args: {
  title: string
  path: string
  review: ArtifactReviewState
}): string {
  const lines = [`Review feedback for [${args.title}](${args.path}):`]
  for (const [index, annotation] of args.review.annotations.entries()) {
    const left = Math.round(annotation.x * 100)
    const top = Math.round(annotation.y * 100)
    lines.push(`- Pin ${index + 1} (${left}% from left, ${top}% from top): ${annotation.note}`)
  }
  const general = args.review.generalNote.trim()
  if (general) lines.push(`- General: ${general}`)
  for (const path of args.review.whiteboardPaths) lines.push(`- Edited whiteboard: [${path.split('/').pop() || path}](${path})`)
  lines.push('', 'Please revise the artifact using this feedback and keep the result in the same project.')
  return lines.join('\n')
}

export function hasArtifactReviewFeedback(review: ArtifactReviewState): boolean {
  return review.annotations.length > 0 || review.generalNote.trim().length > 0 || review.whiteboardPaths.length > 0
}
