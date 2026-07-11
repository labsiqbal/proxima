import React from 'react'
import type { ChatSession, Profile, Project } from '../../types'
import { WorkspaceTree } from '../files/WorkspaceTree'
import { projectFs } from '../../api/fsAdapter'

export function RightRail({ token, activeProject, onOpenFile }: { token: string; activeProfile: Profile | null; activeProject: Project | null; activeSession: ChatSession | null; projects: Project[]; onOpenFile?: (slug: string, path: string) => void }) {
  const fs = React.useMemo(() => activeProject ? projectFs(token, activeProject.slug) : null, [token, activeProject?.slug])
  if (!fs || !activeProject) return <aside className="right-rail"><div className="rail-card"><p className="muted">Select a project to browse files.</p></div></aside>
  // Peek-only: clicking a file opens it in the main Files editor, not inline here.
  return <WorkspaceTree fs={fs} title={activeProject.name} className="right-rail" onOpenFile={path => onOpenFile?.(activeProject.slug, path)} />
}
