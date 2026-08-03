import React from 'react'
import {
  isSvgPath,
  previewUrl,
  rawUrl,
  setTargetPreviewMode,
  type Artifact,
} from '../../api/files'
import { projectFs } from '../../api/fsAdapter'
import { useRawBlobUrl } from '../../hooks/useRawBlobUrl'
import type { FileTarget } from '../../types'
import { MessageContent } from '../chat/MessageContent'
import { MermaidDiagram } from './MermaidDiagram'
import { trapModalTab } from '../ui/modalFocus'
import { DesignPreview } from './ArtifactThumb'
import { EDITABLE_KINDS, fileKind } from './fileKind'
import { splitMermaidSections } from './whiteboard'

const ExcalidrawWhiteboard = React.lazy(() => import('./ExcalidrawWhiteboard').then(module => ({ default: module.ExcalidrawWhiteboard })))

// The artifact viewer is where you LOOK at what a project produced: images,
// video, PDFs, sandboxed HTML pages, and the data documents whose rendering is
// the point (CSV tables, JSON trees, Mermaid diagrams and their editable
// whiteboard). Anything with an "Edit source" reaches the editor from here.
//
// Since #146 it renders IN THE MAIN WINDOW, not as a lightbox over it: opening
// an artifact is a destination, so the surface has a named way back instead of a
// scrim, and Escape stays with whatever overlay is genuinely on top (the dock
// panel, a confirm dialog). Only its own preview-consent alert is still modal.
// Which artifacts arrive here at all is `fileKind.opensInEditor`'s answer:
// documents you write go straight to the editor and never pass through.
//
// The artifact IS the surface (#148, ADR-0043 owner refinement): the review side
// panel that used to take a fixed 22rem column - pins, annotations, general
// feedback, and the handoff into chat - is gone, and the stage now spends the
// whole window on the file. A page too wide for a two-thirds column had to be
// scrolled sideways to be read at all, which cost more than the pins earned.

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

// Kinds that are their own page and want every pixel: an HTML preview or a PDF
// is a document laid out for a full viewport, and boxing it into a centred
// column is what forced sideways scrolling to read it (#148).
const FILLS_STAGE = new Set(['html', 'pdf'])

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

// Identifies one mounted viewer so active-preview consent stays scoped to it.
// Not a secret (the server binds consent to the owner session and the Area too),
// so `getRandomValues` is enough - and unlike `randomUUID` it also exists on a
// plain-HTTP tailnet origin, where an insecure context would otherwise leave
// this empty and make "Enable active preview" permanently disabled.
function previewSessionId(): string {
  const bytes = new Uint8Array(24)
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    crypto.getRandomValues(bytes)
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256)
    }
  }
  return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
}

type ActivePreviewState = {
  areaKey: string
  target: FileTarget
}

