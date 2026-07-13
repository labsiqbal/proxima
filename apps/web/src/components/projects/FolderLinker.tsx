import React from 'react'
import { browseDirs, linkProject } from '../../api/projects'
import type { Project } from '../../types'

// Browse the folders under the configured link roots (default: home) and register
// an EXISTING folder as a Proxima project, so existing work connects to the cockpit.
// Shared by the Projects screen and the first-run onboarding step.
export function FolderLinker({ token, onLinked }: { token: string; onLinked: (p: Project) => Promise<void> }) {
  const [cur, setCur] = React.useState<{ path: string; parent: string | null; dirs: { name: string; path: string }[] } | null>(null)
  const [name, setName] = React.useState('')
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
  const link = async () => {
    if (!cur || busy) return
    const seq = ++actionSeq.current
    setBusy(true); setErr('')
    try {
      const p = await linkProject(token, { path: cur.path, name: name.trim() || undefined })
      if (!mountedRef.current || seq !== actionSeq.current) return
      setName('')
      await onLinked(p)
    } catch (e) {
      if (mountedRef.current && seq === actionSeq.current) setErr(e instanceof Error ? e.message : String(e))
    } finally {
      if (mountedRef.current && seq === actionSeq.current) setBusy(false)
    }
  }
  if (!cur) return <p className="muted">Loading folders…</p>
  const here = cur.path.split('/').filter(Boolean).pop() || cur.path
  return <div className="folder-linker">
    <div className="fl-path"><span className="muted">📁</span> <code>{cur.path}</code></div>
    <div className="fl-list">
      {cur.parent && <button className="fl-row up" disabled={busy} onClick={() => load(cur.parent!)}>↑ ..</button>}
      {cur.dirs.map(d => <button className="fl-row" disabled={busy} key={d.path} onClick={() => load(d.path)}>{d.name}</button>)}
      {!cur.dirs.length && <p className="muted" style={{ padding: '6px 2px' }}>No subfolders here.</p>}
    </div>
    <div className="fl-link">
      <input value={name} onChange={e => setName(e.target.value)} placeholder={here} disabled={busy} />
      <button className="primary-button" disabled={busy} onClick={() => void link()}>{busy ? 'Linking…' : `Link “${here}”`}</button>
    </div>
    {err && <p className="error-text">{err}</p>}
  </div>
}
