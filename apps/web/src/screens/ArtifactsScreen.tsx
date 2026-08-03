import React from 'react'
import type { FileTarget, OutputLink, Project } from '../types'
import { listArtifacts, type Artifact } from '../api/files'
import { listArchive, listArchiveBadges, type ArchiveBadge, type ArchiveRecord } from '../api/archive'
import { ArtifactViewer } from '../components/artifacts/ArtifactViewer'
import { ArchiveRecordPage } from '../components/artifacts/ArchiveRecordPage'
import { DeliverablesLens } from '../components/artifacts/DeliverablesLens'
import { DocumentEditor } from '../components/artifacts/DocumentEditor'
import { opensInEditor } from '../components/artifacts/fileKind'
import { ArtifactThumb, isVisualArtifact } from '../components/artifacts/ArtifactThumb'
import { AppViewport } from '../components/files/AppViewport'
import { STATUS_LABELS, typeMeta } from '../components/artifacts/archive'
import { Dropdown } from '../components/ui/Dropdown'
import { revealFile } from '../lib/revealFile'

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
// global behind the head filter, the way Tasks does.
// There is no tree here at all: browsing the real disk is a dock tool (#145),
// and "Reveal in Files" on a record raises the dock's reveal event.
//
// Opening an artifact takes over this main window instead of raising a popup
// (ADR-0043 decision 3, #146). This screen is the ONE router for that, because
// every way of opening a file arrives here - a gallery click, the dock browser
// and task file links through `App openFileInMainWindow`, a chat result card, a
// record panel's Open, an `#archive/...` permalink:
// - a document you write (markdown, text, source) → `DocumentEditor`, editable
//   from the first frame, because the editor is the point
// - everything else → the inline `ArtifactViewer`, which keeps its renderers,
//   review pins, and neighbour walk, and reaches the editor via "Edit source"
// Both name their way back to whatever they covered - the gallery or an open
// record. Delegate has neither a dock nor Design Studio, but it has this
// destination, so an artifact opened there behaves exactly the same.
//
// A RUNNING APP is the fourth thing this window can hold (ADR-0043 decision 4,
// #147). The dock's Run & Preview keeps the controls and asks the shell to open
// the viewport here (`pendingApp`), the same seam a handed-off file uses. It is
// not an artifact - it has no bytes, no record, and no neighbours to walk - so
// it is its own surface rather than a fifth branch of the artifact router, and
// it takes the whole window (an open artifact steps aside for it, and vice
// versa). Work only: the run is owner-power execution driven from a dock
// Delegate does not have, so Delegate gets no half of it.

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

/**
 * An artifact taken over the main window. `surface` is decided once, when it is
 * opened, from what the file is; "Edit source" is the only thing that moves an
 * already-open artifact from the viewer to the editor, and going back from
 * there returns to the viewer it was opened from rather than skipping it.
 */
type OpenArtifact = {
  slug: string
  items: Artifact[]
  index: number
  surface: 'viewer' | 'editor'
  fromViewer?: boolean
}

const itemKey = (item: GalleryItem) => `${item.projectSlug}:${item.type}:${item.path}`

const recordAsArtifact = (r: Pick<ArchiveRecord, 'type' | 'name' | 'path' | 'project_slug' | 'target'>): Artifact => ({
  type: (r.type === 'script-output' ? 'file' : r.type) as Artifact['type'],
  title: r.name,
  path: r.path,
  project_slug: r.project_slug,
  target: r.target || undefined,
})