export function ArtifactViewer({ token, slug, items, index, onIndex, onClose, backLabel = 'Back', onEditSource }: {
  token: string
  slug: string
  items: Artifact[]
  index: number
  onIndex: (index: number) => void
  onClose: () => void
  /** Where the way back leads - the surface this viewer was opened from. */
  backLabel?: string
  onEditSource?: (artifact: Artifact) => void
}) {
  const item = items[index]
  const path = item?.path || ''
  const name = path.split('/').pop() || path
  const kind = fileKind(path)
  const fs = React.useMemo(() => projectFs(token, slug), [token, slug])
  const [text, setText] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [zoom, setZoom] = React.useState(false)
  const [whiteboard, setWhiteboard] = React.useState<{ source: string; diagramIndex: number } | null>(null)
  const [activePreview, setActivePreview] = React.useState<ActivePreviewState | null>(null)
  const [previewConsentOpen, setPreviewConsentOpen] = React.useState(false)
  const [previewModePending, setPreviewModePending] = React.useState(false)
  const [previewModeError, setPreviewModeError] = React.useState('')
  const loadSeq = React.useRef(0)
  const consentRef = React.useRef<HTMLElement>(null)
  const consentCancelRef = React.useRef<HTMLButtonElement>(null)
  const closeRef = React.useRef<HTMLButtonElement>(null)
  const surfaceRef = React.useRef<HTMLElement>(null)
  const activePreviewRef = React.useRef<ActivePreviewState | null>(null)
  const previewSessionRef = React.useRef(previewSessionId())
  const descriptionId = React.useId()
  const consentTitleId = React.useId()
  const currentAreaKey = item?.target
    ? `${item.target.project}:${item.target.area.kind}:${item.target.area.id ?? 'root'}`
    : ''
  const activeForCurrentArea = activePreview?.areaKey === currentAreaKey
    ? activePreview
    : null
  const svgImage = kind === 'image' && isSvgPath(path)
  const svgBlob = useRawBlobUrl(
    svgImage ? token : undefined,
    svgImage ? slug : undefined,
    path,
    item?.target,
  )

  // Entering the surface puts focus on the way back, so a keyboard owner starts
  // at the top of it. Returning focus to whatever opened this is the OPENER's
  // job (#146): the gallery is unmounted while this surface is up, so a trigger
  // captured here would be a detached node by the time it was restored.
  React.useLayoutEffect(() => {
    closeRef.current?.focus()
  }, [])

  const previous = React.useCallback(() => onIndex((index - 1 + items.length) % items.length), [index, items.length, onIndex])
  const next = React.useCallback(() => onIndex((index + 1) % items.length), [index, items.length, onIndex])

  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // A destination the owner navigated away from stays mounted (hidden) so
      // its state survives; its keyboard must not.
      if (surfaceRef.current?.offsetParent === null) return
      const target = event.target
      const editing = target instanceof HTMLInputElement
        || target instanceof HTMLTextAreaElement
        || target instanceof HTMLSelectElement
        || (target instanceof HTMLElement && target.isContentEditable)
      // Escape dismisses this surface's own layers only. The viewer itself is
      // the main window now (#146): leaving it is an explicit way back, and the
      // key belongs to the overlay actually on top of the screen.
      if (event.key === 'Escape') {
        // Taking the key from every other listener (capture phase, immediate
        // stop) is what keeps one press from also closing the dock panel behind
        // this surface - the rule #145 established, which the dock can no
        // longer see for itself now that nothing here is aria-modal.
        if (whiteboard) {
          event.preventDefault()
          event.stopImmediatePropagation()
          setWhiteboard(null)
        } else if (previewConsentOpen) {
          event.preventDefault()
          event.stopImmediatePropagation()
          setPreviewConsentOpen(false)
        }
      } else if (!whiteboard && !editing && event.key === 'ArrowLeft') previous()
      else if (!whiteboard && !editing && event.key === 'ArrowRight') next()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [previous, next, previewConsentOpen, whiteboard])

  React.useEffect(() => {
    setZoom(false)
    setText(null)
    setError(null)
    setWhiteboard(null)
    if (!['markdown', 'mermaid', 'csv', 'json', 'text'].includes(kind)) return
    const seq = ++loadSeq.current
    fs.read(item?.target || path).then(body => { if (seq === loadSeq.current) setText(body.content) })
      .catch(() => { if (seq === loadSeq.current) setError('Could not read this file.') })
  }, [fs, path, kind, slug, item?.target])

  React.useEffect(() => {
    if (previewConsentOpen) consentCancelRef.current?.focus()
  }, [previewConsentOpen])

  React.useEffect(() => {
    const authority = activePreviewRef.current
    if (!authority || authority.areaKey === currentAreaKey) return
    activePreviewRef.current = null
    setActivePreview(null)
    setPreviewConsentOpen(false)
    setPreviewModeError('')
    void setTargetPreviewMode(
      token,
      slug,
      authority.target,
      previewSessionRef.current,
      false,
      true,
    )
  }, [currentAreaKey, slug, token])

  React.useEffect(() => () => {
    const authority = activePreviewRef.current
    if (!authority) return
    void setTargetPreviewMode(
      token,
      slug,
      authority.target,
      previewSessionRef.current,
      false,
      true,
    )
  }, [slug, token])

  if (!item) return null

  const openDiagram = (source: string, diagramIndex: number) => {
    setWhiteboard({ source, diagramIndex })
  }

  const stage = () => {
    // A design is a folder of scene JSON, not a file with bytes to preview, so
    // it is drawn from its artboard - the same picture the gallery card shows.
    // Work opens designs in the studio; Delegate has none, and this is what it
    // gets instead of an unsupported-file dead end (#146).
    if (item.type === 'design') return <div className="av-design"><DesignPreview token={token} slug={slug} artifact={item} /></div>
    if (kind === 'image') {
      if (svgImage) {
        if (svgBlob.status === 'error') {
          return <div className="av-msg muted">Could not load this image. <button type="button" className="ghost-button sm" onClick={svgBlob.retry}>Retry</button></div>
        }
        if (svgBlob.status !== 'ready' || !svgBlob.url) {
          return <div className="av-msg muted">Loading...</div>
        }
        return <img className={`av-img ${zoom ? 'actual' : 'fit'}`} src={svgBlob.url} alt={name} onClick={() => setZoom(current => !current)} title={zoom ? 'Fit to screen' : 'Actual size'} />
      }
      return <img className={`av-img ${zoom ? 'actual' : 'fit'}`} src={previewUrl(slug, path, item.target)} alt={name} onClick={() => setZoom(current => !current)} title={zoom ? 'Fit to screen' : 'Actual size'} />
    }
    if (kind === 'video') return <video className="av-video" src={previewUrl(slug, path, item.target)} controls autoPlay playsInline />
    if (kind === 'pdf') return <iframe className="av-frame" title={name} src={previewUrl(slug, path, item.target)} />
    if (kind === 'html') {
      const authority = activeForCurrentArea
        ? { previewSession: previewSessionRef.current }
        : undefined
      // `allow-same-origin` is never granted: the preview shares Proxima's
      // origin, so an opaque sandbox is what keeps it away from the session.
      return <iframe
        className="av-frame"
        key={authority ? 'active' : 'passive'}
        title={name}
        src={previewUrl(slug, path, item.target, authority)}
        sandbox={authority ? 'allow-scripts' : ''}
      />
    }
    if (kind === 'binary') return <div className="av-msg muted">Can't preview this file type. <a href={rawUrl(slug, path, item.target)} download={name}>Download</a> to open it.</div>
    if (error) return <div className="av-msg muted">{error}</div>
    if (text == null) return <div className="av-msg muted">Loading...</div>
    if (kind === 'markdown') return <div className="av-doc">{splitMermaidSections(text).map((section, sectionIndex) => section.type === 'mermaid'
      ? <MermaidDiagram key={`mermaid-${section.diagramIndex}`} source={section.content} onEdit={() => openDiagram(section.content, section.diagramIndex)} />
      : <MessageContent
          key={`markdown-${sectionIndex}`}
          content={section.content}
          token={token}
          slug={slug}
          sourcePath={item.target?.path || path}
          fileTarget={item.target}
        />)}</div>
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
    return null
  }

  const enableActivePreview = async () => {
    if (!item.target || !previewSessionRef.current || previewModePending) return
    setPreviewModePending(true)
    setPreviewModeError('')
    try {
      const result = await setTargetPreviewMode(
        token,
        slug,
        item.target,
        previewSessionRef.current,
        true,
      )
      if (!result.active) {
        throw new Error('Active preview consent was not granted.')
      }
      const next = {
        areaKey: currentAreaKey,
        target: item.target,
      }
      activePreviewRef.current = next
      setActivePreview(next)
      setPreviewConsentOpen(false)
    } catch {
      setPreviewModeError('Active preview could not be enabled. The preview remains passive.')
    } finally {
      setPreviewModePending(false)
    }
  }

  const disableActivePreview = async () => {
    const authority = activeForCurrentArea
    if (!authority || previewModePending) return
    setPreviewModePending(true)
    setPreviewModeError('')
    try {
      await setTargetPreviewMode(
        token,
        slug,
        authority.target,
        previewSessionRef.current,
        false,
      )
      activePreviewRef.current = null
      setActivePreview(null)
      setPreviewConsentOpen(false)
    } catch {
      setPreviewModeError('Active preview could not be disabled. Close this review to revoke it automatically, then try again.')
    } finally {
      setPreviewModePending(false)
    }
  }

  return (
    <section
      className="av-surface"
      ref={surfaceRef}
      aria-label={`Artifact: ${item.title || name}`}
      aria-describedby={descriptionId}
      onKeyDown={event => {
        // Only the consent alert is modal; the surface itself is ordinary
        // main-window content and must not hold Tab hostage.
        if (previewConsentOpen && consentRef.current) trapModalTab(event, consentRef.current)
      }}
    >
      <span className="sr-only" id={descriptionId}>Preview of this artifact, filling the main window. Editable kinds open their source in the editor.</span>
      {whiteboard
        ? <React.Suspense fallback={<div className="av-msg muted">Loading whiteboard...</div>}>
          <ExcalidrawWhiteboard
            token={token}
            slug={slug}
            sourcePath={path}
            source={whiteboard.source}
            diagramIndex={whiteboard.diagramIndex}
            onClose={() => setWhiteboard(null)}
          />
        </React.Suspense>
        : <>
          <header className="av-bar">
            <div className="av-title">
              <button
                type="button"
                className="ghost-button av-back"
                ref={closeRef}
                onClick={onClose}
                aria-label={`Back to ${backLabel.toLowerCase()}`}
              >← {backLabel}</button>
              <h2 title={path}>{item.title || name}</h2>
              {kind === 'html' && <span className={`av-preview-mode ${activeForCurrentArea ? 'active' : 'passive'}`}>{activeForCurrentArea ? 'Active preview' : 'Passive preview'}</span>}
              {items.length > 1 && <span className="av-count">{index + 1} / {items.length}</span>}
            </div>
            <div className="av-actions">
              {kind === 'html' && item.target && (activeForCurrentArea
                ? <button type="button" className="ghost-button" disabled={previewModePending} onClick={() => void disableActivePreview()}>{previewModePending ? 'Disabling...' : 'Disable active preview'}</button>
                : <button type="button" className="ghost-button" disabled={previewModePending || !previewSessionRef.current} onClick={() => { setPreviewModeError(''); setPreviewConsentOpen(true) }}>Enable active preview</button>)}
              {EDITABLE_KINDS.has(kind) && onEditSource && <button type="button" className="ghost-button" onClick={() => onEditSource(item)}>Edit source</button>}
              {item.type !== 'design' && <a className="ghost-button" href={rawUrl(slug, path, item.target)} download={name}>Download</a>}
            </div>
          </header>
          {/* The stage is the whole window under the bar (#148). A page or a PDF
              fills it edge to edge, because it was laid out for a viewport of its
              own; pictures and documents keep their breathing room, since a
              picture pinned to the chrome and a line of prose stretched across a
              desktop are each their own kind of unreadable. */}
          <main className={`av-stage ${FILLS_STAGE.has(kind) ? 'fill' : ''} ${items.length > 1 ? 'walkable' : ''}`}>
            {previewModeError && <p className="av-preview-mode-error" role="alert">{previewModeError}</p>}
            <div className={`av-content av-kind-${kind}`}>{stage()}</div>
          </main>
          {items.length > 1 && <button type="button" className="av-nav prev" onClick={previous} title="Previous (←)">‹</button>}
          {items.length > 1 && <button type="button" className="av-nav next" onClick={next} title="Next (→)">›</button>}
          {previewConsentOpen && <div className="av-preview-consent-scrim">
            <section ref={consentRef} className="av-preview-consent" role="alertdialog" aria-modal="true" aria-labelledby={consentTitleId}>
              <p className="eyebrow">Trust boundary</p>
              <h3 id={consentTitleId}>Enable trusted active content?</h3>
              <p>Active content can run scripts and module workers, use network access, navigate within the preview, and send any data from this Area to external services.</p>
              <p><strong>Proxima cannot guarantee Area confidentiality while active mode is enabled.</strong> The preview keeps running in a sandbox with no access to Proxima itself: it cannot read your session, this app, or any other Area.</p>
              <div className="av-preview-consent-actions">
                <button ref={consentCancelRef} type="button" className="ghost-button" disabled={previewModePending} onClick={() => setPreviewConsentOpen(false)}>Keep passive</button>
                <button type="button" className="primary-button" disabled={previewModePending} onClick={() => void enableActivePreview()}>{previewModePending ? 'Enabling...' : 'Enable trusted active mode'}</button>
              </div>
            </section>
          </div>}
        </>}
    </section>
  )
}
