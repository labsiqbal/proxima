import React from 'react'
import type { Project } from '../../types'
import { projectFs } from '../../api/fsAdapter'
import { WorkspaceTree } from '../files/WorkspaceTree'
import { IconClose, IconFile, IconGear, IconMonitor, IconTerminal } from './icons'

const TerminalTabs = React.lazy(() => import('../terminal/TerminalTabs').then(m => ({ default: m.TerminalTabs })))
const AppRunner = React.lazy(() => import('../files/AppRunner').then(m => ({ default: m.AppRunner })))

// Terminal, Files, and Preview are tools, not destinations: a slim icon rail on
// the right opens each one as an overlay panel above the current screen, so the
// plan/chat you were reading stays where it was. All three are scoped to the
// active project, in any context.
export type Tool = 'terminal' | 'files' | 'preview'
const TOOLS: { id: Tool; label: string; Icon: React.ComponentType<{ size?: number }> }[] = [
  { id: 'terminal', label: 'Terminal', Icon: IconTerminal },
  { id: 'files', label: 'Files', Icon: IconFile },
  { id: 'preview', label: 'Preview', Icon: IconMonitor },
]

function PaneFallback({ label }: { label: string }) {
  return <p className="muted tool-pane-hint">{label}</p>
}

export function ToolDock({ token, project, onOpenSettings }: {
  token: string
  project: Project | null
  onOpenSettings: () => void
}) {
  const [open, setOpen] = React.useState<Tool | null>(null)
  // Latch: once Terminal or Files has been opened it stays mounted (hidden when
  // closed) — unmounting would SIGHUP every shell and drop unsaved file edits.
  // Preview is NOT latched: its dev server is a backend process that survives on
  // its own, and an unmounted AppRunner stops status-polling for free.
  const visited = React.useRef(new Set<Tool>())
  if (open && open !== 'preview') visited.current.add(open)
  const toggle = (tool: Tool) => setOpen(current => (current === tool ? null : tool))

  React.useEffect(() => {
    if (!open) return
    const dismiss = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(null) }
    window.addEventListener('keydown', dismiss)
    return () => window.removeEventListener('keydown', dismiss)
  }, [open])

  // Tell the shell a tool panel is open so main content can reserve space for
  // it. Without this the overlay covers right-edge primary actions (e.g.
  // Design Studio's "Generate →") while Files/Terminal/Preview is open.
  React.useEffect(() => {
    const shell = document.querySelector('.app-shell')
    if (!shell) return
    shell.classList.toggle('tool-open', open != null)
    return () => { shell.classList.remove('tool-open') }
  }, [open])

  // "Reveal in Files" (Archive record actions): the dock owns the Files panel,
  // so far-away screens ask for it with an event instead of prop-drilling
  // through the whole shell. detail.path highlights the file in the tree.
  const [revealPath, setRevealPath] = React.useState<string | null>(null)
  React.useEffect(() => {
    const onReveal = (event: Event) => {
      const path = (event as CustomEvent).detail?.path
      if (typeof path === 'string') { setRevealPath(path); setOpen('files') }
    }
    window.addEventListener('proxima:reveal-file', onReveal)
    return () => window.removeEventListener('proxima:reveal-file', onReveal)
  }, [])

  const slug = project?.slug
  const fs = React.useMemo(() => (slug ? projectFs(token, slug) : null), [token, slug])

  const toolButton = (tool: typeof TOOLS[number], where: 'rail' | 'tab') => (
    <button
      key={tool.id}
      className={`${where === 'rail' ? 'tool-rail-btn' : 'tool-panel-tab'} ${open === tool.id ? 'active' : ''}`}
      title={tool.label}
      aria-label={tool.label}
      aria-pressed={open === tool.id}
      onClick={() => (where === 'rail' ? toggle(tool.id) : setOpen(tool.id))}
    ><tool.Icon size={17} />{where === 'tab' && <span>{tool.label}</span>}</button>
  )

  const pane = (tool: Tool, content: React.ReactNode) => (
    (open === tool || visited.current.has(tool)) && (
      <div className="tool-pane" key={tool} style={{ display: open === tool ? 'flex' : 'none' }}>{content}</div>
    )
  )

  return <>
    <aside className="tool-rail" aria-label="Tools">
      {TOOLS.map(tool => toolButton(tool, 'rail'))}
      <span className="tool-rail-sep" aria-hidden="true" />
      <button className="tool-rail-btn tool-rail-gear" title="Settings" aria-label="Settings" onClick={onOpenSettings}><IconGear size={17} /></button>
    </aside>
    <div className={`tool-panel ${open ? 'open' : ''}`} aria-label="Tool panel" aria-hidden={!open}>
      <div className="tool-panel-head">
        <div className="tool-panel-tabs">{TOOLS.map(tool => toolButton(tool, 'tab'))}</div>
        <button className="icon-button" onClick={() => setOpen(null)} aria-label="Close tool panel"><IconClose size={16} /></button>
      </div>
      <div className="tool-panel-body">
        {pane('terminal',
          <React.Suspense fallback={<PaneFallback label="Loading terminal…" />}>
            <TerminalTabs token={token} projectSlug={slug} />
          </React.Suspense>)}
        {pane('files',
          fs && project
            ? <WorkspaceTree fs={fs} title={project.name} className="tool-files" activePath={revealPath} />
            : <PaneFallback label="Pick a project to browse its files." />)}
        {open === 'preview' && <div className="tool-pane" style={{ display: 'flex' }}>
          {slug
            ? <React.Suspense fallback={<PaneFallback label="Loading preview…" />}>
                <AppRunner token={token} slug={slug} onClose={() => setOpen(null)} />
              </React.Suspense>
            : <PaneFallback label="Pick a project to run and preview its app." />}
        </div>}
      </div>
    </div>
  </>
}
