import React from 'react'
import type { FileTarget, OutputLink, Project } from '../types'
import { listArtifacts, type Artifact } from '../api/files'
import { listArchive, listArchiveBadges, type ArchiveBadge, type ArchiveRecord } from '../api/archive'
import { containerInspectionFs, projectFs } from '../api/fsAdapter'
import { WorkspaceTree } from '../components/files/WorkspaceTree'
import { ArtifactViewer, type ArtifactReviewFeedback, type ArtifactReviewHandoffResult } from '../components/artifacts/ArtifactViewer'
import { ArchiveRecordPage } from '../components/artifacts/ArchiveRecordPage'
import { DeliverablesLens } from '../components/artifacts/DeliverablesLens'
import { ArtifactThumb, isVisualArtifact } from '../components/artifacts/ArtifactThumb'
import { STATUS_LABELS, typeMeta } from '../components/artifacts/archive'
import { Dropdown } from '../components/ui/Dropdown'

// Artifacts is the destination (ADR-0043, amending ADR-0040): the main window
// is the gallery of what the project produced, not a file tree. What you LOOK
// at (designs, images, video) renders as thumbnails; what you READ (docs,
// pages, data) renders as a list. Three tabs sit on the same surface rather
// than becoming sub-screens:
// - All          the gallery of scanned outputs, with a deliverable badge on
//                anything the ledger knows (the badge opens its record)
// - Deliverables the durable record ledger - lineage, approval, versions (#139)
// - History      records whose file is gone from disk (records, not phantoms)
// Two scopes share the screen: Work shows the active Container, Delegate goes
// global behind the head filter, the way Tasks does. Opening an artifact goes
// through the shared ArtifactViewer (documents move to the wiki editor in #146);
// full tree browsing moves to the right dock in #145, so the only tree left
// here is the read-only Container inspection an Ops-migration reveal asks for.

type Tab = 'all' | 'deliverables' | 'history'

const TABS: { id: Tab; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'deliverables', label: 'Deliverables' },
  { id: 'history', label: 'History' },
]

// A year: the gallery is "what this project produced", not "what changed today".
const GALLERY_WINDOW_MINUTES = 525600
// The scanner caps its live result (`scan_project_artifacts(cap=40)`), so a busy
// Container shows its newest outputs only. The gallery says so rather than
// looking complete; the full, paginated truth is the Deliverables tab.
const SCAN_CAP = 40

type GalleryItem = Artifact & { projectSlug: string; projectName: string }

type Reveal = { slug: string; path: string; pathKind?: 'root' | 'directory' | 'file'; rootSide?: 'container' | 'virtual' }

const itemKey = (item: GalleryItem) => `${item.projectSlug}:${item.type}:${item.path}`

const recordAsArtifact = (r: Pick<ArchiveRecord, 'type' | 'name' | 'path' | 'project_slug' | 'target'>): Artifact => ({
  type: (r.type === 'script-output' ? 'file' : r.type) as Artifact['type'],
  title: r.name,
  path: r.path,
  project_slug: r.project_slug,
  target: r.target || undefined,
})

