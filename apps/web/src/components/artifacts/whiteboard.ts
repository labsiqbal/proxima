// The Mermaid whiteboard seam: how a markdown document is split into prose and
// diagram sections, and where an edited diagram is written on disk. The path is
// derived from the source path + diagram index so re-editing the same diagram
// lands on the same file instead of piling up copies.

export type MermaidSection =
  | { type: 'markdown'; content: string }
  | { type: 'mermaid'; content: string; diagramIndex: number }

const MERMAID_FENCE = /```mermaid[^\n]*\n([\s\S]*?)```/gi

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

export function whiteboardPathFor(sourcePath: string, diagramIndex = 0, artifactsBase = 'artifacts'): string {
  const name = sourcePath.split('/').pop()?.replace(/\.[^.]+$/, '') || 'diagram'
  const safeName = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 48) || 'diagram'
  const suffix = sourceFingerprint(`${sourcePath}:${diagramIndex}`)
  return `${artifactsBase}/whiteboards/${safeName}-${suffix}.excalidraw`
}
