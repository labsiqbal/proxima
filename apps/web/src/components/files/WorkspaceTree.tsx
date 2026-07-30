import React from 'react'
import type { FileEntry, FileTarget } from '../../types'
import type { FsAdapter, ReadOnlyFsAdapter } from '../../api/fsAdapter'
import { retargetFile, type FileRef } from '../../api/files'
import { IconFile, IconFolder, IconChevronRight, IconFilePlus, IconFolderPlus } from '../shell/icons'
import { confirmDialog } from '../ui/Dialog'

// Lazy-load the CodeMirror editor so its chunk only loads when a file is opened.
const FileEditor = React.lazy(() => import('./FileEditor').then(m => ({ default: m.FileEditor })))

type Ctl = {
  fs: ReadOnlyFsAdapter
  writableFs: FsAdapter | null
  refreshKey: number
  fileFilter?: (name: string) => boolean
  busy: boolean
  expanded: Set<string>; toggle: (p: string) => void
  creating: { dir: string; target?: FileTarget; type: 'file' | 'dir' } | null
  renaming: { path: string; target?: FileTarget } | null
  activePath: string | null
  activePathKind: 'root' | 'directory' | 'file'
  openFile: (p: string, target?: FileTarget) => void
  openMenu: (e: React.MouseEvent, path: string | null, isDir: boolean, target?: FileTarget) => void
  submitCreate: (name: string) => void
  cancelCreate: () => void
  submitRename: (path: string, name: string) => void
  cancelRename: () => void
}

const base = (p: string) => p.split('/').pop() || p

function InlineInput({ initial, icon, depth, label, placeholder, onSubmit, onCancel }: {
  initial: string
  icon: React.ReactNode
  depth: number
  /** Accessible name - New file / New folder / Rename otherwise stay unlabeled. */
  label: string
  placeholder?: string
  onSubmit: (v: string) => void
  onCancel: () => void
}) {
  const [v, setV] = React.useState(initial)
  const done = React.useRef(false)
  const finish = (commit: boolean) => {
    if (done.current) return
    done.current = true
    if (commit && v.trim() && v.trim() !== initial) onSubmit(v.trim()); else onCancel()
  }
  return <div className="tree-row inline" style={{ paddingLeft: 8 + depth * 12 }}>
    <span className="tree-ico" aria-hidden="true">{icon}</span>
    <input
      autoFocus
      className="tree-input"
      value={v}
      aria-label={label}
      placeholder={placeholder}
      onChange={e => setV(e.target.value)}
      onKeyDown={e => { if (e.key === 'Enter') finish(true); else if (e.key === 'Escape') finish(false) }}
      onBlur={() => finish(true)}
    />
  </div>
}

function Level({ dir, target, depth, t }: { dir: string; target?: FileTarget; depth: number; t: Ctl }) {
  const [entries, setEntries] = React.useState<FileEntry[]>([])
  const [loaded, setLoaded] = React.useState(false)
  const loadSeq = React.useRef(0)
  React.useEffect(() => {
    const seq = ++loadSeq.current
    t.fs.list(target || dir)
      .then(b => {
        if (seq !== loadSeq.current) return
        setEntries(b.entries)
        setLoaded(true)
      })
      .catch(() => {
        if (seq === loadSeq.current) setLoaded(true)
      })
    return () => { if (seq === loadSeq.current) loadSeq.current += 1 }
  }, [t.fs, dir, target, t.refreshKey])

  return <div className="tree-level">
    {t.creating && t.creating.dir === dir && (
      <InlineInput
        initial=""
        depth={depth}
        icon={t.creating.type === 'dir' ? <IconFolder size={15} /> : <IconFile size={15} />}
        label={t.creating.type === 'dir' ? 'New folder name' : 'New file name'}
        placeholder={t.creating.type === 'dir' ? 'folder-name' : 'file-name'}
        onSubmit={t.submitCreate}
        onCancel={t.cancelCreate}
      />
    )}
    {entries.filter(entry => entry.type === 'dir' || !t.fileFilter || t.fileFilter(entry.name)).map(entry => {
      const path = dir ? `${dir}/${entry.name}` : entry.name
      if (t.renaming?.path === path) {
        return (
          <InlineInput
            key={path}
            initial={entry.name}
            depth={depth}
            icon={entry.type === 'dir' ? <IconFolder size={15} /> : <IconFile size={15} />}
            label={`Rename ${entry.name}`}
            onSubmit={n => t.submitRename(path, n)}
            onCancel={t.cancelRename}
          />
        )
      }
      if (entry.type === 'dir') {
        const open = t.expanded.has(path)
        return <div key={path}>
          <button
            data-path={path}
            className={`tree-row dir ${t.activePathKind === 'directory' && t.activePath === path ? 'active' : ''}`}
            style={{ paddingLeft: 8 + depth * 12 }}
            aria-expanded={open}
            onClick={() => t.toggle(path)}
            onContextMenu={t.writableFs ? e => t.openMenu(e, path, true, entry.target) : undefined}
          >
            <span className={`tree-chevron ${open ? 'open' : ''}`}><IconChevronRight size={13} /></span>
            <span className="tree-ico"><IconFolder size={15} /></span>
            <span className="tree-name">{entry.name}</span>
          </button>
          {open && <Level dir={path} target={entry.target} depth={depth + 1} t={t} />}
        </div>
      }
      return <button key={path} data-path={path} className={`tree-row file ${t.activePathKind === 'file' && t.activePath === path ? 'active' : ''}`} style={{ paddingLeft: 8 + depth * 12 }} onClick={() => t.openFile(path, entry.target)} onContextMenu={t.writableFs ? e => t.openMenu(e, path, false, entry.target) : undefined}>
        <span className="tree-ico"><IconFile size={15} /></span>
        <span className="tree-name">{entry.name}</span>
      </button>
    })}
    {loaded && entries.length === 0 && depth === 0 && !t.creating && <p className="muted tree-empty">{t.writableFs ? 'Empty · use + to add' : 'Empty'}</p>}
  </div>
}

