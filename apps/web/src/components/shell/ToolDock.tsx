import React from 'react'
import type { Project } from '../../types'
import { containerInspectionFs, projectFs } from '../../api/fsAdapter'
import type { FileRootSide } from '../../api/files'
import { WorkspaceTree } from '../files/WorkspaceTree'
import { IconClose, IconFile, IconGear, IconMonitor, IconTerminal } from './icons'

const TerminalTabs = React.lazy(() => import('../terminal/TerminalTabs').then(m => ({ default: m.TerminalTabs })))
const AppRunner = React.lazy(() => import('../files/AppRunner').then(m => ({ default: m.AppRunner })))

// Terminal, Files, and Preview are tools, not destinations: a slim icon rail on
// the right opens each one as an overlay panel above the current screen, so the
// plan/chat you were reading stays where it was. All three are scoped to the
// active project, in any context.
export type Tool = 'terminal' | 'files' | 'preview'
type RevealTarget = {
  path: string
  pathKind: 'root' | 'directory' | 'file'
  projectSlug?: string
  rootSide: FileRootSide
}
const TOOLS: { id: Tool; label: string; Icon: React.ComponentType<{ size?: number }> }[] = [
  { id: 'terminal', label: 'Terminal', Icon: IconTerminal },
  { id: 'files', label: 'Files', Icon: IconFile },
  { id: 'preview', label: 'Preview', Icon: IconMonitor },
]

function PaneFallback({ label }: { label: string }) {
  return <p className="muted tool-pane-hint">{label}</p>
}

