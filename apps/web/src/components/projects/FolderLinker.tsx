import React from 'react'
import { browseDirs, linkProject } from '../../api/projects'
import type { Project } from '../../types'

type Mode = 'link' | 'create'
type ErrorField = 'folder' | 'display'
type FormError = { message: string; field: ErrorField | null }

const PROJECT_NAME_MAX_LENGTH = 120

// Browse the folders under the configured link roots (default: home) and either
// register an EXISTING folder as a Proxima project, or create a brand-new empty
// folder on disk and register that. Shared by the Projects screen and the
// first-run onboarding step.
export function FolderLinker({ token, onLinked }: { token: string; onLinked: (p: Project) => Promise<void> }) {
  const [mode, setMode] = React.useState<Mode>('link')
  const [cur, setCur] = React.useState<{ path: string; parent: string | null; dirs: { name: string; path: string }[] } | null>(null)
  const [name, setName] = React.useState('')
  const [folderName, setFolderName] = React.useState('')
  const [error, setError] = React.useState<FormError | null>(null)
  const [busy, setBusy] = React.useState(false)
  const loadSeq = React.useRef(0)
  const mountedRef = React.useRef(true)
  const actionSeq = React.useRef(0)
  const folderNameRef = React.useRef<HTMLInputElement>(null)
  const displayNameRef = React.useRef<HTMLInputElement>(null)
  const reportError = React.useCallback((message: string, field: ErrorField) => {
    const target = field === 'folder' ? folderNameRef : displayNameRef
    target.current?.focus()
    setError({ message, field })
  }, [])
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
    setError(null)
    browseDirs(token, path)
      .then(next => { if (mountedRef.current && seq === loadSeq.current) setCur(next) })
      .catch(e => {
        if (mountedRef.current && seq === loadSeq.current) {
          setError({ message: e instanceof Error ? e.message : String(e), field: null })
        }
      })
  }, [token])
  React.useEffect(() => { load() }, [load])

  const switchMode = (next: Mode) => {
    if (busy || next === mode) return
    setMode(next)
    setError(null)
    setName('')
    setFolderName('')
  }

  const submit = async () => {
    if (!cur || busy) return
    if (mode === 'create') {
      const folder = folderName.trim()
      if (!folder) {
        reportError('Enter a name for the new folder', 'folder')
        return
      }
      if (/[/\\]/.test(folder) || folder === '.' || folder === '..') {
        reportError('Folder name cannot contain slashes or be “.” / “..”', 'folder')
        return
      }
    }
    const displayName = name.trim()
    if (displayName.length > PROJECT_NAME_MAX_LENGTH) {
      reportError(`Display name must be ${PROJECT_NAME_MAX_LENGTH} characters or fewer`, 'display')
      return
    }
    const seq = ++actionSeq.current
    setBusy(true)
    setError(null)
    try {
      let p: Project
      if (mode === 'create') {
        const folder = folderName.trim()
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
      if (mountedRef.current && seq === actionSeq.current) {
        reportError(e instanceof Error ? e.message : String(e), mode === 'create' ? 'folder' : 'display')
      }
    } finally {
      if (mountedRef.current && seq === actionSeq.current) setBusy(false)
    }
  }

  if (!cur) return <p className="muted">Loading folders…</p>
  const here = cur.path.split('/').filter(Boolean).pop() || cur.path
  const createLabel = folderName.trim() || 'new-folder'
  const errorField = error?.field
  return <div className="folder-linker">
    <div className="seg fl-mode" role="group" aria-label="Folder action">
      <button type="button" aria-pressed={mode === 'link'} className={mode === 'link' ? 'active' : ''} disabled={busy} onClick={() => switchMode('link')}>
        Link existing
      </button>
      <button type="button" aria-pressed={mode === 'create'} className={mode === 'create' ? 'active' : ''} disabled={busy} onClick={() => switchMode('create')}>
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
            ref={folderNameRef}
            name="folder-name"
            value={folderName}
            onChange={e => {
              setFolderName(e.target.value)
              if (errorField === 'folder') setError(null)
            }}
            placeholder="my-project"
            readOnly={busy}
            autoComplete="off"
            spellCheck={false}
            aria-invalid={errorField === 'folder' || undefined}
            aria-describedby={errorField === 'folder' ? 'folder-linker-error' : undefined}
          />
        </label>
        <label className="fl-field">
          <span className="muted">Display name <span className="fl-optional">(optional)</span></span>
          <input
            ref={displayNameRef}
            name="project-display-name"
            value={name}
            onChange={e => {
              setName(e.target.value)
              if (errorField === 'display') setError(null)
            }}
            placeholder={folderName.trim() || 'My project'}
            readOnly={busy}
            aria-invalid={errorField === 'display' || undefined}
            aria-describedby={errorField === 'display' ? 'folder-linker-error' : undefined}
          />
        </label>
        <button type="button" className="primary-button" disabled={busy || !folderName.trim()} onClick={() => void submit()}>
          {busy ? 'Creating…' : `Create “${createLabel}” here`}
        </button>
      </div>
    ) : (
      <div className="fl-link">
        <input ref={displayNameRef} name="project-display-name" value={name}
          onChange={e => {
            setName(e.target.value)
            if (errorField === 'display') setError(null)
          }}
          placeholder={here} readOnly={busy} aria-label="Project display name"
          aria-invalid={errorField === 'display' || undefined}
          aria-describedby={errorField === 'display' ? 'folder-linker-error' : undefined} />
        <button type="button" className="primary-button" disabled={busy} onClick={() => void submit()}>{busy ? 'Linking…' : `Link “${here}”`}</button>
      </div>
    )}
    {error && <p id="folder-linker-error" className="error-text" role="alert">{error.message}</p>}
  </div>
}