export function ArtifactsScreen({ token, projects, activeProject, globalScope = false, archiveRecord, pendingFile, pendingArtifact, pendingApp, onPendingConsumed, onPendingArtifactConsumed, onPendingAppConsumed, onOpenRecord, onCloseRecord, onOpenTask, onOpenSession, designStudioEnabled = false, onOpenDesign }: {
  token: string
  projects: Project[]
  activeProject?: Project | null
  globalScope?: boolean
  /** An open deliverable record (permanent address) renders as the record panel. */
  archiveRecord?: { project: string; slug: string } | null
  pendingFile?: { slug: string; path: string; target?: FileTarget } | null
  pendingArtifact?: OutputLink | null
  /** The dock's Run & Preview asking for this project's app viewport (#147). */
  pendingApp?: { slug: string } | null
  onPendingConsumed?: () => void
  onPendingArtifactConsumed?: () => void
  onPendingAppConsumed?: () => void
  onOpenRecord?: (project: string, slug: string) => void
  onCloseRecord?: () => void
  onOpenTask?: (jobId: number, engine?: string) => void
  onOpenSession?: (sessionId: number) => void
  designStudioEnabled?: boolean
  onOpenDesign?: (id: string) => void
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
  const [open, setOpen] = React.useState<OpenArtifact | null>(null)
  const [appSlug, setAppSlug] = React.useState<string | null>(null)
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

  // Changing the scope refilters this destination, so an artifact opened from
  // the old scope cannot keep the main window. Declared before the pending-open
  // effects so a handoff that arrives on the same commit still wins.
  React.useEffect(() => { setOpen(null); setAppSlug(null) }, [shownKey])

  // Returning from an open artifact puts focus back on the card or row that
  // opened it (and scrolls it into view). The surface cannot do this itself:
  // the gallery is unmounted while it is up, so its trigger is a detached node.
  const returnFocusRef = React.useRef<string | null>(null)
  React.useEffect(() => {
    if (open || !returnFocusRef.current) return
    const key = returnFocusRef.current
    returnFocusRef.current = null
    const trigger = Array.from(document.querySelectorAll<HTMLElement>('[data-artifact-key]'))
      .find(element => element.dataset.artifactKey === key)
    trigger?.focus()
  }, [open])

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

  // "Reveal in Files" answers "where is this on disk", which is the dock
  // browser's question since #145. The window event keeps this destination free
  // of a tree: it names the path and the dock opens itself on it.
  const revealInDock = React.useCallback((record: Pick<ArchiveRecord, 'path' | 'project_slug'>) => {
    revealFile({ path: record.path, pathKind: 'file', projectSlug: record.project_slug })
  }, [])

  // The one place an artifact becomes a main-window surface. What the file is
  // decides which surface answers, so every entry point routes identically.
  const openArtifactAt = React.useCallback((slug: string, walk: Artifact[], index: number) => {
    const item = walk[index]
    if (!item) return
    setAppSlug(null)
    setOpen({
      slug,
      items: walk,
      index,
      surface: opensInEditor(item) ? 'editor' : 'viewer',
    })
  }, [])

  const openViewerForRecord = React.useCallback((record: Pick<ArchiveRecord, 'type' | 'name' | 'path' | 'project_slug' | 'target'>) => {
    openArtifactAt(record.project_slug, [recordAsArtifact(record)], 0)
  }, [openArtifactAt])

  // Chat result cards and task file links keep working: a pending artifact
  // resolves to its registry record (permanent address) when one exists, and
  // falls back to opening the file itself when it is not (yet) registered.
  React.useEffect(() => {
    if (!pendingArtifact) return
    const link = pendingArtifact
    onPendingArtifactConsumed?.()
    const slug = link.project_slug || activeProject?.slug
    if (!slug || !link.path) return
    const fallback = () => openArtifactAt(
      slug,
      [{ type: link.type as Artifact['type'], title: link.title || link.path, path: link.path, project_slug: slug, target: link.target }],
      0,
    )
    void listArchive(token, { project: slug, path: link.path, limit: 1 }).then(res => {
      if (!mountedRef.current) return
      const hit = res.items[0]
      if (hit) onOpenRecord?.(hit.project_slug, hit.slug)
      else fallback()
    }).catch(() => {
      if (mountedRef.current) fallback()
    })
  }, [pendingArtifact, token, activeProject?.slug, onOpenRecord, onPendingArtifactConsumed, openArtifactAt])

  // The app viewport and an open artifact are exclusive: each takes the whole
  // main window, so opening one closes the other.
  const openAppViewport = React.useCallback((slug: string) => {
    setOpen(null)
    setAppSlug(slug)
  }, [])

  // The dock started (or wants to show) an app. It is always consumed, even in
  // Delegate, so a request raised in Work cannot sit queued and surface later
  // in the wrong shell mode.
  React.useEffect(() => {
    if (!pendingApp) return
    const slug = pendingApp.slug
    onPendingAppConsumed?.()
    if (globalScope) return
    openAppViewport(slug)
  }, [pendingApp, globalScope, onPendingAppConsumed, openAppViewport])

  React.useEffect(() => {
    if (!pendingFile) return
    onPendingConsumed?.()
    openArtifactAt(
      pendingFile.slug,
      [{ type: 'file', title: pendingFile.path.split('/').pop() || pendingFile.path, path: pendingFile.path, target: pendingFile.target }],
      0,
    )
  }, [pendingFile, onPendingConsumed, openArtifactAt])

  // The open artifact owns the main window; back returns to whatever it took
  // over - an open record, or the gallery.
  const backLabel = archiveRecord ? 'Record' : 'Gallery'

  // The running app takes the window whole - no head, no tabs, no gallery
  // behind it - because a dev server is worth every pixel this destination has.
  if (appSlug && !globalScope) {
    return <section className="artifacts-view">
      <AppViewport
        token={token}
        slug={appSlug}
        projectName={projects.find(project => project.slug === appSlug)?.name}
        backLabel={backLabel}
        onClose={() => setAppSlug(null)}
      />
    </section>
  }

  const openItem = open ? open.items[open.index] : null
  const surface = open && openItem && (open.surface === 'editor'
    ? <DocumentEditor
        token={token}
        slug={open.slug}
        path={openItem.path}
        target={openItem.target}
        backLabel={open.fromViewer ? 'Artifact' : backLabel}
        onClose={() => setOpen(current => current?.fromViewer
          ? { ...current, surface: 'viewer', fromViewer: false }
          : null)}
      />
    : <ArtifactViewer
        token={token}
        slug={open.slug}
        items={open.items}
        index={open.index}
        onIndex={index => setOpen(current => current ? { ...current, index } : current)}
        onClose={() => setOpen(null)}
        backLabel={backLabel}
        onEditSource={() => setOpen(current => current ? { ...current, surface: 'editor', fromViewer: true } : current)}
      />)

  if (surface) return <section className="artifacts-view">{surface}</section>

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
        onRevealInFiles={globalScope ? undefined : revealInDock}
        onOpenAppViewport={globalScope ? undefined : openAppViewport}
        onChanged={() => setBadgeNonce(nonce => nonce + 1)}
      />
    </section>
  }

  const openArtifact = (item: GalleryItem) => {
    if (item.type === 'design' && designStudioEnabled && onOpenDesign) {
      onOpenDesign(item.id || item.path.split('/').filter(Boolean).slice(-1)[0] || item.path)
      return
    }
    // ←/→ walks what you LOOK at: the viewer-bound artifacts of this Container.
    // Documents are not on that walk - each opens alone in its editor.
    returnFocusRef.current = itemKey(item)
    if (opensInEditor(item)) {
      openArtifactAt(item.projectSlug, [item], 0)
      return
    }
    const siblings = items.filter(other => other.projectSlug === item.projectSlug
      && !opensInEditor(other)
      && !(other.type === 'design' && designStudioEnabled && onOpenDesign))
    const index = siblings.findIndex(other => itemKey(other) === itemKey(item))
    openArtifactAt(item.projectSlug, siblings, index >= 0 ? index : 0)
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
    <button type="button" className="artifacts-card-open" data-artifact-key={itemKey(item)} onClick={() => openArtifact(item)}>
      <span className="artifacts-thumb"><ArtifactThumb token={token} slug={item.projectSlug} artifact={item} /></span>
      <span className="artifacts-card-meta">
        <strong title={item.title}>{item.title}</strong>
        <small title={item.path}>{subtitle(item)}</small>
      </span>
    </button>
    {recordBadge(item)}
  </div>

  const row = (item: GalleryItem) => <div className="artifacts-row" key={itemKey(item)}>
    <button type="button" className="artifacts-row-open" data-artifact-key={itemKey(item)} onClick={() => openArtifact(item)}>
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
            onClick={() => setTab(item.id)}
          >{item.label}</button>
        ))}
      </div>
      <div className="artifacts-head-controls">
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
        {tab === 'all' && <button className="ghost-button" onClick={() => setReloadNonce(nonce => nonce + 1)} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh'}</button>}
      </div>
    </div>

    {tab === 'all'
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
  </section>
}
