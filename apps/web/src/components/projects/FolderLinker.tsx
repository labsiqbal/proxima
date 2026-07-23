import React from 'react'
import { browseDirs, linkProject } from '../../api/projects'
import type { Project } from '../../types'

type Mode = 'link' | 'create'

// Browse the folders under the configured link roots (default: home) and either
// register an EXISTING folder as a Proxima project, or create a brand-new empty
// folder on disk and register that. Shared by the Projects screen and the
// first-run onboarding step.
export function FolderLinker({ token, onLinked }: { token: string; onLinked: (p: Project) => Promise<void> }) {
  const [mode, setMode] = React.useState<Mode>('link')
  const [cur, setCur] = React.useState<{ path: string; parent: string | null; dirs: { name: string; path: string }[] } | null>(null)
  const [name, setName] = React.useState('')
  const [folderName, setFolderName] = React.useState('')
  const [err, setErr] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const loadSeq = React.useRef(0)
  const mountedRef = React.useRef(true)
  const actionSeq = React.useRef(0)
  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      loadSeq.current += 1
      actionSeq.current += 1
    }
  }, [])
  const load = React.useCallback((path = '') => {
    const seq = ++loadSeq.current
    setErr('')
    browseDirs(token, path)
      .then(next => { if (mountedRef.current && seq === loadSeq.current) setCur(next) })
      .catch(e => { if (mountedRef.current && seq === loadSeq.current) setErr(e instanceof Error ? e.message : String(e)) })
  }, [token])
  React.useEffect(() => { load() }, [load])

  const switchMode = (next: Mode) => {
    if (busy || next === mode) return
    setMode(next)
    setErr('')
    setName('')
    setFolderName('')
  }

  const submit = async () => {
    if (!cur || busy) return
    const seq = ++actionSeq.current
    setBusy(true); setErr('')
    try {
      let p: Project
      if (mode === 'create') {
        const folder = folderName.trim()
        if (!folder) {
          setErr('Enter a name for the new folder')
          return
        }
        if (/[/\\]/.test(folder) || folder === '.' || folder === '..') {
          setErr('Folder name cannot contain slashes or be “.” / “..”')
          return
        }
        const path = cur.path.endsWith('/') ? `${cur.path}${folder}` : `${cur.path}/${folder}`
        p = await linkProject(token, {
          path,
          name: name.trim() || folder,
          mkdir: true,
        })
      } else {
        p = await linkProject(token, { path: cur.path, name: name.trim() || undefined })
      }
      if (!mountedRef.current || seq !== actionSeq.current) return
      setName('')
      setFolderName('')
      await onLinked(p)
    } catch (e) {
      if (mountedRef.current && seq === actionSeq.current) setErr(e instanceof Error ? e.message : String(e))
    } finally {
      if (mountedRef.current && seq === actionSeq.current) setBusy(false)
    }
  }

  if (!cur) return <p className="muted">Loading folders…</p>
  const here = cur.path.split('/').filter(Boolean).pop() || cur.path
  const createLabel = folderName.trim() || 'new-folder'
  return <div className="folder-linker">
    <div className="seg fl-mode" role="tablist" aria-label="Folder action">
      <button type="button" role="tab" aria-selected={mode === 'link'} className={mode === 'link' ? 'active' : ''} disabled={busy} onClick={() => switchMode('link')}>
        Link existing
      </button>
      <button type="button" role="tab" aria-selected={mode === 'create'} className={mode === 'create' ? 'active' : ''} disabled={busy} onClick={() => switchMode('create')}>
        Create new folder
      </button>
    </div>
    <p className="muted fl-hint">
      {mode === 'link'
        ? 'Browse to a folder you already have. Nothing is moved or copied.'
        : 'Pick the parent directory, then name the new empty folder to create on disk.'}
    </p>
    <div className="fl-path"><span className="muted" aria-hidden="true">📁</span> <code>{cur.path}</code></div>
    <div className="fl-list">
      {cur.parent && <button type="button" className="fl-row up" disabled={busy} onClick={() => load(cur.parent!)}>↑ ..</button>}
      {cur.dirs.map(d => <button type="button" className="fl-row" disabled={busy} key={d.path} onClick={() => load(d.path)}>{d.name}</button>)}
      {!cur.dirs.length && <p className="muted fl-empty">No subfolders here.</p>}
    </div>
    {mode === 'create' ? (
      <div className="fl-create">
        <label className="fl-field">
          <span className="muted">New folder name</span>
          <input
            value={folderName}
            onChange={e => setFolderName(e.target.value)}
            placeholder="my-project"
            disabled={busy}
            autoComplete="off"
            spellCheck={false}
          />
        </label>
        <label className="fl-field">
          <span className="muted">Display name <span className="fl-optional">(optional)</span></span>
          <input
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder={folderName.trim() || 'My project'}
            disabled={busy}
          />
        </label>
        <button type="button" className="primary-button" disabled={busy || !folderName.trim()} onClick={() => void submit()}>
          {busy ? 'Creating…' : `Create “${createLabel}” here`}
        </button>
      </div>
    ) : (
      <div className="fl-link">
        <input value={name} onChange={e => setName(e.target.value)} placeholder={here} disabled={busy} aria-label="Project display name" />
        <button type="button" className="primary-button" disabled={busy} onClick={() => void submit()}>{busy ? 'Linking…' : `Link “${here}”`}</button>
      </div>
    )}
    {err && <p className="error-text">{err}</p>}
  </div>
}
