import React from 'react'
import type { Project } from '../../types'
import { renameProject } from '../../api/projects'
import { promptDialog } from '../ui/Dialog'
import { IconChevronRight, IconPencil } from './icons'

/** First letter or digit of a project name, for the rail's compact tile.
 *
 * Walks code points rather than UTF-16 units so an astral first character is
 * never halved, and skips anything that is not a letter or digit so a name that
 * opens with "(", "@" or an emoji still shows a readable glyph. Falls back to a
 * neutral dot: with no project name there is nothing honest to abbreviate. */
export function firstLetter(name: string | null | undefined): string {
  const glyph = Array.from(name || '').find(character => /\p{L}|\p{N}/u.test(character))
  return glyph ? glyph.toUpperCase() : '·'
}

/** Global active-project control for the shell top bar (text + chevron, not icon-only). */
export function ProjectSwitcher({
  projects,
  activeProject,
  onSelectProject,
  token,
  onProjectRenamed,
  className,
  compact = false,
  locked = false,
  lockedReason = 'Project is locked while this view is open',
}: {
  projects: Project[]
  activeProject: Project | null
  onSelectProject: (project: Project) => void
  /** When set with onProjectRenamed, each row offers Rename (Settings → Projects remains available too). */
  token?: string
  onProjectRenamed?: (project: Project) => void
  className?: string
  /** Slightly denser trigger for the mobile topbar. */
  compact?: boolean
  /** Deep surfaces lock the switcher (task, graph editor, archive record, design canvas, …). */
  locked?: boolean
  lockedReason?: string
}) {
  const [open, setOpen] = React.useState(false)
  const [busySlug, setBusySlug] = React.useState<string | null>(null)
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
  // Compact form for the collapsed rail (#153): one identifying letter instead
  // of a name truncated to a meaningless "m…". Only a real project earns a
  // letter - "Select project" would render a misleading "S" - so the empty
  // state keeps a neutral dot and the label lives on the title/popover.
  const initial = firstLetter(activeProject?.name)
  const disabled = projects.length === 0 || locked
  const title = locked ? lockedReason : label
  const canRename = Boolean(token && onProjectRenamed)

  async function rename(project: Project) {
    if (!token || !onProjectRenamed || busySlug) return
    setOpen(false)
    const next = await promptDialog({
      title: `Rename “${project.name}”`,
      label: 'New name',
      defaultValue: project.name,
      confirmLabel: 'Rename',
    })
    if (next === null) return
    const name = next.trim()
    if (!name || name === project.name) return
    setBusySlug(project.slug)
    try {
      const renamed = await renameProject(token, project.slug, name)
      onProjectRenamed(renamed)
    } catch {
      // Switcher has no error bar; Settings → Projects remains the full manage surface.
    } finally {
      setBusySlug(null)
    }
  }

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
        <span className="project-switcher-initial" aria-hidden="true">{initial}</span>
        <span className="project-switcher-name">{label}</span>
        <span className="project-switcher-caret" aria-hidden="true">
          <IconChevronRight size={12} />
        </span>
      </button>
      {open && !disabled && (
        <div className="project-switcher-menu" id={menuId} role="listbox" aria-label="Projects">
          {projects.map(project => {
            const selected = project.slug === activeProject?.slug
            const renaming = busySlug === project.slug
            return (
              <div key={project.slug} className={`project-switcher-row ${selected ? 'active' : ''}`}>
                <button
                  type="button"
                  role="option"
                  aria-selected={selected}
                  className="project-switcher-item"
                  disabled={!!busySlug}
                  onClick={() => {
                    onSelectProject(project)
                    setOpen(false)
                  }}
                >
                  <strong>{project.name}</strong>
                  <small>{project.slug}</small>
                </button>
                {canRename && (
                  <button
                    type="button"
                    className="project-switcher-rename"
                    disabled={!!busySlug}
                    aria-label={`Rename project ${project.name}`}
                    title="Rename"
                    onClick={() => void rename(project)}
                  >
                    {renaming ? '…' : <IconPencil size={13} />}
                  </button>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
