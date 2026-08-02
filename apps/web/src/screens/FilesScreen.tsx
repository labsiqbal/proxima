import React from 'react'
import type { FileTarget, Project } from '../types'
import type { Artifact } from '../api/files'
import { containerInspectionFs, projectFs } from '../api/fsAdapter'
import { WorkspaceTree } from '../components/files/WorkspaceTree'
import { ArtifactViewer } from '../components/artifacts/ArtifactViewer'
import { Dropdown } from '../components/ui/Dropdown'

// Files is a destination, not a right-rail tool (ADR-0040). Two scopes share one
// screen because they differ only in how many trees are on the page:
//   Work     - the active Container, matching the sidebar's project switcher.
//   Delegate - every Container, narrowed by the head filter, the way Tasks and
//              Archive already go global in that mode.
// Opening a file always goes through the ArtifactViewer, so a file browsed here
// renders exactly as it does from Chat, Tasks, or Archive.
export function FilesScreen({ token, projects, activeProject, globalScope = false, revealPath, onRevealConsumed }: {
  token: string
  projects: Project[]
  activeProject?: Project | null
  globalScope?: boolean
  revealPath?: { slug: string; path: string; pathKind?: 'root' | 'directory' | 'file'; rootSide?: 'container' | 'virtual' } | null
  onRevealConsumed?: () => void
}) {
  const [filter, setFilter] = React.useState('')
  const [open, setOpen] = React.useState<{ slug: string; items: Artifact[] } | null>(null)

  // A reveal names its own Container, so honour it over the current filter.
  React.useEffect(() => {
    if (!revealPath || !globalScope) return
    setFilter(revealPath.slug)
  }, [revealPath, globalScope])

  const shown = React.useMemo(() => {
    if (!globalScope) return activeProject ? [activeProject] : []
    return filter ? projects.filter(project => project.slug === filter) : projects
  }, [globalScope, activeProject, projects, filter])

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
    onRevealConsumed?.()
  }, [onRevealConsumed])

  return <section className="files-view">
    <div className="files-head">
      <div><h1>Files</h1></div>
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

    {shown.length === 0
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
              reveal={revealPath && revealPath.slug === project.slug ? revealPath : null}
              onOpenFile={openFile}
            />
          ))}
        </div>}

    {open && (
      <ArtifactViewer
        token={token}
        slug={open.slug}
        items={open.items}
        index={0}
        onIndex={() => {}}
        onClose={() => setOpen(null)}
      />
    )}
  </section>
}

function FilesProjectTree({ token, project, labelled, reveal, onOpenFile }: {
  token: string
  project: Project
  labelled: boolean
  reveal?: { path: string; pathKind?: 'root' | 'directory' | 'file'; rootSide?: 'container' | 'virtual' } | null
  onOpenFile: (slug: string, path: string, target?: FileTarget) => void
}) {
  // Ops-migration recovery reveals point at the Container root, which needs the
  // inspection adapter rather than the normal Area-scoped project filesystem.
  const inspecting = reveal?.rootSide === 'container'
  const fs = React.useMemo(
    () => inspecting ? containerInspectionFs(token, project.slug) : projectFs(token, project.slug),
    [inspecting, token, project.slug],
  )
  return <div className="files-project">
    {labelled && <h2 className="files-project-name">{project.name}</h2>}
    <WorkspaceTree
      key={project.slug}
      fs={fs}
      title={project.name}
      className="files-tree"
      activePath={reveal?.path ?? null}
      activePathKind={reveal?.pathKind}
      onOpenFile={(path, target) => onOpenFile(project.slug, path, target)}
    />
  </div>
}
