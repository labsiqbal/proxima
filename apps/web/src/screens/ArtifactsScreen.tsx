import React from 'react'
import type { OutputLink, Project } from '../types'
import type { Artifact } from '../api/files'
import { listArchive, setArchiveStatus, type ArchiveCounts, type ArchiveRecord, type ArchiveStatus } from '../api/archive'
import { Dropdown } from '../components/ui/Dropdown'
import { BackButton } from '../components/ui/BackButton'
import { ArtifactViewer } from '../components/artifacts/ArtifactViewer'
import { ArchiveRecordPage } from '../components/artifacts/ArchiveRecordPage'
import { fmtDate, fmtSize, LineageLine, permalinkOf, RecordPreview, StatusPill, typeMeta } from '../components/artifacts/archive'

// The Archive (Phase-1 slice 8, T4): a durable registry of deliverables, not a
// folder scan. Detail is the captain's combo: a row expands in place for the
// quick scan, and "Open full record" navigates to the record's permanent
// address (ArchiveRecordPage). No right panel, no popup.

const clean = (n: string) => n.replace(/\s*\(private\)\s*$/i, '')
const PAGE_SIZE = 50

// Facet order for the type chips; unknown types append after these.
const TYPE_ORDER = ['doc', 'image', 'file', 'page', 'design', 'script-output', 'video-file', 'app']
const STATUSES: ArchiveStatus[] = ['draft', 'review', 'approved', 'superseded']
const DATE_CHOICES = [
  { days: 0, label: 'Any time' },
  { days: 7, label: 'Last 7 days' },
  { days: 30, label: 'Last 30 days' },
  { days: 90, label: 'Last 90 days' },
]

const recordAsArtifact = (r: Pick<ArchiveRecord, 'type' | 'name' | 'path' | 'project_slug'>): Artifact => ({
  type: (r.type === 'script-output' ? 'file' : r.type) as Artifact['type'],
  title: r.name,
  path: r.path,
  project_slug: r.project_slug,
})

