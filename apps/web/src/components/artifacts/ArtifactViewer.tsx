import React from 'react'
import { createPortal } from 'react-dom'
import { previewUrl, type Artifact } from '../../api/files'
import { projectFs } from '../../api/fsAdapter'
import { MessageContent } from '../chat/MessageContent'

// Universal lightbox for viewing an artifact without leaving the current screen.
// Per-type renderers: image (fit/actual), video, document (pdf/html/markdown/text),
// data (CSV table, JSON tree). ←/→ walk the list, Esc closes. Editable text/data get
// an "Edit" button that hands back to the caller's source editor.

const IMG = /\.(png|jpe?g|gif|webp|svg|bmp|ico|avif)$/i
const VIDEO = /\.(mp4|webm|mov|m4v)$/i
const PDF = /\.pdf$/i
const HTML = /\.html?$/i
const MD = /\.(md|markdown)$/i
const CSV = /\.(csv|tsv)$/i
const JSONF = /\.json$/i
const TEXT = /\.(txt|log|ya?ml|yml|xml|ini|conf|env|toml|py|js|ts|tsx|jsx|css|sh|sql|rs|go|rb|java|c|h|cpp|toml)$/i

type Kind = 'image' | 'video' | 'pdf' | 'html' | 'markdown' | 'csv' | 'json' | 'text' | 'binary'
function kindOf(path: string): Kind {
  if (IMG.test(path)) return 'image'
  if (VIDEO.test(path)) return 'video'
  if (PDF.test(path)) return 'pdf'
  if (HTML.test(path)) return 'html'
  if (MD.test(path)) return 'markdown'
  if (CSV.test(path)) return 'csv'
  if (JSONF.test(path)) return 'json'
  if (TEXT.test(path)) return 'text'
  return 'binary'
}
const EDITABLE = new Set<Kind>(['markdown', 'csv', 'json', 'text', 'html'])

// Minimal RFC-ish CSV/TSV parse (handles quotes + escaped quotes + CRLF).
function parseDelimited(text: string, delim: string): string[][] {
  const rows: string[][] = []; let row: string[] = []; let cur = ''; let q = false
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { cur += '"'; i++ } else q = false }
      else cur += c
    } else if (c === '"') q = true
    else if (c === delim) { row.push(cur); cur = '' }
    else if (c === '\n') { row.push(cur); rows.push(row); row = []; cur = '' }
    else if (c !== '\r') cur += c
  }
  if (cur !== '' || row.length) { row.push(cur); rows.push(row) }
  return rows.filter(r => r.length > 1 || (r.length === 1 && r[0] !== ''))
}

const MAX_ROWS = 1000

function JsonNode({ k, v, depth }: { k?: string | number; v: unknown; depth: number }) {
  const [open, setOpen] = React.useState(depth < 2)
  const isObj = v !== null && typeof v === 'object'
  if (isObj) {
    const entries: [string | number, unknown][] = Array.isArray(v) ? v.map((x, i) => [i, x]) : Object.entries(v as Record<string, unknown>)
    return <div className="av-json-node">
      <button className="av-json-key" onClick={() => setOpen(o => !o)}>
        <span className="av-json-caret">{open ? '▾' : '▸'}</span>
        {k != null && <span className="jk">{k}</span>}
        <span className="jt">{Array.isArray(v) ? `[${entries.length}]` : `{${entries.length}}`}</span>
      </button>
      {open && <div className="av-json-children">{entries.map(([ck, cv]) => <JsonNode key={ck} k={ck} v={cv} depth={depth + 1} />)}</div>}
    </div>
  }
  return <div className="av-json-leaf">{k != null && <span className="jk">{k}</span>}<span className={`jv ${v === null ? 'null' : typeof v}`}>{v === undefined ? 'undefined' : JSON.stringify(v)}</span></div>
}

