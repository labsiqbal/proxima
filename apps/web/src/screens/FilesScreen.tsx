import React from 'react'
import type { FileTarget, OutputLink, Project } from '../types'
import type { Artifact } from '../api/files'
import { listArchive, listArchiveBadges, type ArchiveBadge, type ArchiveRecord } from '../api/archive'
import { containerInspectionFs, projectFs } from '../api/fsAdapter'
import { WorkspaceTree } from '../components/files/WorkspaceTree'
import { ArtifactViewer, type ArtifactReviewFeedback, type ArtifactReviewHandoffResult } from '../components/artifacts/ArtifactViewer'
import { ArchiveRecordPage } from '../components/artifacts/ArchiveRecordPage'
import { DeliverablesLens } from '../components/artifacts/DeliverablesLens'
import { STATUS_LABELS } from '../components/artifacts/archive'
import { Dropdown } from '../components/ui/Dropdown'

// Files is a destination, not a right-rail tool (ADR-0040), and since prune
// Part D (#139) it is ALSO where deliverables live: Archive merged into Files.
// - Browse: the real disk is the truth. Agent-produced files carry a badge
//   linking to their durable record.
// - Deliverables: the record ledger as a lens - lineage, approval, versions.
// - History: records whose file no longer exists on disk (records, not
//   phantom files - the ledger's survive-deletion property).
// Two scopes share one screen: Work renders the active Container; Delegate
// renders every Container, narrowed by the head filter, the way Tasks goes
// global in that mode. Opening a file always goes through the ArtifactViewer,
// so a file browsed here renders exactly as it does from Chat or Tasks.

type FilesLens = 'browse' | 'deliverables' | 'history'

const LENSES: { id: FilesLens; label: string }[] = [
  { id: 'browse', label: 'Browse' },
  { id: 'deliverables', label: 'Deliverables' },
  { id: 'history', label: 'History' },
]

type BadgeMap = Record<string, Record<string, ArchiveBadge>>

const recordAsArtifact = (r: Pick<ArchiveRecord, 'type' | 'name' | 'path' | 'project_slug' | 'target'>): Artifact => ({
  type: (r.type === 'script-output' ? 'file' : r.type) as Artifact['type'],
  title: r.name,
  path: r.path,
  project_slug: r.project_slug,
  target: r.target || undefined,
})

