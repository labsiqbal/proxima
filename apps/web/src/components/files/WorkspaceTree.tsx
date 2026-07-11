import React from 'react'
import type { FileEntry } from '../../types'
import type { FsAdapter } from '../../api/fsAdapter'
import { IconFile, IconFolder, IconChevronRight, IconFilePlus, IconFolderPlus } from '../shell/icons'
import { confirmDialog } from '../ui/Dialog'

// Lazy-load the CodeMirror editor so its chunk only loads when a file is opened.
const FileEditor = React.lazy(() => import('./FileEditor').then(m => ({ default: m.FileEditor })))

type Ctl = {
  fs: FsAdapter; refreshKey: number
  fileFilter?: (name: string) => boolean
  busy: boolean
  expanded: Set<string>; toggle: (p: string) => void
  creating: { dir: string; type: 'file' | 'dir' } | null
  renaming: string | null
  activePath: string | null
  openFile: (p: string) => void
  openMenu: (e: React.MouseEvent, path: string | null, isDir: boolean) => void
  submitCreate: (name: string) => void
  cancelCreate: () => void
  submitRename: (path: string, name: string) => void
  cancelRename: () => void
}

const base = (p: string) => p.split('/').pop() || p

function InlineInput({ initial, icon, depth, onSubmit, onCancel }: { initial: string; icon: React.ReactNode; depth: number; onSubmit: (v: string) => void; onCancel: () => void }) {
  const [v, setV] = React.useState(initial)
  const done = React.useRef(false)
  const finish = (commit: boolean) => {
    if (done.current) return
    done.current = true
    if (commit && v.trim() && v.trim() !== initial) onSubmit(v.trim()); else onCancel()
  }
  return <div className="tree-row inline" style={{ paddingLeft: 8 + depth * 12 }}>
    <span className="tree-ico">{icon}</span>
    <input autoFocus className="tree-input" value={v} onChange={e => setV(e.target.value)}
      onKeyDown={e => { if (e.key === 'Enter') finish(true); else if (e.key === 'Escape') finish(false) }}
      onBlur={() => finish(true)} />
  </div>
}

function Level({ dir, depth, t }: { dir: string; depth: number; t: Ctl }) {
  const [entries, setEntries] = React.useState<FileEntry[]>([])
  const [loaded, setLoaded] = React.useState(false)
  const loadSeq = React.useRef(0)
  React.useEffect(() => {
    const seq = ++loadSeq.current
    t.fs.list(dir)
      .then(b => {
        if (seq !== loadSeq.current) return
        setEntries(b.entries)
        setLoaded(true)
      })
      .catch(() => {
        if (seq === loadSeq.current) setLoaded(true)
      })
    return () => { if (seq === loadSeq.current) loadSeq.current += 1 }
  }, [t.fs, dir, t.refreshKey])

  return <div className="tree-level">
    {t.creating && t.creating.dir === dir && <InlineInput initial="" depth={depth} icon={t.creating.type === 'dir' ? <IconFolder size={15} /> : <IconFile size={15} />} onSubmit={t.submitCreate} onCancel={t.cancelCreate} />}
    {entries.filter(entry => entry.type === 'dir' || !t.fileFilter || t.fileFilter(entry.name)).map(entry => {
      const path = dir ? `${dir}/${entry.name}` : entry.name
      if (t.renaming === path) return <InlineInput key={path} initial={entry.name} depth={depth} icon={entry.type === 'dir' ? <IconFolder size={15} /> : <IconFile size={15} />} onSubmit={n => t.submitRename(path, n)} onCancel={t.cancelRename} />
      if (entry.type === 'dir') {
        const open = t.expanded.has(path)
        return <div key={path}>
          <button className="tree-row dir" style={{ paddingLeft: 8 + depth * 12 }} onClick={() => t.toggle(path)} onContextMenu={e => t.openMenu(e, path, true)}>
            <span className={`tree-chevron ${open ? 'open' : ''}`}><IconChevronRight size={13} /></span>
            <span className="tree-ico"><IconFolder size={15} /></span>
            <span className="tree-name">{entry.name}</span>
          </button>
          {open && <Level dir={path} depth={depth + 1} t={t} />}
        </div>
      }
      return <button key={path} className={`tree-row file ${t.activePath === path ? 'active' : ''}`} style={{ paddingLeft: 8 + depth * 12 }} onClick={() => t.openFile(path)} onContextMenu={e => t.openMenu(e, path, false)}>
        <span className="tree-ico"><IconFile size={15} /></span>
        <span className="tree-name">{entry.name}</span>
      </button>
    })}
    {loaded && entries.length === 0 && depth === 0 && !t.creating && <p className="muted tree-empty">Empty · use + to add</p>}
  </div>
}

