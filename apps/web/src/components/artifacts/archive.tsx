import React from 'react'
import type { ArchiveRecord, ArchiveStatus } from '../../api/archive'
import { previewUrl, fetchRawBlob, fileUrl } from '../../api/files'
import { projectFs } from '../../api/fsAdapter'
import { MessageContent } from '../chat/MessageContent'
import { MiniPreview } from '../design/MiniPreview'
import type { Artboard } from '../design/scene'

// Shared pieces of the Archive registry UI (Phase-1 slice 8, T4): type badges,
// the status pill, formatting, lineage line, and the per-type preview used by
// both the expanding row (compact) and the full record page (large).

export const TYPE_META: Record<string, { ic: string; label: string }> = {
  doc: { ic: '□', label: 'Doc' },
  image: { ic: '▧', label: 'Image' },
  app: { ic: '▶', label: 'App' },
  page: { ic: '◫', label: 'Page' },
  design: { ic: '◆', label: 'Design' },
  'video-file': { ic: '◉', label: 'Video' },
  file: { ic: '▦', label: 'File' },
  'script-output': { ic: '>_', label: 'Script output' },
}
export const typeMeta = (type: string) => TYPE_META[type] || { ic: '◇', label: type }

export const STATUS_LABELS: Record<ArchiveStatus, string> = {
  draft: 'Draft', review: 'In review', approved: 'Approved', superseded: 'Superseded',
}

export function StatusPill({ status }: { status: ArchiveStatus }) {
  return <span className={`archive-pill ${status}`}>{STATUS_LABELS[status] || status}</span>
}