export function FilesScreen({ token, projects, activeProject, globalScope = false, revealPath, onRevealConsumed, archiveRecord, pendingFile, pendingArtifact, onPendingConsumed, onPendingArtifactConsumed, onOpenRecord, onCloseRecord, onOpenTask, onOpenSession, onActiveProject, designStudioEnabled = false, onOpenDesign, reviewSessionId = null, onSendFeedback }: {
  token: string
  projects: Project[]
  activeProject?: Project | null
  globalScope?: boolean
  revealPath?: { slug: string; path: string; pathKind?: 'root' | 'directory' | 'file'; rootSide?: 'container' | 'virtual' } | null
  onRevealConsumed?: () => void
  /** An open deliverable record (permanent address) renders as the record panel. */
  archiveRecord?: { project: string; slug: string } | null
  pendingFile?: { slug: string; path: string; target?: Artifact['target'] } | null
  pendingArtifact?: OutputLink | null
  onPendingConsumed?: () => void
  onPendingArtifactConsumed?: () => void
  onOpenRecord?: (project: string, slug: string) => void
  onCloseRecord?: () => void
  onOpenTask?: (jobId: number, engine?: string) => void
  onOpenSession?: (sessionId: number) => void
  onActiveProject?: (p: Project) => void
  designStudioEnabled?: boolean
  onOpenDesign?: (id: string) => void
  reviewSessionId?: number | null
  onSendFeedback?: (feedback: ArtifactReviewFeedback) => ArtifactReviewHandoffResult | Promise<ArtifactReviewHandoffResult>
}) {
  const [filter, setFilter] = React.useState('')
  const [lens, setLens] = React.useState<FilesLens>('browse')
  const [open, setOpen] = React.useState<{ slug: string; items: Artifact[]; sessionId?: number | null } | null>(null)
  // "Reveal in Files" raised from the record panel stays inside this screen.
  const [localReveal, setLocalReveal] = React.useState<{ slug: string; path: string; pathKind?: 'root' | 'directory' | 'file' } | null>(null)
  const [badges, setBadges] = React.useState<BadgeMap>({})
  const [badgeNonce, setBadgeNonce] = React.useState(0)
  const mountedRef = React.useRef(true)

  React.useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  const reveal = revealPath ?? localReveal

  // A reveal names its own Container, so honour it over the current filter,
  // and it targets the tree, so it forces the Browse lens.
  React.useEffect(() => {
    if (!reveal) return
    setLens('browse')
    if (globalScope) setFilter(reveal.slug)
  }, [reveal, globalScope])

  const shown = React.useMemo(() => {
    if (!globalScope) return activeProject ? [activeProject] : []
    return filter ? projects.filter(project => project.slug === filter) : projects
  }, [globalScope, activeProject, projects, filter])

  // Deliverable badges for the tree: the latest record per path, per project.
  React.useEffect(() => {
    if (lens !== 'browse') return
    let alive = true
    for (const project of shown) {
      listArchiveBadges(token, project.slug).then(res => {
        if (!alive || !mountedRef.current) return
        setBadges(prev => ({
          ...prev,
          [project.slug]: Object.fromEntries(res.items.map(b => [b.path, b])),
        }))
      }).catch(() => {})
    }
    return () => { alive = false }
  }, [token, lens, shown, badgeNonce])

  const openFile = React.useCallback((slug: string, path: string, target?: FileTarget) => {
    setOpen({
      slug,
      items: [{
        type: 'file',
        title: path.split('/').pop() || path,
        path,
        target,
      } as Artifact],
    })
    setLocalReveal(null)
    onRevealConsumed?.()
  }, [onRevealConsumed])

  const openViewerForRecord = React.useCallback((record: Pick<ArchiveRecord, 'type' | 'name' | 'path' | 'project_slug' | 'target'> & { session_id?: number | null }) => {
    setOpen({ slug: record.project_slug, items: [recordAsArtifact(record)], sessionId: record.session_id ?? reviewSessionId })
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
    void listArchive(token, { project: slug, path: link.path, limit: 1 }).then(res => {
      if (!mountedRef.current) return
      const hit = res.items[0]
      if (hit) onOpenRecord?.(hit.project_slug, hit.slug)
      else setOpen({ slug, items: [{ type: link.type as Artifact['type'], title: link.title || link.path, path: link.path, project_slug: slug, target: link.target }], sessionId: reviewSessionId })
    }).catch(() => {
      if (mountedRef.current) setOpen({ slug, items: [{ type: link.type as Artifact['type'], title: link.title || link.path, path: link.path, project_slug: slug, target: link.target }], sessionId: reviewSessionId })
    })
  }, [pendingArtifact, token, activeProject?.slug, onOpenRecord, onPendingArtifactConsumed, reviewSessionId])

  React.useEffect(() => {
    if (!pendingFile) return
    onPendingConsumed?.()
    setOpen({ slug: pendingFile.slug, items: [{ type: 'file', title: pendingFile.path.split('/').pop() || pendingFile.path, path: pendingFile.path, target: pendingFile.target }], sessionId: reviewSessionId })
  }, [pendingFile, onPendingConsumed, reviewSessionId])

  const revealRecordInFiles = React.useCallback((record: Pick<ArchiveRecord, 'path' | 'project_slug'>) => {
    const p = projects.find(x => x.slug === record.project_slug)
    if (p) onActiveProject?.(p)
    if (globalScope) setFilter(record.project_slug)
    setLocalReveal({ slug: record.project_slug, path: record.path, pathKind: 'file' })
    setLens('browse')
    onCloseRecord?.()
  }, [projects, onActiveProject, globalScope, onCloseRecord])

  const viewer = open && (
    <ArtifactViewer
      token={token}
      slug={open.slug}
      items={open.items}
      index={0}
      onIndex={() => {}}
      onClose={() => setOpen(null)}
      reviewSessionId={open.sessionId ?? null}
      onSendFeedback={onSendFeedback}
    />
  )

  // ── The record panel: a deliverable's permanent address ──
  if (archiveRecord) {
    return <section className="files-view">
      <ArchiveRecordPage
        token={token}
        project={archiveRecord.project}
        slug={archiveRecord.slug}
        onBack={() => onCloseRecord?.()}
        onOpenRecord={(p, s) => onOpenRecord?.(p, s)}
        onOpenSession={globalScope ? undefined : onOpenSession}
        onOpenTask={onOpenTask}
        onOpenViewer={openViewerForRecord}
        onOpenDesign={designStudioEnabled ? onOpenDesign : undefined}
        onRevealInFiles={revealRecordInFiles}
        onChanged={() => setBadgeNonce(n => n + 1)}
      />
      {viewer}
    </section>
  }

  const lensProject = globalScope ? filter : activeProject?.slug || ''

  return <section className="files-view">
    <div className="files-head">
      <div><h1>Files</h1></div>
      <div className="files-lens" role="group" aria-label="Files lens">
        {LENSES.map(item => (
          <button
            key={item.id}
            className={`files-lens-chip ${lens === item.id ? 'active' : ''}`}
            aria-pressed={lens === item.id}
            onClick={() => setLens(item.id)}
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
        : activeProject && <span className="files-scope muted">{activeProject.name}</span>}
    </div>

    {lens !== 'browse'
      ? <DeliverablesLens
          token={token}
          project={lensProject}
          missingOnly={lens === 'history'}
          onOpenRecord={onOpenRecord}
          onOpenTask={onOpenTask}
          onOpenSession={globalScope ? undefined : onOpenSession}
          onOpenViewer={openViewerForRecord}
          onOpenDesign={designStudioEnabled ? onOpenDesign : undefined}
          designStudioEnabled={designStudioEnabled}
          onRecordsChanged={() => setBadgeNonce(n => n + 1)}
        />
      : shown.length === 0
        ? <p className="muted files-empty">
            {globalScope
              ? 'No projects yet. Link a folder to browse its files here.'
              : 'Pick a project to browse its files.'}
          </p>
        : <div className={`files-body${globalScope ? ' files-body-global' : ''}`}>
            {shown.map(project => (
              <FilesProjectTree
                key={project.slug}
                token={token}
                project={project}
                labelled={globalScope}
                reveal={reveal && reveal.slug === project.slug ? reveal : null}
                badges={badges[project.slug]}
                onOpenRecord={onOpenRecord}
                onOpenFile={openFile}
              />
            ))}
          </div>}

    {viewer}
  </section>
}

function FilesProjectTree({ token, project, labelled, reveal, badges, onOpenRecord, onOpenFile }: {
  token: string
  project: Project
  labelled: boolean
  reveal?: { path: string; pathKind?: 'root' | 'directory' | 'file'; rootSide?: 'container' | 'virtual' } | null
  badges?: Record<string, ArchiveBadge>
  onOpenRecord?: (project: string, slug: string) => void
  onOpenFile: (slug: string, path: string, target?: FileTarget) => void
}) {
  // Ops-migration recovery reveals point at the Container root, which needs the
  // inspection adapter rather than the normal Area-scoped project filesystem.
  const inspecting = reveal?.rootSide === 'container'
  const fs = React.useMemo(
    () => inspecting ? containerInspectionFs(token, project.slug) : projectFs(token, project.slug),
    [inspecting, token, project.slug],
  )
  // Agent-produced files carry a deliverable badge; clicking it opens the
  // record. Plain span, not a nested button - the tree row is already one.
  const rowBadge = React.useCallback((path: string) => {
    const badge = badges?.[path]
    if (!badge || badge.file_missing) return null
    const label = STATUS_LABELS[badge.status] || badge.status
    return <span
      className="files-deliv-badge"
      data-status={badge.status}
      title={`Deliverable · ${label} - open record`}
      onClick={e => { e.stopPropagation(); onOpenRecord?.(project.slug, badge.slug) }}
    >◆<span className="files-deliv-badge-label">{label}</span></span>
  }, [badges, onOpenRecord, project.slug])
  return <div className="files-project">
    {labelled && <h2 className="files-project-name">{project.name}</h2>}
    <WorkspaceTree
      key={project.slug}
      fs={fs}
      title={project.name}
      className="files-tree"
      activePath={reveal?.path ?? null}
      activePathKind={reveal?.pathKind}
      rowExtra={rowBadge}
      onOpenFile={(path, target) => onOpenFile(project.slug, path, target)}
    />
  </div>
}
