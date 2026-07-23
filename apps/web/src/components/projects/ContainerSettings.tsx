import React from 'react'
import { addProjectArea, detectProjectAreas, listProjectAreas, updateProjectArea } from '../../api/projects'
import type { Project, ProjectAreas } from '../../types'

// The container's settings surface (T9, slice 11): the project's code areas,
// each paired with its detected git remote. An area WITH a remote is offered
// the per-area "push after merge" toggle (default off - local-only stays the
// posture); an area without one shows an honest "stays on this machine" line
// and no toggle at all. BYO to the letter: pushing uses this machine's own
// git, so there is nothing to sign into here and no remote to configure -
// only the opt-in.
//
// Empty projects used to dead-end here ("link or create a git repo…") with only
// Close. The API already supports manual register + detect - expose those so
// the owner can take a next step without leaving the dialog.

export function ContainerSettingsModal({ token, project, onClose }: {
  token: string
  project: Project
  onClose: () => void
}) {
  const [areas, setAreas] = React.useState<ProjectAreas | null>(null)
  const [error, setError] = React.useState('')
  // The area id being toggled, so one row's spinner never freezes the rest.
  // 'scan' / 'use-root' mark the empty-state actions.
  const [busy, setBusy] = React.useState<number | 'scan' | 'use-root' | null>(null)
  const mounted = React.useRef(true)

  React.useEffect(() => {
    mounted.current = true
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => {
      mounted.current = false
      window.removeEventListener('keydown', onKey)
    }
  }, [onClose])

  React.useEffect(() => {
    let cancelled = false
    listProjectAreas(token, project.slug)
      .then(body => { if (!cancelled) setAreas(body) })
      .catch(cause => { if (!cancelled) setError(String(cause)) })
    return () => { cancelled = true }
  }, [token, project.slug])

  async function toggle(areaId: number, next: boolean) {
    if (busy != null) return
    setBusy(areaId)
    setError('')
    try {
      const updated = await updateProjectArea(token, project.slug, areaId, { push_on_merge: next })
      if (mounted.current) setAreas(current => current && {
        ...current,
        code_areas: current.code_areas.map(area =>
          area.id === areaId ? { ...area, push_on_merge: updated.push_on_merge } : area),
      })
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  async function scanForRepos() {
    if (busy != null) return
    setBusy('scan')
    setError('')
    try {
      const body = await detectProjectAreas(token, project.slug)
      if (mounted.current) {
        setAreas({ code_areas: body.code_areas, ops_area: body.ops_area })
        if (body.code_areas.length === 0 && body.detect.added.length === 0) {
          setError('No git repos found under this project folder yet.')
        }
      }
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  async function useProjectRoot() {
    if (busy != null) return
    setBusy('use-root')
    setError('')
    try {
      await addProjectArea(token, project.slug, { rel_path: '.' })
      // Re-list so remotes + push toggles stay consistent with the list endpoint.
      const body = await listProjectAreas(token, project.slug)
      if (mounted.current) setAreas(body)
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  const empty = !!areas && areas.code_areas.length === 0
  const scanning = busy === 'scan'
  const registering = busy === 'use-root'

  return <div className="modal-scrim" onClick={onClose}>
    <div className="modal-card container-settings-card" onClick={event => event.stopPropagation()} role="dialog" aria-modal="true" aria-label={`Code areas for ${project.name}`}>
      <h3>Code areas - {project.name}</h3>
      <p className="eyebrow">Code areas</p>
      <p className="muted container-settings-lead">
        Folders where agent jobs can edit code in an isolated copy. Usually a git repo
        inside the project - the project folder itself counts too.
      </p>
      {error && <p className="error-text" role="alert">{error}</p>}
      {!areas && !error && <p className="muted">Loading…</p>}
      {empty && <div className="container-empty">
        <p className="muted">
          No code areas yet. Scan for git repos under this project, or treat the whole
          project folder as one so jobs have a place to work.
        </p>
        <div className="container-empty-actions">
          <button
            type="button"
            className="primary-button"
            disabled={busy != null}
            onClick={() => void scanForRepos()}
          >
            {scanning ? 'Scanning…' : 'Scan for git repos'}
          </button>
          <button
            type="button"
            className="ghost-button"
            disabled={busy != null}
            onClick={() => void useProjectRoot()}
          >
            {registering ? 'Adding…' : 'Use project folder'}
          </button>
        </div>
      </div>}
      {areas && areas.code_areas.length > 0 && <ul className="container-areas">
        {areas.code_areas.map(area => <li className="container-area" key={area.id}>
          <div className="container-area-head">
            <code>{area.rel_path === '.' ? 'project root' : area.rel_path}</code>
            {area.source === 'manual' && <span className="pill">manual</span>}
          </div>
          {area.remote
            ? <>
                <p className="muted container-area-remote">
                  Remote: <code>{area.remote.url}</code>
                  {area.remote.web_url && <> · <a href={area.remote.web_url} target="_blank" rel="noreferrer">open on GitHub</a></>}
                  {area.remote.gh_authenticated && <> · gh signed in</>}
                </p>
                <label className="container-area-toggle">
                  <input
                    type="checkbox"
                    checked={!!area.push_on_merge}
                    disabled={busy != null}
                    onChange={event => void toggle(area.id, event.target.checked)}
                  />
                  <span>
                    Push after merge
                    <span className="muted container-area-hint">
                      When a job's approved changes merge, also push them to the remote - with this
                      machine's own git. Proxima stores no credentials. Off: everything stays local.
                    </span>
                  </span>
                </label>
              </>
            : <p className="muted container-area-remote">No git remote - merged changes stay on this machine.</p>}
        </li>)}
      </ul>}
      <div className="confirm-actions">
        {areas && areas.code_areas.length > 0 && <button
          type="button"
          className="ghost-button"
          disabled={busy != null}
          onClick={() => void scanForRepos()}
        >
          {scanning ? 'Scanning…' : 'Scan again'}
        </button>}
        <button type="button" className="ghost-button" onClick={onClose}>Close</button>
      </div>
    </div>
  </div>
}
