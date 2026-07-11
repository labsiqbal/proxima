// Parse a set of wiki notes into a link graph (Obsidian-style [[wikilinks]]).
export type RawNote = { path: string; content: string }
export type GraphNode = { id: string; name: string; degree: number }
export type GraphLink = { source: string; target: string }
export type WikiModel = {
  notes: RawNote[]
  nodes: GraphNode[]
  links: GraphLink[]
  backlinks: Record<string, string[]>   // note path -> paths that link to it
  resolve: (name: string) => string | null
}

export const baseName = (p: string) => (p.split('/').pop() || p).replace(/\.md$/i, '')

const LINK_RE = /\[\[([^\]]+)\]\]/g
export const ROOT_ID = '__project_root__'

// `rootName` injects a synthetic project node linked to every note, so the graph
// is centered on the project itself (not whichever leaf note has the most
// backlinks). The root is graph-only — it never appears in `backlinks`.
export function buildWikiModel(notes: RawNote[], rootName?: string): WikiModel {
  const byBase = new Map<string, string>()
  const byPath = new Map<string, string>()
  for (const n of notes) {
    byBase.set(baseName(n.path).toLowerCase(), n.path)
    byPath.set(n.path.toLowerCase(), n.path)
    byPath.set(n.path.toLowerCase().replace(/\.md$/i, ''), n.path)
  }
  const resolve = (name: string): string | null => {
    const t = name.trim().toLowerCase()
    return byPath.get(t) || byPath.get(t + '.md') || byBase.get(t) || byBase.get((t.split('/').pop() || t)) || null
  }

  const links: GraphLink[] = []
  const backlinks: Record<string, string[]> = {}
  const degree = new Map<string, number>()
  const bump = (p: string) => degree.set(p, (degree.get(p) || 0) + 1)
  const seen = new Set<string>()
  const addLink = (from: string, target: string) => {
    const tp = resolve(target)
    if (!tp || tp === from) return
    const key = from + '->' + tp
    if (seen.has(key)) return
    seen.add(key)
    links.push({ source: from, target: tp })
    ;(backlinks[tp] ||= []).push(from)
    bump(from); bump(tp)
  }
  for (const n of notes) {
    let m: RegExpExecArray | null
    LINK_RE.lastIndex = 0
    while ((m = LINK_RE.exec(n.content))) addLink(n.path, m[1].split('|')[0].split('#')[0].trim())
  }
  const nodes: GraphNode[] = notes.map(n => ({ id: n.path, name: baseName(n.path), degree: degree.get(n.path) || 0 }))
  // Project root hub (graph only): link it to every note so it's the center.
  if (rootName && notes.length) {
    for (const n of notes) { links.push({ source: ROOT_ID, target: n.path }); bump(n.path) }
    nodes.unshift({ id: ROOT_ID, name: rootName, degree: notes.length })
  }
  return { notes, nodes, links, backlinks, resolve }
}

// Convert [[Target|alias]] into a markdown link (#wiki:Target) for preview.
export function linkifyWiki(content: string): string {
  return content.replace(LINK_RE, (_full, inner: string) => {
    const [target, alias] = inner.split('|')
    const label = (alias ?? target).trim()
    return `[${label}](#wiki:${encodeURIComponent(target.split('#')[0].trim())})`
  })
}
