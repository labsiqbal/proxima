import React from 'react'
import { createProject, renameProject, deleteProject, browseDirs, linkProject } from '../api/projects'
import { confirmDialog } from '../components/ui/Dialog'
import type { Project } from '../types'

// Browse the projects tree and register an EXISTING folder as a Proxima project,
// so existing work (e.g. _modes/work/<client>) connects to the cockpit.
function FolderLinker({ token, onLinked }: { token: string; onLinked: (p: Project) => Promise<void> }) {
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

export function ProjectsScreen({ token, projects, onActiveProject, onRefresh }: { token: string; projects: Project[]; onActiveProject: (p: Project) => void; onRefresh: () => Promise<void> }) {
  const [slug, setSlug] = React.useState('')
  const [slugEdited, setSlugEdited] = React.useState(false)
  const [name, setName] = React.useState('')
  const [selected, setSelected] = React.useState<Project | null>(projects[0] || null)
  const [renameVal, setRenameVal] = React.useState('')
  const [rightMode, setRightMode] = React.useState<'add' | 'manage'>(projects.length ? 'manage' : 'add')
  const [error, setError] = React.useState('')
  const [busy, setBusy] = React.useState<'create' | 'rename' | 'delete' | null>(null)
  const mountedRef = React.useRef(true)
  const actionSeq = React.useRef(0)
  const msg = (e: unknown) => (e instanceof Error ? e.message : String(e))
  const slugify = (s: string) => s.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      actionSeq.current += 1
    }
  }, [])

  async function create(event: React.FormEvent) {
    event.preventDefault(); setError('')
    if (busy) return
    const finalSlug = slug || slugify(name)
    setBusy('create')
    const seq = ++actionSeq.current
    try {
      const p = await createProject(token, { slug: finalSlug, name: name || finalSlug })
      if (!mountedRef.current || seq !== actionSeq.current) return
      setSlug(''); setName(''); setSlugEdited(false); setSelected(p); onActiveProject(p)
      await onRefresh()
    } catch (err) { if (mountedRef.current && seq === actionSeq.current) setError(msg(err)) } finally { if (mountedRef.current && seq === actionSeq.current) setBusy(null) }
  }
  async function doRename(event: React.FormEvent) {
    event.preventDefault()
    if (busy || !selected || !renameVal.trim() || renameVal.trim() === selected.name) return
    setError('')
    setBusy('rename')
    const seq = ++actionSeq.current
    try {
      const p = await renameProject(token, selected.slug, renameVal.trim())
      if (!mountedRef.current || seq !== actionSeq.current) return
      setSelected(p); onActiveProject(p); setRenameVal('')
      await onRefresh()
    } catch (err) { if (mountedRef.current && seq === actionSeq.current) setError(msg(err)) } finally { if (mountedRef.current && seq === actionSeq.current) setBusy(null) }
  }
  async function removeProject() {
    if (busy || !selected) return
    if (!(await confirmDialog({ title: `Delete project "${selected.name}"?`, message: 'Its files, chats and tasks will be removed. This cannot be undone.', confirmLabel: 'Delete', danger: true }))) return
    if (!mountedRef.current || busy || !selected) return
    setError(''); setBusy('delete')
    const seq = ++actionSeq.current
    try {
      await deleteProject(token, selected.slug)
      if (!mountedRef.current || seq !== actionSeq.current) return
      setSelected(null)
      await onRefresh()
    } catch (err) { if (mountedRef.current && seq === actionSeq.current) setError(msg(err)) } finally { if (mountedRef.current && seq === actionSeq.current) setBusy(null) }
  }

  return <section className="projects-view">
    <div className="panel project-list-panel">
      <div className="panel-head"><h3>Projects</h3><span>{projects.length}</span></div>
      <button className={`project-add-btn ${rightMode === 'add' ? 'active' : ''}`} disabled={!!busy} onClick={() => { setRightMode('add'); setSelected(null) }}>➕ Add project</button>
      {projects.map(project => <button className={`project-card ${rightMode === 'manage' && selected?.slug === project.slug ? 'active' : ''}`} disabled={!!busy} key={project.slug} onClick={() => { setSelected(project); onActiveProject(project); setRenameVal(''); setRightMode('manage') }}><strong>{project.name}</strong><small>{project.slug}</small></button>)}
    </div>
    <div className="panel project-actions-panel">
      {rightMode === 'add'
        ? <>
          <div className="panel-head"><h3>➕ Add a project</h3><span>new or existing</span></div>
          <form className="stack-form" onSubmit={create}>
            <label>Name<input value={name} onChange={e => { setName(e.target.value); if (!slugEdited) setSlug(slugify(e.target.value)) }} placeholder="My Project" disabled={!!busy} /></label>
            <label>Slug <span className="muted">(lowercase, auto from name)</span><input value={slug} onChange={e => { setSlugEdited(true); setSlug(slugify(e.target.value)) }} placeholder="my-project" disabled={!!busy} /></label>
            <button className="primary-button" disabled={!!busy || !(slug || slugify(name))}>{busy === 'create' ? 'Creating…' : 'Create new'}</button>
          </form>
          <div className="divider" />
          <div className="panel-head"><h3>Link existing folder</h3><span>connect your work</span></div>
          <FolderLinker token={token} onLinked={async p => { setSelected(p); onActiveProject(p); setRightMode('manage'); await onRefresh() }} />
          {error && <p className="error-text">{error}</p>}
        </>
        : !selected
          ? <p className="muted">Select a project on the left, or click “Add project”.</p>
          : <>
            <div className="panel-head"><h3>⚙ Manage project</h3><span className="manage-target">{selected.name}</span></div>
            <p className="eyebrow">Rename</p>
            <form className="stack-form" onSubmit={doRename}>
              <label>New name<input value={renameVal} onChange={e => setRenameVal(e.target.value)} placeholder={selected.name} disabled={!!busy} /></label>
              <button className="primary-button" disabled={!!busy || !renameVal.trim() || renameVal.trim() === selected.name}>{busy === 'rename' ? 'Renaming…' : 'Rename'}</button>
            </form>
            <div className="divider" />
            <div className="danger-zone">
              <div><strong>Remove project</strong><small className="muted"> Linked folders are only unlinked — your real files stay. Proxima-created projects are deleted.</small></div>
              <button className="ghost-button danger" disabled={!!busy} onClick={() => void removeProject()}>{busy === 'delete' ? 'Removing…' : `Remove “${selected.name}”`}</button>
            </div>
            {error && <p className="error-text">{error}</p>}
          </>}
    </div>
  </section>
}
