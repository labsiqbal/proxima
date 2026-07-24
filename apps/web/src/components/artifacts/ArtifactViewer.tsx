import React from 'react'
import { createPortal } from 'react-dom'
import { previewUrl, type Artifact } from '../../api/files'
import { projectFs } from '../../api/fsAdapter'
import { MessageContent } from '../chat/MessageContent'
import { MermaidDiagram } from './MermaidDiagram'
import {
  formatArtifactReviewDraft,
  hasArtifactReviewFeedback,
  loadArtifactReview,
  saveArtifactReview,
  splitMermaidSections,
  type ArtifactAnnotation,
  type ArtifactReviewState,
} from './artifactReview'

const ExcalidrawWhiteboard = React.lazy(() => import('./ExcalidrawWhiteboard').then(module => ({ default: module.ExcalidrawWhiteboard })))

// ArtifactViewer v2 is Proxima's native review surface. It keeps the existing
// media/document/data renderers, adds point annotations, and turns Mermaid into
// an editable Excalidraw whiteboard. Review feedback returns through Proxima chat.

const IMG = /\.(png|jpe?g|gif|webp|svg|bmp|ico|avif)$/i
const VIDEO = /\.(mp4|webm|mov|m4v)$/i
const PDF = /\.pdf$/i
const HTML = /\.html?$/i
const MD = /\.(md|markdown)$/i
const MERMAID = /\.(mmd|mermaid)$/i
const CSV = /\.(csv|tsv)$/i
const JSONF = /\.(json|excalidraw)$/i
const TEXT = /\.(txt|log|ya?ml|yml|xml|ini|conf|env|toml|py|js|ts|tsx|jsx|css|sh|sql|rs|go|rb|java|c|h|cpp)$/i

type Kind = 'image' | 'video' | 'pdf' | 'html' | 'markdown' | 'mermaid' | 'csv' | 'json' | 'text' | 'binary'
function kindOf(path: string): Kind {
  if (IMG.test(path)) return 'image'
  if (VIDEO.test(path)) return 'video'
  if (PDF.test(path)) return 'pdf'
  if (HTML.test(path)) return 'html'
  if (MD.test(path)) return 'markdown'
  if (MERMAID.test(path)) return 'mermaid'
  if (CSV.test(path)) return 'csv'
  if (JSONF.test(path)) return 'json'
  if (TEXT.test(path)) return 'text'
  return 'binary'
}
const EDITABLE = new Set<Kind>(['markdown', 'mermaid', 'csv', 'json', 'text', 'html'])

export type ArtifactReviewFeedback = {
  sessionId: number | null
  text: string
  artifact: Artifact
}

// Minimal RFC-ish CSV/TSV parse (handles quotes + escaped quotes + CRLF).
function parseDelimited(text: string, delim: string): string[][] {
  const rows: string[][] = []; let row: string[] = []; let cur = ''; let quoted = false
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index]
    if (quoted) {
      if (character === '"') { if (text[index + 1] === '"') { cur += '"'; index += 1 } else quoted = false }
      else cur += character
    } else if (character === '"') quoted = true
    else if (character === delim) { row.push(cur); cur = '' }
    else if (character === '\n') { row.push(cur); rows.push(row); row = []; cur = '' }
    else if (character !== '\r') cur += character
  }
  if (cur !== '' || row.length) { row.push(cur); rows.push(row) }
  return rows.filter(item => item.length > 1 || (item.length === 1 && item[0] !== ''))
}

const MAX_ROWS = 1000

function JsonNode({ label, value, depth }: { label?: string | number; value: unknown; depth: number }) {
  const [open, setOpen] = React.useState(depth < 2)
  const isObject = value !== null && typeof value === 'object'
  if (isObject) {
    const entries: [string | number, unknown][] = Array.isArray(value) ? value.map((entry, index) => [index, entry]) : Object.entries(value as Record<string, unknown>)
    return <div className="av-json-node">
      <button className="av-json-key" onClick={() => setOpen(current => !current)}>
        <span className="av-json-caret">{open ? '▾' : '▸'}</span>
        {label != null && <span className="jk">{label}</span>}
        <span className="jt">{Array.isArray(value) ? `[${entries.length}]` : `{${entries.length}}`}</span>
      </button>
      {open && <div className="av-json-children">{entries.map(([childLabel, childValue]) => <JsonNode key={childLabel} label={childLabel} value={childValue} depth={depth + 1} />)}</div>}
    </div>
  }
  return <div className="av-json-leaf">{label != null && <span className="jk">{label}</span>}<span className={`jv ${value === null ? 'null' : typeof value}`}>{value === undefined ? 'undefined' : JSON.stringify(value)}</span></div>
}