export function ArtifactsScreen({ token, projects, activeProject, globalScope = false, revealPath, onRevealConsumed, archiveRecord, pendingFile, pendingArtifact, onPendingConsumed, onPendingArtifactConsumed, onOpenRecord, onCloseRecord, onOpenTask, onOpenSession, designStudioEnabled = false, onOpenDesign, reviewSessionId = null, onSendFeedback }: {
  token: string
  projects: Project[]
  activeProject?: Project | null
  globalScope?: boolean
  /** An Ops-migration recovery reveal: inspect this Container path read-only. */
  revealPath?: Reveal | null
  onRevealConsumed?: () => void
  /** An open deliverable record (permanent address) renders as the record panel. */
  archiveRecord?: { project: string; slug: string } | null
  pendingFile?: { slug: string; path: string; target?: FileTarget } | null
  pendingArtifact?: OutputLink | null
  onPendingConsumed?: () => void
  onPendingArtifactConsumed?: () => void
  onOpenRecord?: (project: string, slug: string) => void
  onCloseRecord?: () => void
  onOpenTask?: (jobId: number, engine?: string) => void
  onOpenSession?: (sessionId: number) => void
  designStudioEnabled?: boolean
  onOpenDesign?: (id: string) => void
  reviewSessionId?: number | null
  onSendFeedback?: (feedback: ArtifactReviewFeedback) => ArtifactReviewHandoffResult | Promise<ArtifactReviewHandoffResult>
}) {
  const [tab, setTab] = React.useState<Tab>('all')
  const [filter, setFilter] = React.useState('')
  const [items, setItems] = React.useState<GalleryItem[]>([])
  const [badges, setBadges] = React.useState<Record<string, Record<string, ArchiveBadge>>>({})
  const [badgeNonce, setBadgeNonce] = React.useState(0)
  const [loading, setLoading] = React.useState(false)
  const [loadError, setLoadError] = React.useState('')
  const [reloadNonce, setReloadNonce] = React.useState(0)
  const [capped, setCapped] = React.useState(false)
  // A dismissed inspection stays dismissed even if the caller is slow to drop it.
  const [dismissedReveal, setDismissedReveal] = React.useState('')
  const [open, setOpen] = React.useState<{ slug: string; items: Artifact[]; index: number; sessionId?: number | null } | null>(null)
  const mountedRef = React.useRef(true)
  const loadSeq = React.useRef(0)

  React.useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false; loadSeq.current += 1 }
  }, [])

  const shown = React.useMemo(() => {
    if (!globalScope) return activeProject ? [activeProject] : []
    return filter ? projects.filter(project => project.slug === filter) : projects
  }, [globalScope, activeProject, projects, filter])
  const shownKey = shown.map(project => project.slug).join(',')
  const shownRef = React.useRef(shown)
  shownRef.current = shown

  // The gallery is a live scan of what the Containers produced; the ledger tabs
  // fetch their own records, so only the All tab pays for it.
  React.useEffect(() => {
    if (tab !== 'all') return
    const seq = ++loadSeq.current
    const scope = shownRef.current
    setLoadError('')
    if (scope.length === 0) {
      setItems([])
      setCapped(false)
      setLoading(false)
      return
    }
    setLoading(true)
    void Promise.allSettled(scope.map(project => listArtifacts(token, project.slug, GALLERY_WINDOW_MINUTES)))
      .then(results => {
        // A newer load owns the surface: leave its loading state alone.
        if (!mountedRef.current || seq !== loadSeq.current) return
        const next: GalleryItem[] = []
        let failed = 0
        let capped = false
        results.forEach((result, index) => {
          const project = scope[index]
          if (result.status !== 'fulfilled') {
            failed += 1
            return
          }
          if (result.value.artifacts.length >= SCAN_CAP) capped = true
          for (const artifact of result.value.artifacts) {
            // Runnable apps are not artifacts to look at; they get the preview
            // viewport in the main window (#147).
            if (artifact.type === 'app') continue
            next.push({ ...artifact, projectSlug: project.slug, projectName: project.name })
          }
        })
        setItems(next)
        setCapped(capped)
        if (failed) setLoadError(failed === results.length ? 'Could not scan project outputs.' : 'Some project outputs could not be scanned; the rest are shown.')
        setLoading(false)
      })
  }, [token, tab, shownKey, reloadNonce])

  // Deliverable badges: the latest record per path, per Container (#139).
  React.useEffect(() => {
    if (tab !== 'all') return
    let alive = true
    for (const project of shownRef.current) {
      listArchiveBadges(token, project.slug).then(res => {
        if (!alive || !mountedRef.current) return
        setBadges(prev => ({
          ...prev,
          [project.slug]: Object.fromEntries(res.items.map(badge => [badge.path, badge])),
        }))
      }).catch(() => {})
    }
    return () => { alive = false }
  }, [token, tab, shownKey, badgeNonce])

  const openViewerForRecord = React.useCallback((record: Pick<ArchiveRecord, 'type' | 'name' | 'path' | 'project_slug' | 'target'> & { session_id?: number | null }) => {
    setOpen({ slug: record.project_slug, items: [recordAsArtifact(record)], index: 0, sessionId: record.session_id ?? reviewSessionId })
  }, [reviewSessionId])

  // Chat result cards and task file links keep working: a pending artifact
  // resolves to its registry record (permanent address) when one exists, and
  // falls back to the universal viewer for anything not (yet) registered.
  React.useEffect(() => {
    if (!pendingArtifact) return
    const link = pendingArtifact
    onPendingArtifactConsumed?.()
    const slug = link.project_slug || activeProject?.slug
    if (!slug || !link.path) return
    const fallback = () => setOpen({
      slug,
      items: [{ type: link.type as Artifact['type'], title: link.title || link.path, path: link.path, project_slug: slug, target: link.target }],
      index: 0,
      sessionId: reviewSessionId,
    })
    void listArchive(token, { project: slug, path: link.path, limit: 1 }).then(res => {
      if (!mountedRef.current) return
      const hit = res.items[0]
      if (hit) onOpenRecord?.(hit.project_slug, hit.slug)
      else fallback()
    }).catch(() => {
      if (mountedRef.current) fallback()
    })
  }, [pendingArtifact, token, activeProject?.slug, onOpenRecord, onPendingArtifactConsumed, reviewSessionId])

  React.useEffect(() => {
    if (!pendingFile) return
    onPendingConsumed?.()
    setOpen({
      slug: pendingFile.slug,
      items: [{ type: 'file', title: pendingFile.path.split('/').pop() || pendingFile.path, path: pendingFile.path, target: pendingFile.target }],
      index: 0,
      sessionId: reviewSessionId,
    })
  }, [pendingFile, onPendingConsumed, reviewSessionId])

  const viewer = open && (
    <ArtifactViewer
      token={token}
      slug={open.slug}
      items={open.items}
      index={open.index}
      onIndex={index => setOpen(current => current ? { ...current, index } : current)}
      onClose={() => setOpen(null)}
      reviewSessionId={open.sessionId ?? null}
      onSendFeedback={onSendFeedback}
    />
  )

  // ── The record panel: a deliverable's permanent address ──
  if (archiveRecord) {
    return <section className="artifacts-view">
      <ArchiveRecordPage
        token={token}
        project={archiveRecord.project}
        slug={archiveRecord.slug}
        onBack={() => onCloseRecord?.()}
        onOpenRecord={(project, slug) => onOpenRecord?.(project, slug)}
        onOpenSession={globalScope ? undefined : onOpenSession}
        onOpenTask={onOpenTask}
        onOpenViewer={openViewerForRecord}
        onOpenDesign={designStudioEnabled ? onOpenDesign : undefined}
        onChanged={() => setBadgeNonce(nonce => nonce + 1)}
      />
      {viewer}
    </section>
  }

  const openArtifact = (item: GalleryItem) => {
    if (item.type === 'design' && designStudioEnabled && onOpenDesign) {
      onOpenDesign(item.id || item.path.split('/').filter(Boolean).slice(-1)[0] || item.path)
      return
    }
    // The viewer walks this Container's gallery, so ←/→ moves between siblings.
    const siblings = items.filter(other => other.projectSlug === item.projectSlug
      && !(other.type === 'design' && designStudioEnabled && onOpenDesign))
    const index = siblings.findIndex(other => itemKey(other) === itemKey(item))
    setOpen({ slug: item.projectSlug, items: siblings, index: index >= 0 ? index : 0, sessionId: reviewSessionId })
  }

  const badgeOf = (item: GalleryItem) => {
    const badge = badges[item.projectSlug]?.[item.path]
    return badge && !badge.file_missing ? badge : null
  }

  const recordBadge = (item: GalleryItem) => {
    const badge = badgeOf(item)
    if (!badge) return null
    const label = STATUS_LABELS[badge.status] || badge.status
    return <button
      type="button"
      className="artifacts-badge"
      data-status={badge.status}
      aria-label={`Deliverable · ${label} - open record`}
      title={`Deliverable · ${label} - open record`}
      onClick={() => onOpenRecord?.(item.projectSlug, badge.slug)}
    >◆ {label}</button>
  }

  const subtitle = (item: GalleryItem) =>
    `${globalScope ? `${item.projectName} · ` : ''}${item.path}`

  const card = (item: GalleryItem) => <div className="artifacts-card" key={itemKey(item)}>
    <button type="button" className="artifacts-card-open" onClick={() => openArtifact(item)}>
      <span className="artifacts-thumb"><ArtifactThumb token={token} slug={item.projectSlug} artifact={item} /></span>
      <span className="artifacts-card-meta">
        <strong title={item.title}>{item.title}</strong>
        <small title={item.path}>{subtitle(item)}</small>
      </span>
    </button>
    {recordBadge(item)}
  </div>

  const row = (item: GalleryItem) => <div className="artifacts-row" key={itemKey(item)}>
    <button type="button" className="artifacts-row-open" onClick={() => openArtifact(item)}>
      <span className="artifacts-glyph" aria-hidden="true">{typeMeta(item.type).ic}</span>
      <span className="artifacts-row-meta">
        <strong title={item.title}>{item.title}</strong>
        <small title={item.path}>{subtitle(item)}</small>
      </span>
      <span className="artifacts-row-kind">{typeMeta(item.type).label}</span>
    </button>
    {recordBadge(item)}
  </div>

  const visuals = items.filter(isVisualArtifact)
  const documents = items.filter(item => !isVisualArtifact(item))
  const ledgerProject = globalScope ? filter : activeProject?.slug || ''
  const revealKey = revealPath ? `${revealPath.slug}:${revealPath.path}` : ''
  const reveal = revealPath && revealKey !== dismissedReveal ? revealPath : null
  const leaveInspection = () => {
    setDismissedReveal(revealKey)
    onRevealConsumed?.()
  }

  const gallery = <div className="artifacts-body">
    {visuals.length > 0 && <section className="artifacts-section" data-testid="artifacts-gallery" aria-label="Designs, images, and video">
      <div className="artifacts-grid">{visuals.map(card)}</div>
    </section>}
    {documents.length > 0 && <section className="artifacts-section" data-testid="artifacts-documents" aria-label="Documents">
      <h2 className="artifacts-section-title">Documents</h2>
      <div className="artifacts-list">{documents.map(row)}</div>
    </section>}
    {capped && items.length > 0 && <p className="artifacts-cap-note muted">
      Showing the most recent outputs of each project - open Deliverables for the complete ledger.
    </p>}
    {items.length === 0 && <div className="artifacts-empty teaching-empty" data-testid="teaching-empty">
      {loading ? (
        <h3 className="teaching-empty-title">Scanning project outputs…</h3>
      ) : shown.length === 0 ? (
        <>
          <h3 className="teaching-empty-title">No project selected</h3>
          <p className="teaching-empty-lead">
            {globalScope
              ? 'Link a folder to see what your agents produced in it.'
              : 'Pick a project to see what your agents produced in it.'}
          </p>
        </>
      ) : (
        <>
          <h3 className="teaching-empty-title">Nothing produced here yet</h3>
          <p className="teaching-empty-lead">
            Artifacts shows what your agents made - designs, images, video, and documents - with the deliverable ledger one tab away.
          </p>
          <ol className="teaching-empty-steps" aria-label="Getting started">
            <li><span className="teaching-empty-step-n" aria-hidden="true">1</span><span>Produce work in Chat or a Task run</span></li>
            <li><span className="teaching-empty-step-n" aria-hidden="true">2</span><span>Return here to see it as a gallery</span></li>
            <li><span className="teaching-empty-step-n" aria-hidden="true">3</span><span>Open Deliverables for lineage, approval, and versions</span></li>
          </ol>
        </>
      )}
    </div>}
  </div>

  return <section className="artifacts-view">
    {loadError && <div className="error-bar">{loadError}</div>}
    <div className="artifacts-head">
      <div><h1>Artifacts</h1></div>
      <div className="artifacts-tabs" role="tablist" aria-label="Artifacts">
        {TABS.map(item => (
          <button
            key={item.id}
            type="button"
            role="tab"
            className={tab === item.id ? 'active' : ''}
            aria-selected={tab === item.id}
            onClick={() => { setTab(item.id); if (reveal) leaveInspection() }}
          >{item.label}</button>
        ))}
      </div>
      {globalScope
        ? <Dropdown
            value={filter}
            onChange={setFilter}
            minWidth={180}
            options={[
              { value: '', label: 'All projects' },
              ...projects.map(project => ({ value: project.slug, label: project.name })),
            ]}
          />
        : activeProject && <span className="artifacts-scope muted">{activeProject.name}</span>}
      {tab === 'all' && !reveal && <button className="ghost-button" onClick={() => setReloadNonce(nonce => nonce + 1)} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh'}</button>}
    </div>

    {reveal
      ? <ContainerInspection
          token={token}
          project={projects.find(project => project.slug === reveal.slug) || activeProject || null}
          reveal={reveal}
          onOpenFile={(slug, path, target) => setOpen({ slug, items: [{ type: 'file', title: path.split('/').pop() || path, path, target }], index: 0 })}
          onClose={leaveInspection}
        />
      : tab === 'all'
        ? gallery
        : <DeliverablesLens
            token={token}
            project={ledgerProject}
            missingOnly={tab === 'history'}
            onOpenRecord={onOpenRecord}
            onOpenTask={onOpenTask}
            onOpenSession={globalScope ? undefined : onOpenSession}
            onOpenViewer={openViewerForRecord}
            onOpenDesign={designStudioEnabled ? onOpenDesign : undefined}
            designStudioEnabled={designStudioEnabled}
            onRecordsChanged={() => setBadgeNonce(nonce => nonce + 1)}
          />}

    {viewer}
  </section>
}