export function ArtifactsScreen({ token, projects, activeProject, archiveRecord, pendingFile, pendingArtifact, onPendingConsumed, onPendingArtifactConsumed, onActiveProject, onOpenRecord, onCloseRecord, onOpenTask, onOpenSession, onBack, backLabel = 'Back', designStudioEnabled = false, onOpenDesign }: {
  token: string
  projects: Project[]
  activeProject: Project | null
  archiveRecord?: { project: string; slug: string } | null
  pendingFile?: { slug: string; path: string } | null
  pendingArtifact?: OutputLink | null
  onPendingConsumed?: () => void
  onPendingArtifactConsumed?: () => void
  onActiveProject?: (p: Project) => void
  onOpenRecord?: (project: string, slug: string) => void
  onCloseRecord?: () => void
  onOpenTask?: (jobId: number, engine?: string) => void
  onOpenSession?: (sessionId: number) => void
  onBack?: () => void
  backLabel?: string
  designStudioEnabled?: boolean
  onOpenDesign?: (id: string) => void
}) {
  const [project, setProject] = React.useState('')
  const [type, setType] = React.useState('')
  const [status, setStatus] = React.useState<ArchiveStatus | ''>('')
  const [q, setQ] = React.useState('')
  const [days, setDays] = React.useState(0)
  const [records, setRecords] = React.useState<ArchiveRecord[]>([])
  const [total, setTotal] = React.useState(0)
  const [counts, setCounts] = React.useState<ArchiveCounts>({ by_type: {}, by_status: {} })
  const [expandedId, setExpandedId] = React.useState<number | null>(null)
  const [viewer, setViewer] = React.useState<{ items: Artifact[]; slug: string } | null>(null)
  const [loading, setLoading] = React.useState(false)
  const [loadError, setLoadError] = React.useState('')
  const loadSeq = React.useRef(0)
  const mountedRef = React.useRef(true)

  React.useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false; loadSeq.current += 1 }
  }, [])

  const fetchPage = React.useCallback(async (offset: number, append: boolean) => {
    const seq = ++loadSeq.current
    setLoading(true)
    setLoadError('')
    try {
      const res = await listArchive(token, { project, type, status, q, days, limit: PAGE_SIZE, offset })
      if (!mountedRef.current || seq !== loadSeq.current) return
      const items = res.items
      setRecords(prev => append ? [...prev, ...items] : items)
      setTotal(res.total)
      setCounts(res.counts)
    } catch (cause) {
      if (mountedRef.current && seq === loadSeq.current) setLoadError(String(cause))
    } finally {
      if (mountedRef.current && seq === loadSeq.current) setLoading(false)
    }
  }, [token, project, type, status, q, days])

  React.useEffect(() => { setExpandedId(null); void fetchPage(0, false) }, [fetchPage])

  const refresh = React.useCallback(() => { void fetchPage(0, false) }, [fetchPage])

  const pickProject = React.useCallback((slug: string) => {
    setProject(slug)
    const next = projects.find(p => p.slug === slug)
    if (next) onActiveProject?.(next)
  }, [projects, onActiveProject])

  // Chat result cards and task file links keep working: a pending artifact
  // resolves to its registry record (permanent address) when one exists, and
  // falls back to the universal viewer for anything not (yet) registered.
  React.useEffect(() => {
    if (!pendingArtifact) return
    const link = pendingArtifact
    onPendingArtifactConsumed?.()
    const slug = link.project_slug || activeProject?.slug
    if (!slug || !link.path) return
    void listArchive(token, { project: slug, path: link.path, limit: 1 }).then(res => {
      if (!mountedRef.current) return
      const hit = res.items[0]
      if (hit) onOpenRecord?.(hit.project_slug, hit.slug)
      else setViewer({ items: [{ type: link.type as Artifact['type'], title: link.title || link.path, path: link.path, project_slug: slug }], slug })
    }).catch(() => {
      if (mountedRef.current) setViewer({ items: [{ type: link.type as Artifact['type'], title: link.title || link.path, path: link.path, project_slug: slug }], slug })
    })
  }, [pendingArtifact, token, activeProject?.slug, onOpenRecord, onPendingArtifactConsumed])

  React.useEffect(() => {
    if (!pendingFile) return
    onPendingConsumed?.()
    setViewer({ items: [{ type: 'file', title: pendingFile.path.split('/').pop() || pendingFile.path, path: pendingFile.path }], slug: pendingFile.slug })
  }, [pendingFile, onPendingConsumed])

  const approve = async (record: ArchiveRecord) => {
    try {
      const updated = await setArchiveStatus(token, record.id, 'approved')
      if (!mountedRef.current) return
      setRecords(prev => prev.map(r => r.id === record.id ? { ...r, ...updated } : r))
      setCounts(prev => ({
        ...prev,
        by_status: {
          ...prev.by_status,
          [record.status]: Math.max(0, (prev.by_status[record.status] || 1) - 1),
          approved: (prev.by_status.approved || 0) + 1,
        },
      }))
    } catch (cause) {
      if (mountedRef.current) setLoadError(String(cause))
    }
  }

  const openViewer = (r: Pick<ArchiveRecord, 'type' | 'name' | 'path' | 'project_slug'>) =>
    setViewer({ items: [recordAsArtifact(r)], slug: r.project_slug })

  // Design opens in its studio, an app runs on its full record page, everything
  // else opens in the universal viewer.
  const openRecord = (r: ArchiveRecord) => {
    if (r.type === 'design' && designStudioEnabled && onOpenDesign) {
      onOpenDesign(r.path.split('/').filter(Boolean).slice(-1)[0] || r.path)
    } else if (r.type === 'app') {
      onOpenRecord?.(r.project_slug, r.slug)
    } else {
      openViewer(r)
    }
  }

  const revealInFiles = (r: Pick<ArchiveRecord, 'path' | 'project_slug'>) => {
    const p = projects.find(x => x.slug === r.project_slug)
    if (p) onActiveProject?.(p)
    // The right tool rail owns the Files panel; this event asks it to open on
    // this record's file (see ToolDock).
    window.dispatchEvent(new CustomEvent('proxima:reveal-file', { detail: { path: r.path } }))
  }

  if (projects.length === 0) return <section className="placeholder-view"><div className="assistant-bubble compact"><h1>Archive</h1><p>No projects yet.</p></div></section>

  // ── Full record page (variant A: its own permanent address) ──
  if (archiveRecord) {
    return <section className="artifacts-view">
      <ArchiveRecordPage
        token={token}
        project={archiveRecord.project}
        slug={archiveRecord.slug}
        onBack={() => onCloseRecord?.()}
        onOpenRecord={(p, s) => onOpenRecord?.(p, s)}
        onOpenSession={onOpenSession}
        onOpenTask={onOpenTask}
        onOpenViewer={openViewer}
        onOpenDesign={designStudioEnabled ? onOpenDesign : undefined}
        onRevealInFiles={revealInFiles}
        onChanged={refresh}
      />
      {viewer && <ArtifactViewer token={token} slug={viewer.slug} items={viewer.items} index={0} onIndex={() => {}} onClose={() => setViewer(null)} />}
    </section>
  }

  const typeKeys = [...TYPE_ORDER.filter(t => counts.by_type[t]), ...Object.keys(counts.by_type).filter(t => !TYPE_ORDER.includes(t)).sort()]
  const totalCount = Object.values(counts.by_type).reduce((a, b) => a + b, 0)

  const row = (r: ArchiveRecord) => {
    const meta = typeMeta(r.type)
    const canApprove = r.status === 'draft' || r.status === 'review'
    const expanded = expandedId === r.id
    return <React.Fragment key={r.id}>
      <button className={`archive-row ${expanded ? 'active' : ''} ${r.status === 'superseded' ? 'superseded' : ''}`} aria-expanded={expanded} onClick={() => setExpandedId(expanded ? null : r.id)}>
        <span className="archive-row-name">
          <span className={`archive-type-ic ${r.type === 'script-output' ? 'mono' : ''}`} aria-hidden="true">{meta.ic}</span>
          <span className="archive-row-name-text">
            <strong title={r.name}>{r.name}</strong>
            <span className="archive-type-tag">{meta.label}{r.version > 1 ? ` · v${r.version}` : ''}{r.file_missing ? ' · file gone' : ''}</span>
          </span>
        </span>
        <span className="archive-row-loc mono" title={`${r.project_slug} / ${r.path}`}><span className="proj">{clean(r.project_name)}</span>{r.area ? ` / ${r.area}` : ''}</span>
        <span className="archive-row-lineage">{r.job_title ? <>by <em>{r.job_title}</em></> : r.session_title ? <>from <em>{r.session_title}</em></> : <span className="muted">before the registry</span>}</span>
        <span className="archive-row-status"><StatusPill status={r.status} /></span>
        <span className="archive-row-date">{fmtDate(r.produced_at)}</span>
        <span className="archive-row-size mono">{fmtSize(r.size)}</span>
        {/* Hover shortcut only (the row itself is a button, so no nested role);
            keyboard users approve from the expanded row or the record page. */}
        {canApprove && <span className="archive-approve-hover" onClick={e => { e.stopPropagation(); void approve(r) }}>✓ Approve</span>}
      </button>
      {expanded && <div className="archive-exp-row">
        <div className="archive-exp-preview"><RecordPreview token={token} record={r} compact /></div>
        <div className="archive-exp-info">
          <LineageLine record={r} onOpenSession={onOpenSession} onOpenTask={onOpenTask} />
          <div className="archive-exp-status">
            <StatusPill status={r.status} />
            {r.status === 'approved' && r.approved_at && <span className="muted">Approved {fmtDate(r.approved_at)}</span>}
            {canApprove && <button className="archive-approve-button" onClick={() => void approve(r)}>✓ Approve</button>}
            {r.status === 'approved' && <button className="archive-approve-button" disabled>✓ Approved</button>}
          </div>
          <div className="archive-exp-foot">
            <button className="archive-link-button" onClick={() => onOpenRecord?.(r.project_slug, r.slug)}>Open full record →</button>
            <button className="archive-link-button" onClick={() => openRecord(r)} disabled={r.file_missing && r.type !== 'app'}>Open</button>
            <span className="mono muted archive-exp-url" title={permalinkOf(r)}>{permalinkOf(r)}</span>
          </div>
        </div>
      </div>}
    </React.Fragment>
  }

  return <section className="artifacts-view">
    {loadError && <div className="error-bar">Could not load the archive: {loadError}</div>}
    <div className="archive-head">
      {onBack && <BackButton label={backLabel} onClick={onBack} />}
      <div className="archive-head-titles">
        <h2>Archive</h2>
        <p className="muted">Every deliverable of record, with lineage and approval - across all projects.</p>
      </div>
      <div className="archive-head-controls">
        <Dropdown value={project} onChange={pickProject} minWidth={180} options={[{ value: '', label: 'All projects' }, ...projects.map(p => ({ value: p.slug, label: clean(p.name) }))]} />
        <input className="archive-search" type="search" placeholder="Search deliverables…" aria-label="Search deliverables" value={q} onChange={e => setQ(e.target.value)} />
        <button className="ghost-button" onClick={refresh} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh'}</button>
      </div>
    </div>
    <div className="archive-facets">
      <div className="archive-facet-group" role="group" aria-label="Type">
        <span className="archive-facet-label">Type</span>
        <button className={`archive-chip ${type === '' ? 'active' : ''}`} onClick={() => setType('')}>All <span className="count">{totalCount}</span></button>
        {typeKeys.map(t => <button key={t} className={`archive-chip ${type === t ? 'active' : ''}`} onClick={() => setType(type === t ? '' : t)}>{typeMeta(t).label} <span className="count">{counts.by_type[t]}</span></button>)}
      </div>
      <div className="archive-facet-group" role="group" aria-label="Status">
        <span className="archive-facet-label">Status</span>
        {STATUSES.map(s => <button key={s} className={`archive-chip status ${status === s ? 'active' : ''}`} data-status={s} onClick={() => setStatus(status === s ? '' : s)}><span className="dot" aria-hidden="true" />{s === 'review' ? 'In review' : s[0].toUpperCase() + s.slice(1)} <span className="count">{counts.by_status[s] || 0}</span></button>)}
      </div>
      <div className="archive-facet-group">
        <span className="archive-facet-label">Date</span>
        <select className="archive-date-select" aria-label="Date range" value={days} onChange={e => setDays(Number(e.target.value))}>
          {DATE_CHOICES.map(c => <option key={c.days} value={c.days}>{c.label}</option>)}
        </select>
      </div>
    </div>
    <div className="archive-registry">
      <div className="archive-cols" aria-hidden="true">
        <span>Deliverable</span>
        <span className="col-loc">Location</span>
        <span className="col-lineage">Produced by</span>
        <span>Status</span>
        <span>Produced</span>
        <span className="col-size">Size</span>
      </div>
      <div className="archive-scroll">
        {records.map(row)}
        {records.length === 0 && <div className="archive-empty">
          <h4>{loading ? 'Loading records…' : 'No records match these filters'}</h4>
          {!loading && <p className="muted">Registry records are durable: even if a file moves or is deleted, its record - lineage, approvals, versions - stays right here.</p>}
        </div>}
      </div>
      <div className="archive-foot">
        <span className="archive-durable-note muted">Records survive file moves - the scanner only feeds the registry, it never owns it.</span>
        <span className="archive-foot-count">
          <span className="muted">Showing {records.length} of {total} record{total === 1 ? '' : 's'}</span>
          {records.length < total && <button className="ghost-button" disabled={loading} onClick={() => void fetchPage(records.length, true)}>Load more</button>}
        </span>
      </div>
    </div>
    {viewer && <ArtifactViewer token={token} slug={viewer.slug} items={viewer.items} index={0} onIndex={() => {}} onClose={() => setViewer(null)} />}
  </section>
}
