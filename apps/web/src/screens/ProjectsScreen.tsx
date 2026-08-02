import React from 'react'
import { createProject, renameProject, deleteProject } from '../api/projects'
import { ContainerSettingsModal } from '../components/projects/ContainerSettings'
import { FolderLinker } from '../components/projects/FolderLinker'
import { OpsMigrationDetail } from '../components/projects/OpsMigrationDetail'
import { RelocateProjectModal } from '../components/projects/RelocateProject'
import { confirmDialog, promptDialog } from '../components/ui/Dialog'
import type { Project } from '../types'

// What the card says when a project's folder is not where its record says it is.
const LOST_LABEL: Record<string, string> = {
  missing: 'Folder missing',
  moved: 'Folder changed',
  unavailable: 'Folder unavailable',
}

// Removing a project is not one behaviour, and the copy has to say so: the API only
// deletes the folder when it lives inside the workspace root, so a *linked* folder is
// merely unlinked and its real files survive. The old confirm dialog claimed "its files
// will be removed" for both cases, which was untrue for linked projects — the accurate
// wording lived in the panel next to it, and that panel is gone now.
const REMOVE_EXPLANATION =
  'A linked folder is only unlinked — your real files stay. A project Proxima created is '
  + 'deleted from disk. Either way its chats and tasks go with it.'

export function ProjectsScreen({ token, projects, activeProject, opsMigrationSlug, onActiveProject, onOpenOpsMigration, onCloseOpsMigration, onRefresh }: {
  token: string
  projects: Project[]
  activeProject: Project | null
  opsMigrationSlug?: string | null
  onActiveProject: (p: Project) => void
  onOpenOpsMigration?: (project: Project) => void
  onCloseOpsMigration?: () => void
  onRefresh: () => Promise<void>
}) {
  const [query, setQuery] = React.useState('')
  const [adding, setAdding] = React.useState(false)
  // The project whose container settings (code areas + push-after-merge) are open.
  const [settingsFor, setSettingsFor] = React.useState<Project | null>(null)
  // The project being re-pinned to its folder's real location (prune C6).
  const [relocating, setRelocating] = React.useState<Project | null>(null)
  const [error, setError] = React.useState('')
  // The slug being acted on, so one card's spinner never freezes the whole grid.
  const [busy, setBusy] = React.useState<string | null>(null)
  const mounted = React.useRef(true)
  const actionSeq = React.useRef(0)
  const message = (cause: unknown) => (cause instanceof Error ? cause.message : String(cause))

  React.useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      actionSeq.current += 1
    }
  }, [])

  async function act(key: string, run: () => Promise<void>) {
    if (busy) return
    setBusy(key)
    setError('')
    const seq = ++actionSeq.current
    try {
      await run()
    } catch (cause) {
      if (mounted.current && seq === actionSeq.current) setError(message(cause))
    } finally {
      if (mounted.current && seq === actionSeq.current) setBusy(null)
    }
  }

  async function rename(project: Project) {
    const next = await promptDialog({
      title: `Rename “${project.name}”`,
      label: 'New name',
      defaultValue: project.name,
      confirmLabel: 'Rename',
    })
    if (next === null) return
    const name = next.trim()
    if (!name || name === project.name) return
    await act(project.slug, async () => {
      const renamed = await renameProject(token, project.slug, name)
      // Refresh the active reference only when it *is* the renamed one; renaming a
      // project is not a request to switch to it.
      if (activeProject?.slug === project.slug) onActiveProject(renamed)
      await onRefresh()
    })
  }

  async function remove(project: Project) {
    const confirmed = await confirmDialog({
      title: `Remove project “${project.name}”?`,
      message: REMOVE_EXPLANATION,
      confirmLabel: 'Remove',
      danger: true,
    })
    if (!confirmed || !mounted.current) return
    await act(project.slug, async () => {
      await deleteProject(token, project.slug)
      await onRefresh()
    })
  }

  const filtered = projects.filter(project =>
    `${project.name} ${project.slug}`.toLowerCase().includes(query.trim().toLowerCase()))
  const opsMigrationProject = opsMigrationSlug
    ? projects.find(project => project.slug === opsMigrationSlug)
    : null

  if (opsMigrationSlug) {
    return opsMigrationProject
      ? <OpsMigrationDetail
          token={token}
          project={opsMigrationProject}
          onBack={() => onCloseOpsMigration?.()}
          onChanged={onRefresh}
        />
      : <section className="ops-migration-detail" aria-labelledby="ops-migration-missing-title">
          <button type="button" className="ghost-button" onClick={() => onCloseOpsMigration?.()}>Back to projects</button>
          <div className="placeholder-view"><div className="assistant-bubble compact">
            <h2 id="ops-migration-missing-title">Project not found</h2>
            <p className="muted">The Ops migration link points to <code>{opsMigrationSlug}</code>, which is not registered for this owner.</p>
          </div></div>
        </section>
  }

  return <section className="tasks-view projects-view">
    <div className="tasks-head">
      <input
        className="destination-search"
        type="search"
        name="project-search"
        value={query}
        onChange={event => setQuery(event.target.value)}
        placeholder="Search projects"
        aria-label="Search projects"
      />
      <button className="primary-button" disabled={!!busy} onClick={() => setAdding(true)}>Add project</button>
    </div>

    {error && <div className="error-bar">{error}</div>}

    {filtered.length === 0
      ? <div className="placeholder-view"><div className="assistant-bubble compact">
          <h1>Projects</h1>
          <p className="muted">{projects.length
            ? 'No project matches that search.'
            : 'No projects yet. Create one, or link a folder you already work in.'}</p>
        </div></div>
      : <div className="wf-grid">{filtered.map((project, index) => {
          const active = activeProject?.slug === project.slug
          const working = busy === project.slug
          // A folder that moved, was renamed, or cannot be used is a state the
          // owner can act on (prune C6) - never a card that only fails later.
          const lost = project.location.state !== 'bound'
          return <div
            className={`wf-card project-tile stagger-item${active ? ' active' : ''}`}
            style={{ ['--i' as string]: index } as React.CSSProperties}
            key={project.slug}
          >
            <button
              className="kanban-del"
              title="Remove"
              aria-label={`Remove project ${project.name}`}
              disabled={!!busy}
              onClick={() => void remove(project)}
            >×</button>
            <button className="wf-card-main" disabled={!!busy} onClick={() => onActiveProject(project)}>
              <strong>{project.name}</strong>
              <span className="wf-card-meta">
                <span className="pill">{project.slug}</span>
                {active && <span className="muted">Active</span>}
                {lost && <span className="pill pill-warn">{LOST_LABEL[project.location.state]}</span>}
              </span>
            </button>
            {lost && <p className="project-lost-note">{project.location.message}</p>}
            <div className="wf-card-foot">
              {lost && <button
                className="ghost-button project-lost-action"
                disabled={!!busy}
                aria-label={`Find the folder for ${project.name}`}
                onClick={() => setRelocating(project)}
              >
                Find folder
              </button>}
              <button
                className="ghost-button"
                disabled={!!busy}
                aria-label={`Rename project ${project.name}`}
                onClick={() => void rename(project)}
              >
                {working ? 'Working…' : 'Rename'}
              </button>
              <button
                className="ghost-button"
                disabled={!!busy}
                aria-label={`Project settings for ${project.name}`}
                onClick={() => setSettingsFor(project)}
              >
                Settings
              </button>
              <button
                className="ghost-button"
                disabled={!!busy}
                aria-label={`Ops migration for ${project.name}`}
                onClick={() => onOpenOpsMigration?.(project)}
              >
                Ops migration
              </button>
            </div>
          </div>
        })}</div>}

    {relocating && <RelocateProjectModal
      token={token}
      project={relocating}
      onClose={() => setRelocating(null)}
      onRelocated={async project => {
        setRelocating(null)
        if (activeProject?.slug === project.slug) onActiveProject(project)
        await onRefresh()
      }}
    />}

    {settingsFor && <ContainerSettingsModal
      token={token}
      project={settingsFor}
      onClose={() => setSettingsFor(null)}
    />}

    {adding && <AddProjectModal
      token={token}
      onClose={() => setAdding(false)}
      onAdded={async project => {
        setAdding(false)
        onActiveProject(project)
        await onRefresh()
      }}
    />}
  </section>
}