// Ops-migration recovery inspection (prune #133/#139 heritage): a reveal names a
// Container-root path that needs the read-only inspection adapter, not the
// Area-scoped project filesystem. It is the one tree left in this destination -
// ordinary browsing moves to the right dock in #145.
function ContainerInspection({ token, project, reveal, onOpenFile, onClose }: {
  token: string
  project: Project | null
  reveal: Reveal
  onOpenFile: (slug: string, path: string, target?: FileTarget) => void
  onClose: () => void
}) {
  const inspecting = reveal.rootSide === 'container'
  const slug = project?.slug || reveal.slug
  const fs = React.useMemo(
    () => inspecting ? containerInspectionFs(token, slug) : projectFs(token, slug),
    [inspecting, token, slug],
  )
  return <div className="artifacts-inspect" data-testid="artifacts-inspect">
    <div className="artifacts-inspect-head">
      <div>
        <strong>Inspecting {project?.name || reveal.slug}</strong>
        <span className="muted mono">{reveal.path || '/'}</span>
      </div>
      <button type="button" className="ghost-button" onClick={onClose}>Close inspection</button>
    </div>
    <WorkspaceTree
      key={slug}
      fs={fs}
      title={project?.name || reveal.slug}
      className="artifacts-tree"
      activePath={reveal.path}
      activePathKind={reveal.pathKind}
      onOpenFile={(path, target) => onOpenFile(slug, path, target)}
    />
  </div>
}
