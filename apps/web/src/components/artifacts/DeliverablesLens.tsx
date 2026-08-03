import React from 'react'
import { listArchive, setArchiveStatus, type ArchiveCounts, type ArchiveRecord, type ArchiveStatus } from '../../api/archive'
import { fmtDate, fmtSize, LineageLine, permalinkOf, RecordPreview, StatusPill, typeMeta } from './archive'

// The deliverable ledger (prune Part D, #139): the durable deliverable
// registry, rendered as a filterable list. It is the Deliverables tab of the
// Artifacts destination (ADR-0043). Rows expand in place for the quick scan;
// "Open full record" navigates to the record's permanent address. `missingOnly`
// is the History tab: records whose file is gone from disk - they are records,
// not phantom files.

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

export function DeliverablesLens({ token, project = '', missingOnly = false, onOpenRecord, onOpenTask, onOpenSession, onOpenViewer, onOpenDesign, designStudioEnabled = false, onRecordsChanged }: {
  token: string
  /** Project slug to scope to; '' lists every project (Delegate's global lens). */
  project?: string
  /** The history filter: only records whose file no longer exists on disk. */
  missingOnly?: boolean
  onOpenRecord?: (project: string, slug: string) => void
  onOpenTask?: (jobId: number, engine?: string) => void
  onOpenSession?: (sessionId: number) => void
  onOpenViewer?: (record: Pick<ArchiveRecord, 'type' | 'name' | 'path' | 'project_slug' | 'target'> & { session_id?: number | null }) => void
  onOpenDesign?: (id: string) => void
  designStudioEnabled?: boolean
  /** Approvals notify the parent so tree badges stay in sync. */
  onRecordsChanged?: () => void
}) {
  const [type, setType] = React.useState('')
  const [status, setStatus] = React.useState<ArchiveStatus | ''>('')
  const [q, setQ] = React.useState('')
  const [days, setDays] = React.useState(0)
  const [records, setRecords] = React.useState<ArchiveRecord[]>([])
  const [total, setTotal] = React.useState(0)
  const [counts, setCounts] = React.useState<ArchiveCounts>({ by_type: {}, by_status: {} })
  const [expandedId, setExpandedId] = React.useState<number | null>(null)
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
      const res = await listArchive(token, { project, type, status, q, days, missing: missingOnly ? 1 : undefined, limit: PAGE_SIZE, offset })
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
  }, [token, project, type, status, q, days, missingOnly])

  React.useEffect(() => { setExpandedId(null); void fetchPage(0, false) }, [fetchPage])

  const refresh = React.useCallback(() => { void fetchPage(0, false) }, [fetchPage])

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
      onRecordsChanged?.()
    } catch (cause) {
      if (mountedRef.current) setLoadError(String(cause))
    }
  }

  // Design opens in its studio, an app runs on its full record page, everything
  // else opens in the universal viewer.
  const openRecord = (r: ArchiveRecord) => {
    if (r.type === 'design' && designStudioEnabled && onOpenDesign) {
      onOpenDesign(r.path.split('/').filter(Boolean).slice(-1)[0] || r.path)
    } else if (r.type === 'app') {
      onOpenRecord?.(r.project_slug, r.slug)
    } else {
      onOpenViewer?.(r)
    }
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

  return <div className="archive-lens">
    {loadError && <div className="error-bar">Could not load deliverable records: {loadError}</div>}
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
      <div className="archive-facet-group archive-facet-search">
        <input className="archive-search" type="search" placeholder="Search deliverables…" aria-label="Search deliverables" value={q} onChange={e => setQ(e.target.value)} />
        <button className="ghost-button" onClick={refresh} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh'}</button>
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
        {records.length === 0 && <div className="archive-empty teaching-empty" data-testid="teaching-empty">
          {loading ? (
            <h3 className="teaching-empty-title">Loading records…</h3>
          ) : missingOnly ? (
            <>
              <h3 className="teaching-empty-title">No history records here</h3>
              <p className="teaching-empty-lead">
                History keeps the record of deliverables whose file was later moved or deleted - lineage and approval survive the file.
              </p>
            </>
          ) : (
            <>
              <h3 className="teaching-empty-title">No records match these filters</h3>
              <p className="teaching-empty-lead">
                Deliverables is the durable registry of agent output from Chat, Tasks, and Workflows - a lens over the same files you browse here.
              </p>
              <ul className="teaching-empty-caps" aria-label="What you can do here">
                <li>Browse docs, images, video, designs, and other outputs</li>
                <li>Open a document in the editor, and everything else in the review viewer</li>
                <li>Follow lineage back to the session or task that produced them</li>
              </ul>
              <ol className="teaching-empty-steps" aria-label="Getting started">
                <li><span className="teaching-empty-step-n" aria-hidden="true">1</span><span>Produce work in Chat or a Task run</span></li>
                <li><span className="teaching-empty-step-n" aria-hidden="true">2</span><span>Return here to find deliverables by type or status</span></li>
                <li><span className="teaching-empty-step-n" aria-hidden="true">3</span><span>Open a row for preview, or the full record for approvals</span></li>
              </ol>
            </>
          )}
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
  </div>
}
