import React from 'react'
import { getOpsMigration, retryOpsMigration, validateOpsMigration } from '../../api/projects'
import type { OpsMigrationDetail as OpsMigrationDetailPayload, Project } from '../../types'
import { confirmDialog } from '../ui/Dialog'

const stateLabel = (state: string) => state.replaceAll('_', ' ')
const inspectableKind = (state: string): 'directory' | 'file' | null => (
  state === 'directory' || state === 'file' ? state : null
)

export function OpsMigrationDetail({ token, project, onBack, onChanged }: {
  token: string
  project: Project
  onBack: () => void
  onChanged: () => Promise<void>
}) {
  const [loaded, setLoaded] = React.useState<{
    slug: string
    value: OpsMigrationDetailPayload
  } | null>(null)
  const [busy, setBusy] = React.useState<'load' | 'validate' | 'retry' | null>('load')
  const [error, setError] = React.useState('')
  const [notice, setNotice] = React.useState('')
  const titleRef = React.useRef<HTMLHeadingElement>(null)
  const requestSeq = React.useRef(0)
  const detail = loaded?.slug === project.slug ? loaded.value : null

  const load = React.useCallback(async () => {
    const seq = ++requestSeq.current
    setBusy('load')
    setError('')
    setNotice('')
    try {
      const next = await getOpsMigration(token, project.slug)
      if (seq === requestSeq.current) {
        setLoaded({ slug: project.slug, value: next })
      }
    } catch (cause) {
      if (seq === requestSeq.current) {
        setError(cause instanceof Error ? cause.message : String(cause))
      }
    } finally {
      if (seq === requestSeq.current) setBusy(null)
    }
  }, [project.slug, token])

  React.useEffect(() => {
    void load()
    titleRef.current?.focus()
    return () => { requestSeq.current += 1 }
  }, [load])

  async function refreshValidation() {
    if (busy) return
    const seq = ++requestSeq.current
    setBusy('validate')
    setError('')
    setNotice('')
    try {
      const next = await validateOpsMigration(token, project.slug)
      if (seq !== requestSeq.current) return
      setLoaded({ slug: project.slug, value: next })
      setNotice(next.retry_safe
        ? 'Validation is safe. Retry is now available.'
        : 'Validation refreshed. The layout still needs owner attention.')
    } catch (cause) {
      if (seq === requestSeq.current) {
        setError(cause instanceof Error ? cause.message : String(cause))
      }
    } finally {
      if (seq === requestSeq.current) setBusy(null)
    }
  }

  async function retry() {
    if (busy || !detail?.retry_safe) return
    const confirmed = await confirmDialog({
      title: `Retry Ops migration for ${project.name}?`,
      message: 'Validation is currently safe. Proxima will move only the planned Ops-owned paths on this filesystem and will stop if the layout changes.',
      confirmLabel: 'Retry migration',
    })
    if (!confirmed) return
    const seq = ++requestSeq.current
    setBusy('retry')
    setError('')
    setNotice('')
    try {
      const next = await retryOpsMigration(token, project.slug)
      if (seq !== requestSeq.current) return
      setLoaded({ slug: project.slug, value: next })
      setNotice('Ops migration completed. The Attention item is resolved.')
      await onChanged()
    } catch (cause) {
      if (seq === requestSeq.current) {
        setError(cause instanceof Error ? cause.message : String(cause))
      }
    } finally {
      if (seq === requestSeq.current) setBusy(null)
    }
  }

  function reveal(path: string, pathKind: 'root' | 'directory' | 'file') {
    window.dispatchEvent(new CustomEvent('proxima:reveal-file', {
      detail: {
        path,
        pathKind,
        projectSlug: project.slug,
        rootSide: 'container',
      },
    }))
  }

  const phase = detail?.phase || 'loading'
  const safe = !!detail?.retry_safe
  const unavailable = detail?.what_remains_usable.unavailable_paths || []
  const legacyInspection = detail?.inspection.legacy_root
  const physicalInspection = detail?.inspection.physical_root
  const physicalRootKind = detail
    && physicalInspection?.inspectable
    && (detail.physical_ops.state === 'empty' || detail.physical_ops.state === 'populated')
    ? 'directory'
    : null

  return <section className="ops-migration-detail" aria-labelledby="ops-migration-title">
    <header className="ops-migration-head">
      <button type="button" className="ghost-button" onClick={onBack}>Back to projects</button>
      <div>
        <p className="eyebrow">Project settings</p>
        <h2 id="ops-migration-title" ref={titleRef} tabIndex={-1}>Ops migration</h2>
        <p className="muted">Recovery details for <strong>{project.name}</strong> <code>{project.slug}</code></p>
      </div>
      <span className={`ops-migration-phase ${safe ? 'is-safe' : phase === 'complete' || phase === 'not_required' ? 'is-complete' : 'needs-attention'}`}>
        {stateLabel(phase)}
      </span>
    </header>

    {error && <div className="ops-migration-message is-error" role="alert">
      <strong>Migration details could not update</strong>
      <span>{error}</span>
      <button type="button" className="ghost-button" onClick={() => void load()}>Try again</button>
    </div>}
    {notice && <p className="ops-migration-message is-success" role="status">{notice}</p>}
    {busy === 'load' && !detail && <div className="settings-loading" role="status"><span className="ui-spinner" />Loading migration details...</div>}

    {detail && <>
      <div className="ops-migration-summary">
        <article className="panel ops-migration-reason">
          <div className="panel-head"><h3>Stored reason</h3><span>exact recorded detail</span></div>
          <p>{detail.stored_reason || 'No migration failure has been recorded.'}</p>
        </article>
        <article className="panel">
          <div className="panel-head"><h3>Migration state</h3><span>version {detail.migration_version}</span></div>
          <dl className="ops-migration-facts">
            <div><dt>Phase</dt><dd>{stateLabel(detail.phase)}</dd></div>
            <div><dt>Active Ops path</dt><dd><code>{detail.active_ops_path ?? 'unavailable'}</code></dd></div>
            <div><dt>Physical ops/</dt><dd>{stateLabel(detail.physical_ops.state)}</dd></div>
            <div><dt>Attention</dt><dd>{stateLabel(detail.attention.status)}</dd></div>
          </dl>
          {detail.physical_ops.entries.length > 0 && (
            <ul className="ops-migration-list" aria-label="Physical ops entries">
              {detail.physical_ops.entries.map(entry => (
                <li key={entry.path}>
                  <code>{entry.path}</code>
                  <span>{stateLabel(entry.kind)}</span>
                </li>
              ))}
            </ul>
          )}
        </article>
      </div>

      <article className={`ops-migration-validation ${safe ? 'is-safe' : 'needs-attention'}`} aria-live="polite">
        <div>
          <p className="eyebrow">Current validation</p>
          <strong>{safe ? 'Layout is safe to retry' : detail.phase === 'complete' || detail.phase === 'not_required' ? 'No retry needed' : 'Retry remains disabled'}</strong>
          <p>{detail.validation_reason || 'All planned paths passed collision, type, hash, symlink, overlap, and same-filesystem checks.'}</p>
        </div>
        <div className="ops-migration-actions">
          <button
            type="button"
            className="ghost-button"
            disabled={!!busy || !legacyInspection?.inspectable}
            aria-describedby="ops-migration-legacy-inspection"
            onClick={() => { if (legacyInspection?.inspectable) reveal('', 'root') }}
          >Reveal legacy side</button>
          <button
            type="button"
            className="ghost-button"
            disabled={!!busy || !physicalRootKind}
            aria-describedby="ops-migration-physical-inspection"
            onClick={() => { if (physicalRootKind) reveal('ops', physicalRootKind) }}
          >Reveal physical ops/</button>
          <button type="button" className="ghost-button" disabled={!!busy} onClick={() => void refreshValidation()}>
            {busy === 'validate' ? 'Refreshing...' : 'Refresh validation'}
          </button>
          <button
            type="button"
            className="primary-button"
            disabled={!!busy || !safe}
            aria-describedby="ops-migration-retry-rule"
            onClick={() => void retry()}
          >
            {busy === 'retry' ? 'Retrying...' : 'Retry migration'}
          </button>
        </div>
        <span id="ops-migration-legacy-inspection" className="sr-only">
          {legacyInspection?.reason || 'Legacy Container root is available for inspection'}
        </span>
        <span id="ops-migration-physical-inspection" className="sr-only">
          {physicalInspection?.reason || 'Physical ops root is available for inspection'}
        </span>
        <p id="ops-migration-retry-rule" className="muted">
          Retry is available only after validation is safe. Proxima never merges, overwrites,
          deletes, follows symlinks, moves across filesystems, or chooses authoritative content.
        </p>
      </article>

      <article className="panel">
        <div className="panel-head"><h3>Legacy owned paths</h3><span>{detail.legacy_owned_paths.length} detected</span></div>
        {detail.legacy_owned_paths.length === 0
          ? <p className="muted">No legacy Ops-owned paths are present.</p>
          : <div className="ops-migration-table-wrap"><table className="ops-migration-table">
              <caption>Legacy and physical state for each planned Ops-owned path</caption>
              <thead><tr><th scope="col">Path</th><th scope="col">Legacy</th><th scope="col">Physical</th><th scope="col">Usable</th><th scope="col">Inspect</th></tr></thead>
              <tbody>{detail.legacy_owned_paths.map(path => {
                const legacyKind = legacyInspection?.inspectable
                  ? inspectableKind(path.legacy_state)
                  : null
                const physicalKind = physicalInspection?.inspectable
                  ? inspectableKind(path.physical_state)
                  : null
                return <tr key={path.path}>
                  <th scope="row"><code>{path.path}</code></th>
                  <td>{stateLabel(path.legacy_state)}</td>
                  <td><code>{path.destination}</code><small>{stateLabel(path.physical_state)}</small></td>
                  <td>{path.usable_from_active_ops ? 'Yes' : 'Not from active Ops'}</td>
                  <td><div className="ops-migration-row-actions">
                    <button
                      type="button"
                      className="text-button"
                      disabled={!legacyKind}
                      title={legacyKind ? undefined : `Cannot inspect ${stateLabel(path.legacy_state)} content`}
                      onClick={() => { if (legacyKind) reveal(path.path, legacyKind) }}
                      aria-label={`Reveal legacy ${path.path}`}
                    >Legacy</button>
                    <button
                      type="button"
                      className="text-button"
                      disabled={!physicalKind}
                      title={physicalKind ? undefined : `Cannot inspect ${stateLabel(path.physical_state)} content`}
                      onClick={() => { if (physicalKind) reveal(path.destination, physicalKind) }}
                      aria-label={`Reveal physical ${path.destination}`}
                    >Physical</button>
                  </div></td>
                </tr>
              })}</tbody>
            </table></div>}
      </article>

      <div className="ops-migration-summary">
        <article className="panel">
          <div className="panel-head"><h3>Conflicts</h3><span>{detail.conflicts.length}</span></div>
          {detail.conflicts.length
            ? <ul className="ops-migration-list">{detail.conflicts.map((conflict, index) =>
                <li key={`${conflict.path}:${index}`}><code>{conflict.path}</code><span>{conflict.reason}</span></li>)}</ul>
            : <p className="muted">No current layout conflicts.</p>}
        </article>
        <article className="panel">
          <div className="panel-head"><h3>What remains usable</h3><span>{unavailable.length ? 'limited' : 'available'}</span></div>
          <p>{detail.what_remains_usable.summary}</p>
          {detail.what_remains_usable.available_paths.length > 0 && <p className="muted">Available: {detail.what_remains_usable.available_paths.join(', ')}</p>}
          {unavailable.length > 0 && <p className="error-text">Unavailable until recovery: {unavailable.join(', ')}</p>}
        </article>
      </div>
    </>}
  </section>
}
