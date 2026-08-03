import React from 'react'
import type { FileTarget, Project } from '../../types'
import { containerInspectionFs, projectFs } from '../../api/fsAdapter'
import type { RevealTarget } from '../../lib/revealFile'
import { WorkspaceTree } from './WorkspaceTree'

// The dock's Files tool (#145): the real-disk tree for one Container, with the
// prune's semantics intact because it is the same WorkspaceTree over the same
// Files API - paths mean what they say, warn-and-skip symlink markers stay inert
// (#142), layout-map targets browse their mapped Area (#136).
//
// It answers two shapes. Ordinary browsing follows the shell's active project
// and hands an opened file to the main window. A reveal (see `lib/revealFile`)
// points it somewhere else: another Container, or the Container root side an
// Ops-migration recovery inspects read-only - the transient inspection panel the
// Artifacts destination carried until this ticket absorbed it.
export function DockFileBrowser({ token, project, projects, reveal, onLeaveReveal, onOpenFile }: {
  token: string
  /** The shell's active project - where ordinary browsing starts. */
  project: Project | null
  /** Named so a reveal into another Container can title its tree properly. */
  projects: Project[]
  reveal: RevealTarget | null
  onLeaveReveal: () => void
  /**
   * The handoff seam: opening a file is a main-window event, not a panel-sized
   * editor. Under inspection it is deliberately unused - those bytes exist only
   * through the read-only adapter, so that tree reads them in place.
   */
  onOpenFile?: (slug: string, path: string, target?: FileTarget) => void
}) {
  const slug = reveal?.projectSlug || project?.slug
  const inspecting = reveal?.rootSide === 'container'
  const name = (slug === project?.slug
    ? project?.name
    : projects.find(item => item.slug === slug)?.name) || slug || ''
  const fs = React.useMemo(
    () => slug
      ? inspecting ? containerInspectionFs(token, slug) : projectFs(token, slug)
      : null,
    [inspecting, slug, token],
  )

  if (!fs || !slug) return <p className="muted tool-pane-hint">Pick a project to browse its files.</p>

  // A detour is any tree that is not "the active project, ordinary side": say
  // whose files these are and give it exactly one way back.
  const detour = !!reveal && (inspecting || reveal.projectSlug !== project?.slug)
  return <>
    {detour && <div className="tool-files-detour">
      <div>
        <strong>{inspecting ? 'Inspecting' : 'Browsing'} {name}</strong>
        <span className="muted mono">{reveal?.path || '/'}</span>
      </div>
      <button type="button" className="ghost-button" onClick={onLeaveReveal}>
        {inspecting ? 'Close inspection' : `Back to ${project?.name || 'my project'}`}
      </button>
    </div>}
    <WorkspaceTree
      key={`${slug}:${inspecting ? 'container' : 'virtual'}`}
      fs={fs}
      title={name}
      className="tool-files"
      activePath={reveal?.path ?? null}
      activePathKind={reveal?.pathKind}
      onOpenFile={!inspecting && onOpenFile
        ? (path, target) => onOpenFile(slug, path, target)
        : undefined}
    />
  </>
}
