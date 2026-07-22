import React from 'react'
import { listProjectAreas, updateProjectArea } from '../../api/projects'
import type { Project, ProjectAreas } from '../../types'

// The container's settings surface (T9, slice 11): the project's code areas,
// each paired with its detected git remote. An area WITH a remote is offered
// the per-area "push after merge" toggle (default off - local-only stays the
// posture); an area without one shows an honest "stays on this machine" line
// and no toggle at all. BYO to the letter: pushing uses this machine's own
// git, so there is nothing to sign into here and no remote to configure -
// only the opt-in.

export function ContainerSettingsModal({ token, project, onClose }: {
  token: string
  project: Project
  onClose: () => void
}) {
  const [areas, setAreas] = React.useState<ProjectAreas | null>(null)
  const [error, setError] = React.useState('')
  // The area id being toggled, so one row's spinner never freezes the rest.
  const [busy, setBusy] = React.useState<number | null>(null)
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

  return <div className="modal-scrim" onClick={onClose}>
    <div className="modal-card container-settings-card" onClick={event => event.stopPropagation()} role="dialog" aria-modal="true" aria-label={`Container settings for ${project.name}`}>
      <h3>Container settings - {project.name}</h3>
      <p className="eyebrow">Code areas</p>
      {error && <p className="error-text">{error}</p>}
      {!areas && !error && <p className="muted">Loading…</p>}
      {areas && areas.code_areas.length === 0 && <p className="muted">
        No code areas yet - link or create a git repo inside this project's folder.
      </p>}
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
        <button type="button" className="ghost-button" onClick={onClose}>Close</button>
      </div>
    </div>
  </div>
}