function writableAdapter(fs: ReadOnlyFsAdapter | FsAdapter): FsAdapter | null {
  if (
    'write' in fs
    && 'mkdir' in fs
    && 'rename' in fs
    && 'remove' in fs
  ) {
    return fs
  }
  return null
}

export function WorkspaceTree({ fs, title, className = 'right-rail', refreshSignal = 0, onOpenFile, onChange, activePath, activePathKind = 'file', fileFilter, defaultExt }: { fs: ReadOnlyFsAdapter | FsAdapter; title: string; className?: string; refreshSignal?: number; onOpenFile?: (path: string, target?: FileTarget) => void; onChange?: () => void; activePath?: string | null; activePathKind?: 'root' | 'directory' | 'file'; fileFilter?: (name: string) => boolean; defaultExt?: string }) {
  const [refreshKey, setRefreshKey] = React.useState(0)
  const [editing, setEditing] = React.useState<{ path: string; target?: FileTarget } | null>(null)
  const [treeError, setTreeError] = React.useState<string | null>(null)
  const [expanded, setExpanded] = React.useState<Set<string>>(new Set())
  const [creating, setCreating] = React.useState<{ dir: string; target?: FileTarget; type: 'file' | 'dir' } | null>(null)
  const [renaming, setRenaming] = React.useState<{ path: string; target?: FileTarget } | null>(null)
  const [menu, setMenu] = React.useState<{ x: number; y: number; path: string | null; isDir: boolean; target?: FileTarget } | null>(null)
  const [busy, setBusy] = React.useState(false)
  const mountedRef = React.useRef(true)
  const actionSeq = React.useRef(0)
  const treeScrollRef = React.useRef<HTMLDivElement | null>(null)
  const writableFs = writableAdapter(fs)
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

  // "Reveal in Files" (and any external activePath): expand every ancestor so the
  // highlighted row is actually visible, not buried under a collapsed folder.
  // Close any inline editor too - it is absolute-positioned over the tree and
  // would hide the highlight Reveal is supposed to show. Archive already has
  // Open for content.
  // Re-run when `fs` changes too: the reset effect above clears `expanded`, and
  // a same-path reveal after a project switch would otherwise stay collapsed.
  React.useEffect(() => {
    if (!activePath) return
    setEditing(null)
    const parts = activePath.split('/').filter(Boolean)
    const limit = activePathKind === 'directory' ? parts.length : parts.length - 1
    if (limit <= 0) return
    setExpanded(s => {
      const next = new Set(s)
      let acc = ''
      for (let i = 0; i < limit; i++) {
        acc = acc ? `${acc}/${parts[i]}` : parts[i]
        next.add(acc)
      }
      return next
    })
  }, [activePath, activePathKind, fs])

  // After ancestors expand and their children load, scroll the highlighted row
  // into view inside the tree scroller (not the whole page).
  React.useEffect(() => {
    if (!activePath) return
    const scroller = treeScrollRef.current
    if (!scroller) return
    const frame = window.requestAnimationFrame(() => {
      const row = scroller.querySelector(`[data-path="${CSS.escape(activePath)}"]`)
      // jsdom has no scrollIntoView; real browsers do.
      if (row && typeof (row as HTMLElement).scrollIntoView === 'function') {
        ;(row as HTMLElement).scrollIntoView({ block: 'nearest' })
      }
    })
    return () => window.cancelAnimationFrame(frame)
  }, [activePath, expanded, refreshKey])
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

  const childRef = (parent: FileTarget | undefined, displayPath: string, name: string): FileRef => {
    if (!parent) return displayPath
    const targetPath = parent.path ? `${parent.path}/${name}` : name
    return retargetFile(parent, targetPath)
  }

  const openFile = (path: string, target?: FileTarget) => {
    if (onOpenFile) {
      if (target) onOpenFile(path, target)
      else onOpenFile(path)
    }
    else setEditing({ path, target })
  }

  function startCreate(dir: string, type: 'file' | 'dir', target?: FileTarget) {
    if (busy || !writableFs) return
    setRenaming(null)
    if (dir) setExpanded(s => new Set(s).add(dir))
    setCreating({ dir, target, type })
  }
  async function submitCreate(name: string) {
    const c = creating; if (!c || busy || !writableFs) return
    setCreating(null)
    if (c.type === 'file' && defaultExt && !/\.[a-z0-9]+$/i.test(name)) name = `${name}.${defaultExt}`
    const full = c.dir ? `${c.dir}/${name}` : name
    const ref = childRef(c.target, full, name)
    if (c.type === 'dir') await guard(writableFs.mkdir(ref))
    else if (await guard(writableFs.write(ref, ''))) {
      openFile(full, typeof ref === 'string' ? undefined : ref)
    }
  }
  async function submitRename(path: string, name: string) {
    if (busy || !writableFs) return
    const current = renaming
    setRenaming(null)
    const parent = path.split('/').slice(0, -1).join('/')
    const to = parent ? `${parent}/${name}` : name
    if (to === path) return
    const fromRef: FileRef = current?.target || path
    const toRef: FileRef = current?.target
      ? retargetFile(
        current.target,
        current.target.path.split('/').slice(0, -1).concat(name).filter(Boolean).join('/'),
      )
      : to
    if (await guard(writableFs.rename(fromRef, toRef)) && editing?.path === path) {
      setEditing({
        path: to,
        target: typeof toRef === 'string' ? undefined : toRef,
      })
    }
  }
  async function remove(path: string, target?: FileTarget) {
    if (busy || !writableFs) return
    if (!(await confirmDialog({ title: `Delete "${base(path)}"?`, message: 'This cannot be undone.', confirmLabel: 'Delete', danger: true }))) return
    if (
      await guard(writableFs.remove(target || path))
      && (editing?.path === path || editing?.path.startsWith(path + '/'))
    ) setEditing(null)
  }

  const t: Ctl = {
    fs, writableFs, refreshKey, fileFilter, busy, expanded,
    toggle: p => { if (!busy) setExpanded(s => { const n = new Set(s); if (n.has(p)) n.delete(p); else n.add(p); return n }) },
    creating, renaming, activePath: activePath !== undefined ? activePath : editing?.path || null,
    activePathKind: activePath !== undefined ? activePathKind : 'file',
    openFile,
    openMenu: (e, path, isDir, target) => {
      if (!writableFs) return
      e.preventDefault()
      e.stopPropagation()
      setMenu({ x: e.clientX, y: e.clientY, path, isDir, target })
    },
    submitCreate, cancelCreate: () => setCreating(null),
    submitRename, cancelRename: () => setRenaming(null)
  }

  const menuDir = menu ? (menu.path == null ? '' : menu.isDir ? menu.path : menu.path.split('/').slice(0, -1).join('/')) : ''
  const menuDirTarget = menu?.target
    ? menu.isDir
      ? menu.target
      : retargetFile(
        menu.target,
        menu.target.path.split('/').slice(0, -1).join('/'),
      )
    : undefined

  return <aside className={className}>
    <div className="tree-toolbar"><strong>{title}</strong><div className="tree-actions">
      {writableFs
        ? <>
            <button title="New file" aria-label="New file" onClick={() => startCreate('', 'file')} disabled={busy}><IconFilePlus size={16} /></button>
            <button title="New folder" aria-label="New folder" onClick={() => startCreate('', 'dir')} disabled={busy}><IconFolderPlus size={16} /></button>
          </>
        : <span className="tree-read-only">Read-only</span>}
    </div></div>
    {treeError && <p className="tree-error">{treeError}</p>}
    <div className="tree-scroll" ref={treeScrollRef} onContextMenu={writableFs ? e => { if (e.target === e.currentTarget) t.openMenu(e, null, true) } : undefined}><Level dir="" depth={0} t={t} /></div>
    {!onOpenFile && editing && <React.Suspense fallback={<div className="file-editor"><div className="file-editor-head"><strong>{base(editing.path)}</strong></div><p className="muted" style={{ padding: '10px' }}>Loading editor…</p></div>}><FileEditor fs={fs} write={writableFs?.write} path={editing.path} target={editing.target} onClose={() => setEditing(null)} /></React.Suspense>}
    {writableFs && menu && <div className="ctx-menu" style={{ top: menu.y, left: menu.x }} onClick={e => e.stopPropagation()}>
      <button onClick={() => { startCreate(menuDir, 'file', menuDirTarget); setMenu(null) }} disabled={busy}>New File</button>
      <button onClick={() => { startCreate(menuDir, 'dir', menuDirTarget); setMenu(null) }} disabled={busy}>New Folder</button>
      {menu.path != null && <><div className="ctx-sep" /><button onClick={() => { if (!busy) setRenaming({ path: menu.path as string, target: menu.target }); setMenu(null) }} disabled={busy}>Rename</button><button className="danger" onClick={() => { const p = menu.path as string; const target = menu.target; setMenu(null); void remove(p, target) }} disabled={busy}>Delete</button></>}
    </div>}
  </aside>
}