function reviewId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`
}

export function ArtifactViewer({ token, slug, items, index, onIndex, onClose, onEditSource, reviewSessionId = null, onSendFeedback }: {
  token: string
  slug: string
  items: Artifact[]
  index: number
  onIndex: (index: number) => void
  onClose: () => void
  onEditSource?: (artifact: Artifact) => void
  reviewSessionId?: number | null
  onSendFeedback?: (feedback: ArtifactReviewFeedback) => void
}) {
  const item = items[index]
  const path = item?.path || ''
  const name = path.split('/').pop() || path
  const kind = kindOf(path)
  const fs = React.useMemo(() => projectFs(token, slug), [token, slug])
  const [text, setText] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [zoom, setZoom] = React.useState(false)
  const [annotating, setAnnotating] = React.useState(false)
  const [pendingPoint, setPendingPoint] = React.useState<{ x: number; y: number } | null>(null)
  const [pendingNote, setPendingNote] = React.useState('')
  const [activeAnnotationId, setActiveAnnotationId] = React.useState<string | null>(null)
  const [review, setReview] = React.useState<ArtifactReviewState>(() => loadArtifactReview(slug, path))
  const [whiteboard, setWhiteboard] = React.useState<{ source: string; diagramIndex: number } | null>(null)
  const loadSeq = React.useRef(0)
  const noteRef = React.useRef<HTMLTextAreaElement>(null)

  const updateReview = React.useCallback((mutate: (current: ArtifactReviewState) => ArtifactReviewState) => {
    setReview(current => {
      const next = mutate(current)
      saveArtifactReview(slug, path, next)
      return next
    })
  }, [slug, path])

  const previous = React.useCallback(() => onIndex((index - 1 + items.length) % items.length), [index, items.length, onIndex])
  const next = React.useCallback(() => onIndex((index + 1) % items.length), [index, items.length, onIndex])

  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (whiteboard) setWhiteboard(null)
        else onClose()
      } else if (!whiteboard && event.key === 'ArrowLeft') previous()
      else if (!whiteboard && event.key === 'ArrowRight') next()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, previous, next, whiteboard])

  React.useEffect(() => {
    setZoom(false)
    setText(null)
    setError(null)
    setAnnotating(false)
    setPendingPoint(null)
    setPendingNote('')
    setActiveAnnotationId(null)
    setWhiteboard(null)
    setReview(loadArtifactReview(slug, path))
    if (!['markdown', 'mermaid', 'csv', 'json', 'text'].includes(kind)) return
    const seq = ++loadSeq.current
    fs.read(path).then(body => { if (seq === loadSeq.current) setText(body.content) })
      .catch(() => { if (seq === loadSeq.current) setError('Could not read this file.') })
  }, [fs, path, kind, slug])

  React.useEffect(() => {
    if (pendingPoint) noteRef.current?.focus()
  }, [pendingPoint])

  if (!item) return null

  const openDiagram = (source: string, diagramIndex: number) => {
    setWhiteboard({ source, diagramIndex })
    setAnnotating(false)
  }

  const stage = () => {
    if (kind === 'image') return <img className={`av-img ${zoom ? 'actual' : 'fit'}`} src={previewUrl(slug, path)} alt={name} onClick={() => { if (!annotating) setZoom(current => !current) }} title={zoom ? 'Fit to screen' : 'Actual size'} />
    if (kind === 'video') return <video className="av-video" src={previewUrl(slug, path)} controls autoPlay playsInline />
    if (kind === 'pdf') return <iframe className="av-frame" title={name} src={previewUrl(slug, path)} />
    if (kind === 'html') return <iframe className="av-frame" title={name} src={previewUrl(slug, path)} sandbox="allow-scripts" />
    if (error) return <div className="av-msg muted">{error}</div>
    if (text == null) return <div className="av-msg muted">Loading...</div>
    if (kind === 'markdown') return <div className="av-doc">{splitMermaidSections(text).map((section, sectionIndex) => section.type === 'mermaid'
      ? <MermaidDiagram key={`mermaid-${section.diagramIndex}`} source={section.content} onEdit={() => openDiagram(section.content, section.diagramIndex)} />
      : <MessageContent key={`markdown-${sectionIndex}`} content={section.content} token={token} slug={slug} />)}</div>
    if (kind === 'mermaid') return <div className="av-doc av-diagram-doc"><MermaidDiagram source={text} onEdit={() => openDiagram(text, 0)} /></div>
    if (kind === 'json') {
      try { return <div className="av-json"><JsonNode value={JSON.parse(text)} depth={0} /></div> }
      catch { return <pre className="av-text">{text}</pre> }
    }
    if (kind === 'csv') {
      const rows = parseDelimited(text, path.toLowerCase().endsWith('.tsv') ? '\t' : ',')
      if (!rows.length) return <div className="av-msg muted">Empty file.</div>
      const [head, ...body] = rows
      const shown = body.slice(0, MAX_ROWS)
      return <div className="av-table-wrap">
        <table className="av-table">
          <thead><tr>{head.map((heading, headingIndex) => <th key={headingIndex}>{heading}</th>)}</tr></thead>
          <tbody>{shown.map((row, rowIndex) => <tr key={rowIndex}>{head.map((_, columnIndex) => <td key={columnIndex}>{row[columnIndex] ?? ''}</td>)}</tr>)}</tbody>
        </table>
        {body.length > MAX_ROWS && <p className="av-table-note muted">Showing first {MAX_ROWS} of {body.length} rows. Download to see all.</p>}
      </div>
    }
    if (kind === 'text') return <pre className="av-text">{text}</pre>
    return <div className="av-msg muted">Can't preview this file type. <a href={previewUrl(slug, path)} download={name}>Download</a> to open it.</div>
  }

  const placeAnnotation = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!annotating) return
    const bounds = event.currentTarget.getBoundingClientRect()
    if (!bounds.width || !bounds.height) return
    setPendingPoint({
      x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
      y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)),
    })
    setPendingNote('')
  }

  const addAnnotation = () => {
    const note = pendingNote.trim()
    if (!pendingPoint || !note) return
    const annotation: ArtifactAnnotation = {
      id: reviewId(),
      x: pendingPoint.x,
      y: pendingPoint.y,
      note,
      createdAt: new Date().toISOString(),
    }
    updateReview(current => ({ ...current, annotations: [...current.annotations, annotation] }))
    setActiveAnnotationId(annotation.id)
    setPendingPoint(null)
    setPendingNote('')
    setAnnotating(false)
  }

  const recordWhiteboard = (whiteboardPath: string) => {
    updateReview(current => current.whiteboardPaths.includes(whiteboardPath)
      ? current
      : { ...current, whiteboardPaths: [...current.whiteboardPaths, whiteboardPath] })
  }

  const addToChat = () => {
    if (!onSendFeedback || !hasArtifactReviewFeedback(review)) return
    onSendFeedback({
      sessionId: reviewSessionId,
      text: formatArtifactReviewDraft({ title: item.title || name, path, review }),
      artifact: item,
    })
  }

  return createPortal(
    <div className="av-overlay" onClick={event => { if (event.target === event.currentTarget) onClose() }}>
      {whiteboard
        ? <React.Suspense fallback={<div className="av-msg muted">Loading whiteboard...</div>}>
          <ExcalidrawWhiteboard
            token={token}
            slug={slug}
            sourcePath={path}
            source={whiteboard.source}
            diagramIndex={whiteboard.diagramIndex}
            onClose={() => setWhiteboard(null)}
            onSaved={recordWhiteboard}
          />
        </React.Suspense>
        : <>
          <header className="av-bar" onClick={event => event.stopPropagation()}>
            <div className="av-title"><strong title={path}>{name}</strong><span className="av-review-label">Review</span>{items.length > 1 && <span className="av-count">{index + 1} / {items.length}</span>}</div>
            <div className="av-actions">
              <button type="button" className={`ghost-button ${annotating ? 'active' : ''}`} aria-pressed={annotating} onClick={() => { setAnnotating(current => !current); setPendingPoint(null) }}>{annotating ? 'Click artifact to pin' : 'Annotate'}</button>
              {EDITABLE.has(kind) && onEditSource && <button type="button" className="ghost-button" onClick={() => onEditSource(item)}>Edit source</button>}
              <a className="ghost-button" href={previewUrl(slug, path)} download={name}>Download</a>
              <button type="button" className="ghost-button" onClick={onClose} title="Close (Esc)" aria-label="Close artifact review">✕</button>
            </div>
          </header>
          <div className="av-workspace">
            <main className="av-stage">
              <div className={`av-review-surface av-kind-${kind} ${annotating ? 'annotating' : ''}`}>
                {stage()}
                <div className={`av-annotation-layer ${annotating ? 'placing' : ''}`} onClick={placeAnnotation} aria-label={annotating ? 'Click to place an annotation' : undefined}>
                  {review.annotations.map((annotation, annotationIndex) => <button
                    type="button"
                    key={annotation.id}
                    className={`av-pin ${activeAnnotationId === annotation.id ? 'active' : ''}`}
                    style={{ left: `${annotation.x * 100}%`, top: `${annotation.y * 100}%` }}
                    aria-label={`Annotation ${annotationIndex + 1}: ${annotation.note}`}
                    onClick={event => { event.stopPropagation(); setActiveAnnotationId(annotation.id) }}
                  >{annotationIndex + 1}</button>)}
                  {pendingPoint && <span className="av-pin pending" style={{ left: `${pendingPoint.x * 100}%`, top: `${pendingPoint.y * 100}%` }}>+</span>}
                </div>
              </div>
            </main>
            <aside className="av-review-panel" aria-label="Artifact feedback">
              <div className="av-review-panel-head">
                <div><p className="eyebrow">Artifact review</p><strong>{review.annotations.length} pin{review.annotations.length === 1 ? '' : 's'}</strong></div>
                <button type="button" className="ghost-button" onClick={() => { setAnnotating(true); setPendingPoint(null) }}>+ Pin</button>
              </div>
              {pendingPoint && <div className="av-note-editor">
                <label htmlFor="av-pending-note">What should change here?</label>
                <textarea id="av-pending-note" ref={noteRef} value={pendingNote} onChange={event => setPendingNote(event.target.value)} placeholder="Be specific so your agent can act on it." />
                <div><button type="button" className="ghost-button" onClick={() => { setPendingPoint(null); setPendingNote('') }}>Cancel</button><button type="button" className="primary-button" disabled={!pendingNote.trim()} onClick={addAnnotation}>Add note</button></div>
              </div>}
              <div className="av-annotation-list">
                {review.annotations.map((annotation, annotationIndex) => <article key={annotation.id} className={`av-annotation-card ${activeAnnotationId === annotation.id ? 'active' : ''}`}>
                  <button type="button" className="av-annotation-copy" onClick={() => setActiveAnnotationId(annotation.id)}><span className="av-pin static">{annotationIndex + 1}</span><span>{annotation.note}</span></button>
                  <button type="button" className="av-annotation-remove" aria-label={`Remove annotation ${annotationIndex + 1}`} onClick={() => updateReview(current => ({ ...current, annotations: current.annotations.filter(entry => entry.id !== annotation.id) }))}>Remove</button>
                </article>)}
                {!review.annotations.length && !pendingPoint && <p className="muted av-review-empty">Choose Annotate, then click anywhere on the artifact to leave a precise note.</p>}
              </div>
              {review.whiteboardPaths.length > 0 && <div className="av-whiteboard-links"><strong>Edited whiteboard</strong>{review.whiteboardPaths.map(whiteboardPath => <span className="mono" key={whiteboardPath}>{whiteboardPath}</span>)}</div>}
              <label className="av-general-note">General feedback<textarea value={review.generalNote} onChange={event => updateReview(current => ({ ...current, generalNote: event.target.value }))} placeholder="Overall direction, tone, or requested changes" /></label>
              <div className="av-review-submit">
                <button type="button" className="primary-button" disabled={!onSendFeedback || !hasArtifactReviewFeedback(review)} onClick={addToChat}>Add feedback to chat</button>
                <p className="muted">Opens this artifact's Proxima chat with an editable feedback draft.</p>
              </div>
            </aside>
          </div>
          {items.length > 1 && <button type="button" className="av-nav prev" onClick={event => { event.stopPropagation(); previous() }} title="Previous (←)">‹</button>}
          {items.length > 1 && <button type="button" className="av-nav next" onClick={event => { event.stopPropagation(); next() }} title="Next (→)">›</button>}
        </>}
    </div>,
    document.body,
  )
}