function AddProjectModal({ token, onClose, onAdded }: {
  token: string
  onClose: () => void
  onAdded: (project: Project) => Promise<void>
}) {
  const [name, setName] = React.useState('')
  const [slug, setSlug] = React.useState('')
  const [slugEdited, setSlugEdited] = React.useState(false)
  const [error, setError] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const mounted = React.useRef(true)
  const slugify = (value: string) =>
    value.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')

  React.useEffect(() => {
    mounted.current = true
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => {
      mounted.current = false
      window.removeEventListener('keydown', onKey)
    }
  }, [onClose])

  async function create(event: React.FormEvent) {
    event.preventDefault()
    if (busy) return
    const finalSlug = slug || slugify(name)
    if (!finalSlug) return
    setBusy(true)
    setError('')
    try {
      const project = await createProject(token, { slug: finalSlug, name: name || finalSlug })
      await onAdded(project)
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  return <div className="modal-scrim" onClick={onClose}>
    <div className="modal-card project-add-card" onClick={event => event.stopPropagation()} role="dialog" aria-modal="true" aria-label="Add a project">
      <h3>Add a project</h3>
      <form className="stack-form" onSubmit={create}>
        <label>Name<input
          autoFocus
          value={name}
          disabled={busy}
          placeholder="My Project"
          onChange={event => {
            setName(event.target.value)
            if (!slugEdited) setSlug(slugify(event.target.value))
          }}
        /></label>
        <label>Slug <span className="muted">(lowercase, auto from name)</span><input
          value={slug}
          disabled={busy}
          placeholder="my-project"
          onChange={event => { setSlugEdited(true); setSlug(slugify(event.target.value)) }}
        /></label>
        <button className="primary-button" disabled={busy || !(slug || slugify(name))}>
          {busy ? 'Creating…' : 'Create new'}
        </button>
      </form>
      <div className="divider" />
      <p className="eyebrow">Use a folder on disk</p>
      <p className="muted project-add-hint">Link work you already have, or create a new empty folder under a parent you pick. Nothing is moved or copied into Proxima's data dir.</p>
      <FolderLinker token={token} onLinked={onAdded} />
      {error && <p className="error-text">{error}</p>}
      <div className="confirm-actions">
        <button type="button" className="ghost-button" onClick={onClose}>Close</button>
      </div>
    </div>
  </div>
}
