import React from 'react'
import type { FileTarget, Project } from '../../types'
import { parseRevealTarget, REVEAL_FILE_EVENT, type RevealTarget } from '../../lib/revealFile'
import { OPEN_RUN_PREVIEW_EVENT } from '../../lib/runPreview'
import { DockFileBrowser } from '../files/DockFileBrowser'
import { IconClose, IconFile, IconGear, IconMonitor, IconTerminal } from './icons'

const TerminalTabs = React.lazy(() => import('../terminal/TerminalTabs').then(m => ({ default: m.TerminalTabs })))
const AppRunner = React.lazy(() => import('../files/AppRunner').then(m => ({ default: m.AppRunner })))

// Terminal, Files, and Preview are tools, not destinations: a slim icon rail on
// the right opens each one as an overlay panel above the current screen, so the
// plan/chat you were reading stays where it was. All three are scoped to the
// active project, in any context.
//
// Browsing spent one release as a destination (ADR-0040) and came back here in
// #145: the destination is Artifacts (ADR-0043), a gallery of what the project
// produced, while "where is that file on disk" is the utility you open next to
// your work. The dock owns when Files is showing and where it is pointed;
// `DockFileBrowser` owns the tree itself.
export type Tool = 'terminal' | 'files' | 'preview'

const TOOLS: { id: Tool; label: string; Icon: React.ComponentType<{ size?: number }> }[] = [
  { id: 'terminal', label: 'Terminal', Icon: IconTerminal },
  { id: 'files', label: 'Files', Icon: IconFile },
  { id: 'preview', label: 'Preview', Icon: IconMonitor },
]

function PaneFallback({ label }: { label: string }) {
  return <p className="muted tool-pane-hint">{label}</p>
}

export function ToolDock({ token, project, projects = [], available = true, onOpenSettings, onOpenChange, onOpenFile, onOpenAppViewport }: {
  token: string
  project: Project | null
  /** Named so a reveal into another Container can title its tree properly. */
  projects?: Project[]
  available?: boolean
  onOpenSettings: () => void
  onOpenChange?: (open: boolean) => void
  /**
   * The handoff seam: opening a file from the dock is a main-window event, not a
   * panel-sized editor. The shell hands the path to Artifacts, which opens a
   * document in the editor and anything else in its inline viewer (#146).
   */
  onOpenFile?: (slug: string, path: string, target?: FileTarget) => void
  /**
   * The same shape for the running app (#147): the Preview tool keeps the Run
   * controls and asks the shell to put the app's viewport in the main window,
   * because opening a destination is App state, not the dock's.
   */
  onOpenAppViewport?: (slug: string) => void
}) {
  const [open, setOpen] = React.useState<Tool | null>(null)
  const [reveal, setReveal] = React.useState<RevealTarget | null>(null)
  // Latch: once Terminal or Files has been opened it stays mounted (hidden when
  // closed) — unmounting would SIGHUP every shell and drop tree state.
  // Preview is NOT latched: its dev server is a backend process that survives on
  // its own, and an unmounted AppRunner stops status-polling for free.
  const visited = React.useRef(new Set<Tool>())
  if (open && open !== 'preview') visited.current.add(open)
  const closePanel = React.useCallback(() => {
    setOpen(null)
    setReveal(null)
  }, [])
  const selectTool = (tool: Tool, toggle: boolean) => {
    // Picking a tool by hand is a fresh start: drop the reveal so Files returns
    // to the active project instead of staying parked on someone else's tree.
    setReveal(null)
    setOpen(current => toggle && current === tool ? null : tool)
  }

  // Escape closes the topmost overlay, not everything at once: a modal raised
  // over the dock (a confirm dialog, the active-preview consent) owns the key
  // until it is gone, otherwise one press dismisses both and loses the owner's
  // place. A handed-off file is not one of those - it opens behind this panel in
  // the main window (#146), so Escape still closes the panel above it.
  React.useEffect(() => {
    if (!open) return
    const dismiss = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (document.querySelector('[aria-modal="true"]')) return
      closePanel()
    }
    window.addEventListener('keydown', dismiss)
    return () => window.removeEventListener('keydown', dismiss)
  }, [closePanel, open])
  React.useEffect(() => {
    if (!available) closePanel()
  }, [available, closePanel])

  // Tell the shell a tool panel is open so main content can reserve space for
  // it. Without this the overlay covers right-edge primary actions (e.g.
  // Design Studio's "Generate →") while Terminal/Files/Preview is open.
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

  const slug = project?.slug
  const activeSlugRef = React.useRef(slug)
  activeSlugRef.current = slug
  // Switching the shell project ends any reveal: the owner moved on.
  React.useEffect(() => { setReveal(null) }, [slug])

  // "Reveal in Files" and Ops-migration recovery raise a window event rather
  // than prop-drilling through the whole shell: the dock owns the only tree in
  // the app, so far-away surfaces just name a path and let it answer. While
  // Project tools are suppressed there is no tree to point, so the reveal is
  // dropped rather than queued behind a hidden panel.
  React.useEffect(() => {
    const onReveal = (event: Event) => {
      if (!available) return
      const target = parseRevealTarget(event, activeSlugRef.current)
      if (!target) return
      setReveal(target)
      setOpen('files')
    }
    window.addEventListener(REVEAL_FILE_EVENT, onReveal)
    return () => window.removeEventListener(REVEAL_FILE_EVENT, onReveal)
  }, [available])

  // The main-window viewport's way back to the controls that start and stop the
  // app (#147). Same reasoning as the reveal above: the dock owns the tool, a
  // far-away surface only asks for it, and a request that arrives while Project
  // tools are suppressed is dropped rather than queued.
  React.useEffect(() => {
    const onRunControls = () => { if (available) setOpen('preview') }
    window.addEventListener(OPEN_RUN_PREVIEW_EVENT, onRunControls)
    return () => window.removeEventListener(OPEN_RUN_PREVIEW_EVENT, onRunControls)
  }, [available])

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
          <DockFileBrowser
            token={token}
            project={project}
            projects={projects}
            reveal={reveal}
            onLeaveReveal={() => setReveal(null)}
            onOpenFile={onOpenFile}
          />)}
        {open === 'preview' && <div className="tool-pane" style={{ display: 'flex' }}>
          {slug
            ? <React.Suspense fallback={<PaneFallback label="Loading preview…" />}>
                <AppRunner token={token} slug={slug} onClose={closePanel} onOpenViewport={onOpenAppViewport && (() => onOpenAppViewport(slug))} />
              </React.Suspense>
            : <PaneFallback label="Pick a project to run and preview its app." />}
        </div>}
      </div>
    </div>
  </>
}