export const fmtSize = (bytes: number | null | undefined) => {
  if (bytes == null) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export const fmtDate = (iso: string | null | undefined, full = false) => {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  if (full) return d.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  if (sameDay) return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  return d.toLocaleDateString(undefined, now.getFullYear() === d.getFullYear() ? { month: 'short', day: 'numeric' } : { year: 'numeric', month: 'short', day: 'numeric' })
}

export const permalinkOf = (record: Pick<ArchiveRecord, 'project_slug' | 'slug'>) =>
  `#archive/${record.project_slug}/${record.slug}`

// One line of provenance: chat › task › file. Each hop is a real link when the
// caller can navigate there.
export function LineageLine({ record, onOpenSession, onOpenTask }: {
  record: ArchiveRecord
  onOpenSession?: (sessionId: number) => void
  onOpenTask?: (jobId: number, engine?: string) => void
}) {
  const parts: React.ReactNode[] = []
  if (record.session_id != null) {
    parts.push(onOpenSession
      ? <button key="s" className="archive-lineage-link" onClick={e => { e.stopPropagation(); onOpenSession(record.session_id!) }}>{record.session_title || 'Chat'}</button>
      : <span key="s">{record.session_title || 'Chat'}</span>)
  }
  if (record.job_id != null) {
    parts.push(onOpenTask
      ? <button key="j" className="archive-lineage-link" onClick={e => { e.stopPropagation(); onOpenTask(record.job_id!, record.job_engine || undefined) }}>{record.job_title || `Task #${record.job_id}`}</button>
      : <span key="j">{record.job_title || `Task #${record.job_id}`}</span>)
  }
  parts.push(<span key="p" className="mono archive-lineage-path" title={record.path}>{record.path}</span>)
  return <span className="archive-lineage-line">
    {parts.map((node, i) => <React.Fragment key={i}>{i > 0 && <span className="sep" aria-hidden="true">›</span>}{node}</React.Fragment>)}
  </span>
}

const IMG = /\.(png|jpe?g|gif|webp|svg|bmp|ico|avif)$/i
const VIDEO = /\.(mp4|webm|mov)$/i
const HTML = /\.html?$/i
const MD = /\.(md|markdown)$/i

/** Resolve the scene.json path for a design archive record (folder or file). */
export const designScenePath = (path: string) => {
  const cleaned = path.replace(/\/+$/, '')
  return cleaned.endsWith('scene.json') ? cleaned : `${cleaned}/scene.json`
}

// Per-type preview for a record. Compact in the expanding row, large on the
// record page. A missing file shows the durable-record note instead of a
// broken preview - the record outlives its file.
export function RecordPreview({ token, record, compact = false }: {
  token: string
  record: Pick<ArchiveRecord, 'type' | 'path' | 'project_slug' | 'file_missing' | 'name' | 'size'>
  compact?: boolean
}) {
  const { type, path, project_slug: slug } = record
  const [media, setMedia] = React.useState<string | null>(null)
  const [md, setMd] = React.useState<string | null>(null)
  const [designArt, setDesignArt] = React.useState<Artboard | null | undefined>(undefined)
  const isImg = type === 'image' || IMG.test(path)
  const isVideo = type === 'video-file' || VIDEO.test(path)
  const isHtml = type === 'page' || HTML.test(path)
  const isMd = MD.test(path)
  const isDesign = type === 'design'
  React.useEffect(() => {
    let alive = true
    let objectUrl: string | null = null
    setMedia(null); setMd(null); setDesignArt(undefined)
    if (record.file_missing) return
    if (isImg || isVideo) {
      fetchRawBlob(token, slug, path).then(u => {
        if (!alive) { URL.revokeObjectURL(u); return }
        objectUrl = u
        setMedia(u)
      }).catch(() => {})
    } else if (isMd) {
      projectFs(token, slug).read(path).then(f => { if (alive) setMd(f.content) }).catch(() => { if (alive) setMd('') })
    } else if (isDesign) {
      // Same first-artboard thumbnail the Design gallery uses - Archive used to
      // show only "use Open to view it" with no visual of the deliverable.
      projectFs(token, slug).read(designScenePath(path)).then(f => {
        if (!alive) return
        try {
          const scene = JSON.parse(f.content) as { artboards?: Artboard[] }
          setDesignArt(scene.artboards?.[0] || null)
        } catch {
          setDesignArt(null)
        }
      }).catch(() => { if (alive) setDesignArt(null) })
    }
    return () => { alive = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [token, slug, path, record.file_missing, isImg, isVideo, isMd, isDesign])

  if (record.file_missing) {
    return <div className="archive-preview-box empty">
      <p className="muted">The file is gone from disk - this record keeps its history, lineage, and status.</p>
    </div>
  }
  if (isImg) return <div className={`archive-preview-box media ${compact ? 'compact' : ''}`}>{media && <img src={media} alt={record.name} />}</div>
  if (isVideo) return <div className={`archive-preview-box media ${compact ? 'compact' : ''}`}>{media && <video src={media} controls={!compact} muted={compact} playsInline preload="metadata" />}</div>
  if (isHtml) return <div className={`archive-preview-box frame ${compact ? 'compact' : ''}`}><iframe title={record.name} src={previewUrl(slug, path)} sandbox="allow-scripts" /></div>
  if (isMd) return <div className={`archive-preview-box doc ${compact ? 'compact' : ''}`}><div className="md">{md != null ? <MessageContent content={md} /> : <p className="muted">Loading…</p>}</div></div>
  if (isDesign) {
    if (designArt === undefined) {
      return <div className={`archive-preview-box empty ${compact ? 'compact' : ''}`}><p className="muted">Loading design…</p></div>
    }
    if (designArt) {
      const resolveSrc = (src: string) => /^(https?:|data:|blob:)/.test(src) ? src : fileUrl(slug, src)
      return <div className={`archive-preview-box design ${compact ? 'compact' : ''}`} aria-label={`Preview of ${record.name}`}>
        <div className="archive-design-thumb"><MiniPreview art={designArt} resolveSrc={resolveSrc} /></div>
      </div>
    }
    // scene.json missing or unreadable - fall through to the generic open hint
  }
  const meta = typeMeta(type)
  return <div className={`archive-preview-box empty ${compact ? 'compact' : ''}`}>
    <span className="archive-type-ic lg" aria-hidden="true">{meta.ic}</span>
    <p className="muted">{meta.label}{record.size != null ? ` · ${fmtSize(record.size)}` : ''} - use Open to view it.</p>
  </div>
}