export function WorkspaceTree({ fs, title, className = 'right-rail', refreshSignal = 0, onOpenFile, onChange, activePath, fileFilter, defaultExt }: { fs: FsAdapter; title: string; className?: string; refreshSignal?: number; onOpenFile?: (path: string) => void; onChange?: () => void; activePath?: string | null; fileFilter?: (name: string) => boolean; defaultExt?: string }) {
  const [refreshKey, setRefreshKey] = React.useState(0)
  const [editing, setEditing] = React.useState<string | null>(null)
  const [treeError, setTreeError] = React.useState<string | null>(null)
  const [expanded, setExpanded] = React.useState<Set<string>>(new Set())
  const [creating, setCreating] = React.useState<{ dir: string; type: 'file' | 'dir' } | null>(null)
  const [renaming, setRenaming] = React.useState<string | null>(null)
  const [menu, setMenu] = React.useState<{ x: number; y: number; path: string | null; isDir: boolean } | null>(null)
  const [busy, setBusy] = React.useState(false)
  const mountedRef = React.useRef(true)
  const actionSeq = React.useRef(0)
  const refresh = () => setRefreshKey(k => k + 1)
  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      actionSeq.current += 1
    }
  }, [])

  // Reset view when the source (fs) or external refresh signal changes.
  React.useEffect(() => { actionSeq.current += 1; setBusy(false); setEditing(null); setExpanded(new Set()); setRefreshKey(k => k + 1) }, [fs])
  React.useEffect(() => { if (refreshSignal) setRefreshKey(k => k + 1) }, [refreshSignal])
  React.useEffect(() => {
    const onChange = () => setRefreshKey(k => k + 1)
    window.addEventListener('proxima:files-changed', onChange)
    return () => window.removeEventListener('proxima:files-changed', onChange)
  }, [])
  React.useEffect(() => {
    if (!menu) return
    const close = () => setMenu(null)
    window.addEventListener('click', close)
    window.addEventListener('scroll', close, true)
    return () => { window.removeEventListener('click', close); window.removeEventListener('scroll', close, true) }
  }, [menu])

  const guard = async (p: Promise<unknown>) => {
    if (busy) return false
    const seq = ++actionSeq.current
    setBusy(true); setTreeError(null)
    try {
      await p
      if (mountedRef.current && seq === actionSeq.current) {
        refresh()
        onChange?.()
      }
      return mountedRef.current && seq === actionSeq.current
    } catch (e) {
      if (mountedRef.current && seq === actionSeq.current) setTreeError(String(e))
      return false
    } finally {
      if (mountedRef.current && seq === actionSeq.current) setBusy(false)
    }
  }

  const openFile = (p: string) => { if (onOpenFile) onOpenFile(p); else setEditing(p) }

  function startCreate(dir: string, type: 'file' | 'dir') {
    if (busy) return
    setRenaming(null)
    if (dir) setExpanded(s => new Set(s).add(dir))
    setCreating({ dir, type })
  }
  async function submitCreate(name: string) {
    const c = creating; if (!c || busy) return
    setCreating(null)
    if (c.type === 'file' && defaultExt && !/\.[a-z0-9]+$/i.test(name)) name = `${name}.${defaultExt}`
    const full = c.dir ? `${c.dir}/${name}` : name
    if (c.type === 'dir') await guard(fs.mkdir(full))
    else if (await guard(fs.write(full, ''))) openFile(full)
  }
  async function submitRename(path: string, name: string) {
    if (busy) return
    setRenaming(null)
    const parent = path.split('/').slice(0, -1).join('/')
    const to = parent ? `${parent}/${name}` : name
    if (to === path) return
    if (await guard(fs.rename(path, to)) && editing === path) setEditing(to)
  }
  async function remove(path: string) {
    if (busy) return
    if (!(await confirmDialog({ title: `Delete "${base(path)}"?`, message: 'This cannot be undone.', confirmLabel: 'Delete', danger: true }))) return
    if (await guard(fs.remove(path)) && (editing === path || (editing && editing.startsWith(path + '/')))) setEditing(null)
  }

  const t: Ctl = {
    fs, refreshKey, fileFilter, busy, expanded,
    toggle: p => { if (!busy) setExpanded(s => { const n = new Set(s); if (n.has(p)) n.delete(p); else n.add(p); return n }) },
    creating, renaming, activePath: activePath !== undefined ? activePath : editing,
    openFile,
    openMenu: (e, path, isDir) => { e.preventDefault(); e.stopPropagation(); setMenu({ x: e.clientX, y: e.clientY, path, isDir }) },
    submitCreate, cancelCreate: () => setCreating(null),
    submitRename, cancelRename: () => setRenaming(null)
  }

  const menuDir = menu ? (menu.path == null ? '' : menu.isDir ? menu.path : menu.path.split('/').slice(0, -1).join('/')) : ''

  return <aside className={className}>
    <div className="tree-toolbar"><strong>{title}</strong><div className="tree-actions">
      <button title="New file" aria-label="New file" onClick={() => startCreate('', 'file')} disabled={busy}><IconFilePlus size={16} /></button>
      <button title="New folder" aria-label="New folder" onClick={() => startCreate('', 'dir')} disabled={busy}><IconFolderPlus size={16} /></button>
    </div></div>
    {treeError && <p className="tree-error">{treeError}</p>}
    <div className="tree-scroll" onContextMenu={e => { if (e.target === e.currentTarget) t.openMenu(e, null, true) }}><Level dir="" depth={0} t={t} /></div>
    {!onOpenFile && editing && <React.Suspense fallback={<div className="file-editor"><div className="file-editor-head"><strong>{base(editing)}</strong></div><p className="muted" style={{ padding: '10px' }}>Loading editor…</p></div>}><FileEditor fs={fs} path={editing} onClose={() => setEditing(null)} /></React.Suspense>}
    {menu && <div className="ctx-menu" style={{ top: menu.y, left: menu.x }} onClick={e => e.stopPropagation()}>
      <button onClick={() => { startCreate(menuDir, 'file'); setMenu(null) }} disabled={busy}>New File</button>
      <button onClick={() => { startCreate(menuDir, 'dir'); setMenu(null) }} disabled={busy}>New Folder</button>
      {menu.path != null && <><div className="ctx-sep" /><button onClick={() => { if (!busy) setRenaming(menu.path); setMenu(null) }} disabled={busy}>Rename</button><button className="danger" onClick={() => { const p = menu.path as string; setMenu(null); void remove(p) }} disabled={busy}>Delete</button></>}
    </div>}
  </aside>
}
