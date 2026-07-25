import React from 'react'
import type { Project } from '../../types'
import { IconChevronRight } from './icons'

/** Global active-project control for the shell top bar (text + chevron, not icon-only). */
export function ProjectSwitcher({
  projects,
  activeProject,
  onSelectProject,
  className,
  compact = false,
  locked = false,
  lockedReason = 'Project is locked while this view is open',
}: {
  projects: Project[]
  activeProject: Project | null
  onSelectProject: (project: Project) => void
  className?: string
  /** Slightly denser trigger for the mobile topbar. */
  compact?: boolean
  /** Deep surfaces lock the switcher (task, graph editor, archive record, design canvas, …). */
  locked?: boolean
  lockedReason?: string
}) {
  const [open, setOpen] = React.useState(false)
  const root = React.useRef<HTMLDivElement>(null)
  const menuId = React.useId()

  React.useEffect(() => {
    if (!open) return
    const dismiss = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false)
    }
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    window.addEventListener('mousedown', dismiss)
    window.addEventListener('keydown', key)
    return () => {
      window.removeEventListener('mousedown', dismiss)
      window.removeEventListener('keydown', key)
    }
  }, [open])

  // Close the menu if the surface becomes deep-locked while open.
  React.useEffect(() => {
    if (locked) setOpen(false)
  }, [locked])

  const label = activeProject?.name || (projects.length ? 'Select project' : 'No projects')
  const disabled = projects.length === 0 || locked
  const title = locked ? lockedReason : label

  return (
    <div className={`project-switcher ${compact ? 'compact' : ''} ${locked ? 'is-locked' : ''} ${className || ''}`} ref={root}>
      <button
        type="button"
        className={`project-switcher-btn ${open ? 'active' : ''}`}
        disabled={disabled}
        onClick={() => setOpen(value => !value)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={locked ? `Active project: ${label} (locked)` : `Active project: ${label}`}
        title={title}
      >
        <span className="project-switcher-name">{label}</span>
        <span className="project-switcher-caret" aria-hidden="true">
          <IconChevronRight size={12} />
        </span>
      </button>
      {open && !disabled && (
        <div className="project-switcher-menu" id={menuId} role="listbox" aria-label="Projects">
          {projects.map(project => {
            const selected = project.slug === activeProject?.slug
            return (
              <button
                type="button"
                role="option"
                aria-selected={selected}
                key={project.slug}
                className={`project-switcher-item ${selected ? 'active' : ''}`}
                onClick={() => {
                  onSelectProject(project)
                  setOpen(false)
                }}
              >
                <strong>{project.name}</strong>
                <small>{project.slug}</small>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
