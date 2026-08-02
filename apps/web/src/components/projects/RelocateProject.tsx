import React from 'react'
import { FolderLinker } from './FolderLinker'
import type { Project } from '../../types'

// Relocate/rebind (prune C6, #141): a project whose folder was moved or renamed
// on disk is re-pinned here, through the same onboarding folder picker. Nothing
// is moved or copied - only the project's stored address changes - and the
// server confirms the folder's own identity before accepting it.
export function RelocateProjectModal({ token, project, onClose, onRelocated }: {
  token: string
  project: Project
  onClose: () => void
  onRelocated: (project: Project) => Promise<void>
}) {
  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return <div className="modal-scrim" onClick={onClose}>
    <div
      className="modal-card project-relocate-card"
      onClick={event => event.stopPropagation()}
      role="dialog"
      aria-modal="true"
      aria-label={`Find the folder for ${project.name}`}
    >
      <h3>Find “{project.name}”</h3>
      <p className="muted">{project.location.message}</p>
      <p className="relocate-stored">
        <span className="muted">Last known location</span>
        <code>{project.location.path}</code>
      </p>
      <FolderLinker
        token={token}
        rebind={{ slug: project.slug, name: project.name }}
        onLinked={onRelocated}
      />
      <div className="confirm-actions">
        <button type="button" className="ghost-button" onClick={onClose}>Cancel</button>
      </div>
    </div>
  </div>
}
