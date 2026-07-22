import React from 'react'
import MiniSearch from 'minisearch'
import { baseName, type RawNote } from './wikiGraph'

export function WikiSearch({ notes, onOpen }: { notes: RawNote[]; onOpen: (path: string) => void }) {
  const [q, setQ] = React.useState('')
  const index = React.useMemo(() => {
    const ms = new MiniSearch({
      fields: ['title', 'text'],
      storeFields: ['title', 'path'],
      searchOptions: { boost: { title: 2 }, fuzzy: 0.2, prefix: true }
    })
    ms.addAll(notes.map((n, i) => ({ id: i, path: n.path, title: baseName(n.path), text: n.content })))
    return ms
  }, [notes])

  const results = q.trim() ? index.search(q.trim()) : []
  const snippet = (path: string) => {
    const note = notes.find(n => n.path === path)
    if (!note) return ''
    const i = note.content.toLowerCase().indexOf(q.trim().toLowerCase())
    const start = Math.max(0, i - 40)
    return (start > 0 ? '…' : '') + note.content.slice(start, start + 120).replace(/\n/g, ' ')
  }

  return <div className="wiki-search">
    <input autoFocus placeholder="Search all notes…" value={q} onChange={e => setQ(e.target.value)} />
    <div className="wiki-results">
      {!q.trim() && notes.length === 0 && (
        <p className="muted">No notes to search yet. Create one in Files — index.md and log.md are catalog/journal and stay out of search.</p>
      )}
      {!q.trim() && notes.length > 0 && (
        <p className="muted">Type to search note titles and content.</p>
      )}
      {q.trim() && results.length === 0 && <p className="muted">No matches.</p>}
      {results.slice(0, 50).map((r) => {
        const path = r.path as string
        return <button key={r.id} className="wiki-result" onClick={() => onOpen(path)}>
          <strong>{r.title as string}</strong>
          <small className="muted">{path}</small>
          <span className="snippet">{snippet(path)}</span>
        </button>
      })}
    </div>
  </div>
}
