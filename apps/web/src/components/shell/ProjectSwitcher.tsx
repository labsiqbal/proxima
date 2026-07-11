import React from 'react'
import type { Project } from '../../types'
import { IconFolder, IconChevronRight } from './icons'

const clean = (n: string) => n.replace(/\s*\(private\)\s*$/i, '')

// Single place to see & switch the active project.
export function ProjectSwitcher({ projects, activeProject, onSelect }: { projects: Project[]; activeProject: Project | null; onSelect: (p: Project) => void }) {
  const [open, setOpen] = React.useState(false)
  React.useEffect(() => {
    if (!open) return
    const close = () => setOpen(false)
    window.addEventListener('click', close)
    return () => window.removeEventListener('click', close)
  }, [open])

  return <div className="wiki-picker proj-switch" onClick={e => e.stopPropagation()}>
    <button className="wiki-picker-btn" onClick={() => setOpen(o => !o)}>
      <span className="wp-ico"><IconFolder size={16} /></span>
      <span className="wp-label">{activeProject ? clean(activeProject.name) : (projects.length ? 'Select project' : 'No projects yet')}</span>
      <span className="wp-caret"><IconChevronRight size={14} /></span>
    </button>
    {open && <div className="wiki-picker-menu">
      {projects.length === 0 && <div className="wp-group">No projects yet</div>}
      {projects.map(p => <button key={p.slug} className={`wp-item ${activeProject?.slug === p.slug ? 'active' : ''}`} onClick={() => { onSelect(p); setOpen(false) }}>
        <IconFolder size={16} /><span className="wp-label">{clean(p.name)}</span>
      </button>)}
    </div>}
  </div>
}
