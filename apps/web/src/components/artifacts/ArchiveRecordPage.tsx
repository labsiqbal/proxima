import React from 'react'
import { getArchiveRecord, setArchiveStatus, type ArchiveRecordDetail, type ArchiveStatus } from '../../api/archive'
import { AppRunner } from '../files/AppRunner'
import { fmtDate, fmtSize, permalinkOf, RecordPreview, StatusPill, STATUS_LABELS, typeMeta } from './archive'

// The full-width record page (T4 combo detail, variant A): a permanent address
// per record with breadcrumb back, prev/next, large preview, metadata, the
// lineage chain as navigable links, version history, and actions. No right
// panel, no popup - this IS the page.
export function ArchiveRecordPage({ token, project, slug, onBack, onOpenRecord, onOpenSession, onOpenTask, onOpenViewer, onOpenDesign, onRevealInFiles, onChanged }: {
  token: string
  project: string
  slug: string
  onBack: () => void
  onOpenRecord: (project: string, slug: string) => void
  onOpenSession?: (sessionId: number) => void
  onOpenTask?: (jobId: number, engine?: string) => void
  onOpenViewer?: (record: ArchiveRecordDetail) => void
  onOpenDesign?: (designId: string) => void
  onRevealInFiles?: (record: ArchiveRecordDetail) => void
  onChanged?: () => void
}) {
  const [record, setRecord] = React.useState<ArchiveRecordDetail | null>(null)
  const [error, setError] = React.useState('')
  const [runApp, setRunApp] = React.useState(false)
  const [busy, setBusy] = React.useState(false)

  React.useEffect(() => {
    let alive = true
    setRecord(null); setError(''); setRunApp(false)
    getArchiveRecord(token, project, slug)
      .then(r => { if (alive) setRecord(r) })
      .catch(cause => { if (alive) setError(String(cause)) })
    return () => { alive = false }
  }, [token, project, slug])

  const changeStatus = async (status: ArchiveStatus) => {
    if (!record || busy) return
    setBusy(true)
    try {
      await setArchiveStatus(token, record.id, status)
      const fresh = await getArchiveRecord(token, project, slug)
      setRecord(fresh)
      onChanged?.()
    } catch (cause) {
      setError(String(cause))
    } finally {
      setBusy(false)
    }
  }

  const open = () => {
    if (!record) return
    if (record.type === 'design' && onOpenDesign) {
      onOpenDesign(record.path.split('/').filter(Boolean).slice(-1)[0] || record.path)
    } else if (record.type === 'app') {
      setRunApp(true)
    } else {
      onOpenViewer?.(record)
    }
  }

  if (error) return <div className="archive-record-page"><div className="archive-record-topbar"><button className="ghost-button" onClick={onBack}>‹ Archive</button></div><div className="error-bar">Could not load this record: {error}</div></div>
  if (!record) return <div className="archive-record-page"><div className="archive-record-topbar"><button className="ghost-button" onClick={onBack}>‹ Archive</button></div><p className="muted archive-record-loading">Loading record…</p></div>

  const meta = typeMeta(record.type)
  const canApprove = record.status === 'draft' || record.status === 'review'
  return <div className="archive-record-page">
    <div className="archive-record-topbar">
      <nav className="archive-crumbs" aria-label="Breadcrumb">
        <button className="archive-crumb-link" onClick={onBack}>Archive</button>
        <span className="archive-crumb-sep" aria-hidden="true">›</span>
        <span className="archive-crumb-here" title={record.name}>{record.name}</span>
      </nav>
      <span className="archive-permalink" title="Permanent address - safe to bookmark and share; it outlives the file itself.">
        <span className="archive-permalink-label">Permanent address</span>
        <span className="mono">{permalinkOf(record)}</span>
      </span>
      <span className="archive-record-nav">
        <button className="ghost-button" disabled={!record.prev_slug} onClick={() => record.prev_slug && onOpenRecord(project, record.prev_slug)}>‹ Newer</button>
        <button className="ghost-button" disabled={!record.next_slug} onClick={() => record.next_slug && onOpenRecord(project, record.next_slug)}>Older ›</button>
      </span>
    </div>
    <div className="archive-record-scroll">
      <div className="archive-record-head">
        <span className={`archive-type-ic lg ${record.type === 'script-output' ? 'mono' : ''}`} aria-hidden="true">{meta.ic}</span>
        <span className="archive-record-title">
          <strong title={record.name}>{record.name}</strong>
          <span className="archive-type-tag">{meta.label} · v{record.version} · {record.project_name}</span>
        </span>
        <StatusPill status={record.status} />
      </div>
      <div className="archive-record-grid">
        <div className="archive-record-main">
          <section className="archive-record-section">
            <h4>Preview</h4>
            {runApp && record.type === 'app'
              ? <AppRunner token={token} slug={record.project_slug} initialDir={record.path === '.' ? '' : record.path} onClose={() => setRunApp(false)} />
              : <RecordPreview token={token} record={record} />}
          </section>
          <section className="archive-record-section">
            <h4>Actions</h4>
            <div className="archive-record-actions">
              <button className="primary-button" onClick={open} disabled={record.file_missing && record.type !== 'app'}>
                {record.type === 'app' ? 'Preview app' : record.type === 'design' ? 'Open in Design' : 'Open'}
              </button>
              {canApprove && <button className="archive-approve-button" disabled={busy} onClick={() => void changeStatus('approved')}>✓ Approve</button>}
              {record.status === 'approved' && <button className="archive-approve-button" disabled>✓ Approved</button>}
              <label className="archive-status-set">
                Set status
                <select value={record.status} disabled={busy} onChange={e => void changeStatus(e.target.value as ArchiveStatus)}>
                  {(Object.keys(STATUS_LABELS) as ArchiveStatus[]).map(s => <option key={s} value={s}>{STATUS_LABELS[s]}</option>)}
                </select>
              </label>
              {onRevealInFiles && <button className="ghost-button" disabled={record.file_missing} onClick={() => onRevealInFiles(record)}>Reveal in Files</button>}
            </div>
            <p className="muted archive-record-hint">Approving here and approving the task in its review write the same status - one truth, two doors.</p>
          </section>
          <section className="archive-record-section">
            <h4>Versions</h4>
            <div className="archive-versions">
              {record.versions.map(v => <button key={v.id} className={`archive-version-row ${v.id === record.id ? 'current' : ''}`} disabled={v.id === record.id} onClick={() => onOpenRecord(project, v.slug)}>
                <span className="v mono">v{v.version}</span>
                <span className="vinfo">{v.id === record.id ? 'This record · ' : ''}produced {fmtDate(v.produced_at, true)}{v.approved_at ? ` · approved ${fmtDate(v.approved_at)}` : ''}</span>
                <StatusPill status={v.status} />
              </button>)}
            </div>
            {record.status === 'superseded' && record.superseded_by_slug && <p className="muted archive-record-hint">
              Superseded by <button className="archive-lineage-link" onClick={() => onOpenRecord(project, record.superseded_by_slug!)}>the newer version</button> - an old deliverable cannot pass as the latest.
            </p>}
          </section>
        </div>
        <div className="archive-record-side">
          <section className="archive-record-section">
            <h4>Details</h4>
            <dl className="archive-meta-grid">
              <dt>Project</dt><dd>{record.project_name}</dd>
              <dt>Location</dt><dd className="mono" title={record.path}>{record.path}</dd>
              <dt>Produced</dt><dd>{fmtDate(record.produced_at, true)}</dd>
              <dt>Size</dt><dd className="mono">{fmtSize(record.size)}</dd>
              {record.approved_at && <><dt>Approved</dt><dd>{fmtDate(record.approved_at, true)}</dd></>}
              {record.file_missing && <><dt>File</dt><dd>gone from disk - record kept</dd></>}
            </dl>
          </section>
          <section className="archive-record-section">
            <h4>Produced by</h4>
            <div className="archive-lineage-chain">
              {record.session_id != null && <button className="archive-lineage-step" onClick={() => onOpenSession?.(record.session_id!)} disabled={!onOpenSession}>
                <span className="node" aria-hidden="true">❯</span>
                <span className="step-text"><span className="step-kind">Chat</span><span className="step-name">{record.session_title || `Chat #${record.session_id}`}</span></span>
              </button>}
              {record.job_id != null && <button className="archive-lineage-step" onClick={() => onOpenTask?.(record.job_id!, record.job_engine || undefined)} disabled={!onOpenTask}>
                <span className="node" aria-hidden="true">⚙</span>
                <span className="step-text"><span className="step-kind">{record.node_id ? 'Plan' : 'Task'}</span><span className="step-name">{record.job_title || `Task #${record.job_id}`}</span></span>
              </button>}
              {record.node_id && <div className="archive-lineage-step static">
                <span className="node" aria-hidden="true">◫</span>
                <span className="step-text"><span className="step-kind">Step</span><span className="step-name">{record.node_id}</span></span>
              </div>}
              <div className="archive-lineage-step static terminal">
                <span className="node" aria-hidden="true">{meta.ic}</span>
                <span className="step-text"><span className="step-kind">File</span><span className="step-name mono">{record.path}</span></span>
              </div>
              {record.session_id == null && record.job_id == null && <p className="muted archive-record-hint">Registered from files that existed before the registry - produced before lineage was tracked.</p>}
            </div>
          </section>
        </div>
      </div>
    </div>
  </div>
}

