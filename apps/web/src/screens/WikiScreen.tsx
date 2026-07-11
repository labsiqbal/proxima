import React from 'react'
import type { Project } from '../types'
import { WorkspaceTree } from '../components/files/WorkspaceTree'
import { projectFs } from '../api/fsAdapter'
import { type WikiNoteRaw } from '../api/wiki'
import { projectWikiAll } from '../api/files'
import { buildWikiModel } from '../components/wiki/wikiGraph'
import { WikiSearch } from '../components/wiki/WikiSearch'
import { IconFolder, IconChevronRight } from '../components/shell/icons'

const cleanName = (name: string) => name.replace(/\s*\(private\)\s*$/i, '')

const WikiGraph = React.lazy(() => import('../components/wiki/WikiGraph').then(m => ({ default: m.WikiGraph })))
const WikiNote = React.lazy(() => import('../components/wiki/WikiNote').then(m => ({ default: m.WikiNote })))

type Tab = 'files' | 'graph' | 'search'

export function WikiScreen({ token, projects, activeProject, onActiveProject }: { token: string; projects: Project[]; activeProject: Project | null; onActiveProject: (p: Project) => void }) {
  // Wiki is project-scoped: a private project is already your personal space, so
  // there is no separate "My Wiki". The selected project is the app-wide
  // activeProject (shared with chat/tasks/workflows), NOT screen-local state —
  // otherwise it would reset on every menu switch. Fall back to the first project
  // only when there is no active one yet.
  const [tab, setTab] = React.useState<Tab>('files')
  const [openNote, setOpenNote] = React.useState<string | null>(null)
  const [notes, setNotes] = React.useState<WikiNoteRaw[]>([])
  const [version, setVersion] = React.useState(0)
  const [pickerOpen, setPickerOpen] = React.useState(false)
  const loadSeq = React.useRef(0)
  const mountedRef = React.useRef(true)

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      loadSeq.current += 1
    }
  }, [])

  React.useEffect(() => {
    if (!pickerOpen) return
    const close = () => setPickerOpen(false)
    window.addEventListener('click', close)
    return () => window.removeEventListener('click', close)
  }, [pickerOpen])

  const selected = projects.find(p => p.slug === activeProject?.slug) || projects[0] || null
  const fs = React.useMemo(() => (selected ? projectFs(token, selected.slug, 'wiki') : null), [token, selected?.slug])
  const loadAll = React.useCallback(
    () => (selected ? projectWikiAll(token, selected.slug) : Promise.resolve({ notes: [] as WikiNoteRaw[] })),
    [token, selected?.slug],
  )

  React.useEffect(() => { setOpenNote(null) }, [fs])
  React.useEffect(() => {
    const seq = ++loadSeq.current
    loadAll()
      .then(b => {
        if (mountedRef.current && seq === loadSeq.current) setNotes(b.notes)
      })
      .catch(() => {
        if (mountedRef.current && seq === loadSeq.current) setNotes([])
      })
  }, [loadAll, version])

  // Exclude the auto-generated meta files: log.md (turn journal, noise) and
  // index.md (catalog — redundant with the project root hub in the graph).
  const mdNotes = React.useMemo(() => notes.filter(n => /\.(md|markdown)$/i.test(n.path) && !/(^|\/)(log|index)\.md$/i.test(n.path)), [notes])
  const model = React.useMemo(() => buildWikiModel(mdNotes, selected ? cleanName(selected.name) : undefined), [mdNotes, selected])
  const isMd = (name: string) => /\.(md|markdown)$/i.test(name)
  const title = selected ? `${selected.name} · wiki` : 'Wiki'
  const reload = () => setVersion(v => v + 1)
  const openInFiles = (p: string) => { setOpenNote(p); setTab('files') }

  if (!selected) {
    return <section className="wiki-view"><div className="wiki-placeholder"><p className="muted">No project yet. Create a project to start its wiki — each project (private by default) is your personal space.</p></div></section>
  }

  return <section className="wiki-view">
    <div className="wiki-switch">
      <div className="wiki-picker" onClick={e => e.stopPropagation()}>
        <button className="wiki-picker-btn" onClick={() => setPickerOpen(o => !o)}>
          <span className="wp-ico"><IconFolder size={16} /></span>
          <span className="wp-label">{cleanName(selected.name)}</span>
          <span className="wp-caret"><IconChevronRight size={14} /></span>
        </button>
        {pickerOpen && <div className="wiki-picker-menu">
          <div className="wp-group">Project wikis</div>
          {projects.map(p => <button key={p.slug} className={`wp-item ${selected.slug === p.slug ? 'active' : ''}`} onClick={() => { onActiveProject(p); setPickerOpen(false) }}>
            <IconFolder size={16} /><span className="wp-label">{cleanName(p.name)}</span>
          </button>)}
        </div>}
      </div>
      <div className="seg tabs">
        <button className={tab === 'files' ? 'active' : ''} onClick={() => setTab('files')}>Files</button>
        <button className={tab === 'graph' ? 'active' : ''} onClick={() => setTab('graph')}>Graph</button>
        <button className={tab === 'search' ? 'active' : ''} onClick={() => setTab('search')}>Search</button>
      </div>
    </div>

    {tab === 'files' && fs && <div className="wiki-files">
      <WorkspaceTree key={title} fs={fs} title={title} className="wiki-tree" onOpenFile={setOpenNote} onChange={reload} activePath={openNote} fileFilter={isMd} defaultExt="md" />
      <div className="wiki-main">
        {openNote
          ? <React.Suspense fallback={<div className="wiki-pane-msg muted">Loading note…</div>}><WikiNote fs={fs} path={openNote} backlinks={model.backlinks[openNote] || []} resolve={model.resolve} onOpenNote={openInFiles} onClose={() => setOpenNote(null)} onSaved={reload} /></React.Suspense>
          : <div className="wiki-placeholder"><p className="muted">Select or create a note. Link notes with <code>[[Note]]</code> — backlinks &amp; graph update automatically.</p></div>}
      </div>
    </div>}

    {tab === 'graph' && <React.Suspense fallback={<div className="wiki-pane-msg muted">Loading graph…</div>}><WikiGraph nodes={model.nodes} links={model.links} activePath={openNote} onOpen={openInFiles} /></React.Suspense>}

    {tab === 'search' && <WikiSearch notes={mdNotes} onOpen={openInFiles} />}
  </section>
}