export function ArtifactViewer({ token, slug, items, index, onIndex, onClose, onEditSource }: {
  token: string
  slug: string
  items: Artifact[]
  index: number
  onIndex: (i: number) => void
  onClose: () => void
  onEditSource?: (a: Artifact) => void
}) {
  const item = items[index]
  const path = item?.path || ''
  const name = path.split('/').pop() || path
  const kind = kindOf(path)
  const fs = React.useMemo(() => projectFs(token, slug), [token, slug])
  const [text, setText] = React.useState<string | null>(null)
  const [err, setErr] = React.useState<string | null>(null)
  const [zoom, setZoom] = React.useState(false)
  const loadSeq = React.useRef(0)

  const prev = React.useCallback(() => onIndex((index - 1 + items.length) % items.length), [index, items.length, onIndex])
  const next = React.useCallback(() => onIndex((index + 1) % items.length), [index, items.length, onIndex])

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowLeft') prev()
      else if (e.key === 'ArrowRight') next()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, prev, next])

  // Text-ish kinds load their content; media/pdf/html stream straight from previewUrl.
  React.useEffect(() => {
    setZoom(false); setText(null); setErr(null)
    if (!['markdown', 'csv', 'json', 'text'].includes(kind)) return
    const seq = ++loadSeq.current
    fs.read(path).then(b => { if (seq === loadSeq.current) setText(b.content) })
      .catch(() => { if (seq === loadSeq.current) setErr('Could not read this file.') })
  }, [fs, path, kind])

  if (!item) return null

  const stage = () => {
    if (kind === 'image') return <img className={`av-img ${zoom ? 'actual' : 'fit'}`} src={previewUrl(slug, path)} alt={name} onClick={() => setZoom(z => !z)} title={zoom ? 'Fit to screen' : 'Actual size'} />
    if (kind === 'video') return <video className="av-video" src={previewUrl(slug, path)} controls autoPlay playsInline />
    if (kind === 'pdf') return <iframe className="av-frame" title={name} src={previewUrl(slug, path)} />
    if (kind === 'html') return <iframe className="av-frame" title={name} src={previewUrl(slug, path)} sandbox="allow-scripts" />
    if (err) return <div className="av-msg muted">{err}</div>
    if (text == null) return <div className="av-msg muted">Loading…</div>
    if (kind === 'markdown') return <div className="av-doc"><div className="md"><MessageContent content={text} /></div></div>
    if (kind === 'json') {
      try { return <div className="av-json">{<JsonNode v={JSON.parse(text)} depth={0} />}</div> }
      catch { return <pre className="av-text">{text}</pre> }
    }
    if (kind === 'csv') {
      const rows = parseDelimited(text, CSV.test(path) && path.toLowerCase().endsWith('.tsv') ? '\t' : ',')
      if (!rows.length) return <div className="av-msg muted">Empty file.</div>
      const [head, ...body] = rows
      const shown = body.slice(0, MAX_ROWS)
      return <div className="av-table-wrap">
        <table className="av-table">
          <thead><tr>{head.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
          <tbody>{shown.map((r, ri) => <tr key={ri}>{head.map((_, ci) => <td key={ci}>{r[ci] ?? ''}</td>)}</tr>)}</tbody>
        </table>
        {body.length > MAX_ROWS && <p className="av-table-note muted">Showing first {MAX_ROWS} of {body.length} rows — download to see all.</p>}
      </div>
    }
    return <div className="av-msg muted">Can't preview this file type. <a href={previewUrl(slug, path)} download={name}>Download</a> to open it.</div>
  }

  return createPortal(
    <div className="av-overlay" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <header className="av-bar" onClick={e => e.stopPropagation()}>
        <div className="av-title"><strong title={path}>{name}</strong>{items.length > 1 && <span className="av-count">{index + 1} / {items.length}</span>}</div>
        <div className="av-actions">
          {EDITABLE.has(kind) && onEditSource && <button className="ghost-button" onClick={() => onEditSource(item)}>Edit source</button>}
          <a className="ghost-button" href={previewUrl(slug, path)} download={name}>Download</a>
          <button className="ghost-button" onClick={onClose} title="Close (Esc)">✕</button>
        </div>
      </header>
      {items.length > 1 && <button className="av-nav prev" onClick={e => { e.stopPropagation(); prev() }} title="Previous (←)">‹</button>}
      <div className="av-stage" onClick={e => { if (e.target === e.currentTarget) onClose() }}>{stage()}</div>
      {items.length > 1 && <button className="av-nav next" onClick={e => { e.stopPropagation(); next() }} title="Next (→)">›</button>}
    </div>,
    document.body,
  )
}