export function ToolDock({ token, project, available = true, onOpenSettings, onOpenChange }: {
  token: string
  project: Project | null
  available?: boolean
  onOpenSettings: () => void
  onOpenChange?: (open: boolean) => void
}) {
  const [open, setOpen] = React.useState<Tool | null>(null)
  const [revealTarget, setRevealTarget] = React.useState<RevealTarget | null>(null)
  // Latch: once Terminal or Files has been opened it stays mounted (hidden when
  // closed) — unmounting would SIGHUP every shell and drop unsaved file edits.
  // Preview is NOT latched: its dev server is a backend process that survives on
  // its own, and an unmounted AppRunner stops status-polling for free.
  const visited = React.useRef(new Set<Tool>())
  if (open && open !== 'preview') visited.current.add(open)
  const closePanel = React.useCallback(() => {
    setOpen(null)
    setRevealTarget(null)
  }, [])
  const selectTool = (tool: Tool, toggle: boolean) => {
    setRevealTarget(null)
    setOpen(current => toggle && current === tool ? null : tool)
  }

  React.useEffect(() => {
    if (!open) return
    const dismiss = (event: KeyboardEvent) => { if (event.key === 'Escape') closePanel() }
    window.addEventListener('keydown', dismiss)
    return () => window.removeEventListener('keydown', dismiss)
  }, [closePanel, open])
  React.useEffect(() => {
    if (!available) closePanel()
  }, [available, closePanel])

  // Tell the shell a tool panel is open so main content can reserve space for
  // it. Without this the overlay covers right-edge primary actions (e.g.
  // Design Studio's "Generate →") while Files/Terminal/Preview is open.
  React.useEffect(() => {
    const shell = document.querySelector('.app-shell')
    if (!shell) return
    shell.classList.toggle('tool-open', open != null)
    onOpenChange?.(open != null)
    return () => {
      shell.classList.remove('tool-open')
      onOpenChange?.(false)
    }
  }, [onOpenChange, open])

  // "Reveal in Files" (Archive record actions): the dock owns the Files panel,
  // so far-away screens ask for it with an event instead of prop-drilling
  // through the whole shell. detail.path highlights the file in the tree.
  React.useEffect(() => {
    const onReveal = (event: Event) => {
      if (!available) return
      const detail = (event as CustomEvent).detail
      const path = detail?.path
      const projectSlug = detail?.projectSlug
      if (typeof path !== 'string') return
      if (
        typeof projectSlug === 'string'
        && projectSlug
        && projectSlug !== project?.slug
      ) return
      setRevealTarget({
        path,
        pathKind: detail?.pathKind === 'root' || detail?.pathKind === 'directory'
          ? detail.pathKind
          : 'file',
        projectSlug: typeof projectSlug === 'string' ? projectSlug : undefined,
        rootSide: detail?.rootSide === 'container' ? 'container' : 'virtual',
      })
      setOpen('files')
    }
    window.addEventListener('proxima:reveal-file', onReveal)
    return () => window.removeEventListener('proxima:reveal-file', onReveal)
  }, [available, project?.slug])

  const slug = project?.slug
  React.useEffect(() => {
    setRevealTarget(null)
  }, [slug])
  const applicableReveal = !!(
    slug
    && revealTarget
    && (!revealTarget.projectSlug || revealTarget.projectSlug === slug)
  )
  const recoveryInspection = !!(
    applicableReveal
    && revealTarget?.rootSide === 'container'
  )
  const revealPath = applicableReveal ? revealTarget?.path ?? null : null
  const revealPathKind = applicableReveal ? revealTarget?.pathKind : undefined
  const fs = React.useMemo(
    () => slug
      ? recoveryInspection
        ? containerInspectionFs(token, slug)
        : projectFs(token, slug)
      : null,
    [recoveryInspection, slug, token],
  )

  const toolButton = (tool: typeof TOOLS[number], where: 'rail' | 'tab') => (
    <button
      key={tool.id}
      className={`${where === 'rail' ? 'tool-rail-btn' : 'tool-panel-tab'} ${open === tool.id ? 'active' : ''}`}
      title={tool.label}
      aria-label={tool.label}
      aria-pressed={open === tool.id}
      onClick={() => selectTool(tool.id, where === 'rail')}
    ><tool.Icon size={17} />{where === 'tab' && <span>{tool.label}</span>}</button>
  )

  const pane = (tool: Tool, content: React.ReactNode) => (
    (open === tool || visited.current.has(tool)) && (
      <div className="tool-pane" key={tool} style={{ display: open === tool ? 'flex' : 'none' }}>{content}</div>
    )
  )

  return <>
    <aside className="tool-rail" aria-label="Tools" hidden={!available}>
      {TOOLS.map(tool => toolButton(tool, 'rail'))}
      <span className="tool-rail-sep" aria-hidden="true" />
      <button className="tool-rail-btn tool-rail-gear" title="Settings" aria-label="Settings" onClick={onOpenSettings}><IconGear size={17} /></button>
    </aside>
    <div className={`tool-panel ${open ? 'open' : ''}`} aria-label="Tool panel" aria-hidden={!open || !available} hidden={!available}>
      <div className="tool-panel-head">
        <div className="tool-panel-tabs">{TOOLS.map(tool => toolButton(tool, 'tab'))}</div>
        <button className="icon-button" onClick={closePanel} aria-label="Close tool panel"><IconClose size={16} /></button>
      </div>
      <div className="tool-panel-body">
        {pane('terminal',
          <React.Suspense fallback={<PaneFallback label="Loading terminal…" />}>
            <TerminalTabs token={token} projectSlug={slug} />
          </React.Suspense>)}
        {pane('files',
          fs && project
            ? <WorkspaceTree key={slug} fs={fs} title={project.name} className="tool-files" activePath={revealPath} activePathKind={revealPathKind} />
            : <PaneFallback label="Pick a project to browse its files." />)}
        {open === 'preview' && <div className="tool-pane" style={{ display: 'flex' }}>
          {slug
            ? <React.Suspense fallback={<PaneFallback label="Loading preview…" />}>
                <AppRunner token={token} slug={slug} onClose={closePanel} />
              </React.Suspense>
            : <PaneFallback label="Pick a project to run and preview its app." />}
        </div>}
      </div>
    </div>
  </>
}
