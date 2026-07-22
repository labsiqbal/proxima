import React from 'react'
import type { JobStatus, JobWorktree } from '../../types'
import { getJobDiff, rejectJob, type JobDiff } from '../../api/jobs'
import { fileStatusLabel, parseUnifiedPatch } from './diff'

// The repo-job review surface (slice 4, T1 local-first). One component, two
// T4-ratified homes: a plan row's EXPANDING body on the Tasks screen, and the
// full-width task page. Never a side panel, never a popup. The job worked in
// an isolated copy of the code area; here the owner reads the change and
// either approves (Proxima merges it into the area's own line, locally) or
// rejects with a required one-line why (the copy is discarded, the project
// untouched). Copy stays jargon-free: "isolated copy" and "changes", not
// git nouns.

const short = (sha: string | null | undefined) => (sha || '').slice(0, 7)

export function ChangesReview({ token, jobId, jobStatus, worktree, rejectedReason, canDecide, decideBlockedNote, onApprove, onChanged }: {
  token: string
  jobId: number
  jobStatus: JobStatus
  worktree: JobWorktree
  rejectedReason?: string | null
  /** Whether this surface owns the approve/reject verdict right now (final review). */
  canDecide: boolean
  /** Shown instead of the approve action when the verdict is blocked elsewhere (e.g. plan jobs still awaiting their own review). */
  decideBlockedNote?: string | null
  /** Engine-specific approve (linear job vs plan); errors surface here. */
  onApprove: () => Promise<unknown>
  /** Parent refresh after any verdict — payloads change shape on merge/reject. */
  onChanged: () => void
}) {
  const [diff, setDiff] = React.useState<JobDiff | null>(null)
  const [diffError, setDiffError] = React.useState('')
  const [showDiff, setShowDiff] = React.useState(jobStatus === 'review')
  // The component stays mounted while a running job polls its way into
  // review — open the change the moment the review starts.
  React.useEffect(() => { if (jobStatus === 'review') setShowDiff(true) }, [jobStatus])
  const [busy, setBusy] = React.useState<'approve' | 'reject' | null>(null)
  const [error, setError] = React.useState('')
  const [rejecting, setRejecting] = React.useState(false)
  const [reason, setReason] = React.useState('')
  const mounted = React.useRef(true)
  React.useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const discarded = worktree.status === 'discarded'
  // When a refused merge lands the copy in the clash state, the banner below
  // carries the server's reason — the raw request error would only repeat it.
  React.useEffect(() => { if (worktree.status === 'conflict') setError('') }, [worktree.status])
  React.useEffect(() => {
    if (discarded) {
      // A discarded copy has no diff to fetch (and any previously loaded one
      // would render only until a reload) — show the verdict line alone.
      setDiff(null)
      setDiffError('')
      return
    }
    let cancelled = false
    setDiff(null)
    setDiffError('')
    getJobDiff(token, jobId)
      .then(body => { if (!cancelled) setDiff(body) })
      .catch(cause => { if (!cancelled) setDiffError(String(cause)) })
    return () => { cancelled = true }
    // Re-fetch when the lifecycle moves (e.g. merged: the diff is then read
    // off the code area's own history).
  }, [token, jobId, worktree.status, discarded])

  async function approve() {
    if (busy) return
    setBusy('approve')
    setError('')
    try {
      await onApprove()
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
      onChanged() // even a refused merge changes state (clash is recorded)
    }
  }

  async function reject() {
    if (busy || !reason.trim()) return
    setBusy('reject')
    setError('')
    try {
      await rejectJob(token, jobId, reason.trim())
      if (mounted.current) setRejecting(false)
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
      onChanged()
    }
  }

  const files = diff ? parseUnifiedPatch(diff.patch) : []
  const merged = worktree.status === 'merged'

  return <section className="changes-review" aria-label="Code changes">
    <div className="changes-head">
      <strong>Changes</strong>
      {diff?.summary && <span className="muted changes-summary">{diff.summary}</span>}
      {jobStatus !== 'review' && !discarded && <button className="ghost-button changes-toggle" onClick={() => setShowDiff(current => !current)}>
        {showDiff ? 'Hide changes' : 'Show changes'}
      </button>}
    </div>

    {merged && <p className="changes-note is-merged">
      ✓ Changes merged into <code>{worktree.base_branch}</code>
      {worktree.merge_commit && <> · <code>{short(worktree.merge_commit)}</code></>}
    </p>}
    {discarded && <p className="changes-note is-discarded">
      ✕ Changes discarded{rejectedReason ? <> — {rejectedReason}</> : null}
    </p>}
    {/* 'conflict' covers every refused merge — true clashes with newer work
        AND refusals like uncommitted files in the project. The headline stays
        neutral; the server's reason below says which one it was. */}
    {worktree.status === 'conflict' && <div className="changes-conflict" role="alert">
      <p><strong>These changes could not be brought in.</strong></p>
      {worktree.error && <p className="changes-conflict-detail">{worktree.error}</p>}
      <p>Nothing in your project was changed. Fix the reason above in the project folder, then approve again.</p>
    </div>}
    {jobStatus === 'review' && worktree.status === 'active' && <p className="changes-note">
      This job worked in an isolated copy of the code. Approve to bring the changes
      into <code>{worktree.base_branch}</code> — your project is untouched until then.
    </p>}

    {error && <div className="error-bar">{error}</div>}
    {diffError && !discarded && <div className="error-bar">{diffError}</div>}

    {showDiff && diff && <>
      <ul className="changes-files">
        {diff.files.map(file => <li key={file.path}>
          <span className={`changes-file-status is-${fileStatusLabel(file.status)}`}>{fileStatusLabel(file.status)}</span>
          <span className="changes-file-path">{file.old_path ? `${file.old_path} → ${file.path}` : file.path}</span>
        </li>)}
        {diff.files.length === 0 && <li className="muted">No file changes.</li>}
      </ul>
      {files.length > 0 && <div className="diff-view">
        {files.map(file => <div className="diff-file" key={file.path}>
          <div className="diff-file-head">{file.path}</div>
          <pre className="diff-body">
            {file.lines.filter(line => line.kind !== 'meta').map((line, index) =>
              <span key={index} className={`diff-line is-${line.kind}`}>{line.text || ' '}{'\n'}</span>)}
          </pre>
        </div>)}
        {diff.patch_truncated && <p className="muted changes-truncated">The change is very large — this preview is cut short, but the file list above is complete.</p>}
      </div>}
    </>}

    {canDecide && jobStatus === 'review' && !discarded && !merged && <div className="changes-actions">
      {!rejecting && <>
        <button className="primary-button" onClick={() => void approve()} disabled={!!busy}>
          {busy === 'approve'
            ? 'Merging…'
            : worktree.status === 'conflict' ? 'Approve again' : '✓ Approve & merge changes'}
        </button>
        <button className="ghost-button danger" onClick={() => { setRejecting(true); setError('') }} disabled={!!busy}>Reject…</button>
      </>}
      {rejecting && <form className="changes-reject-form" onSubmit={event => { event.preventDefault(); void reject() }}>
        <input
          autoFocus
          type="text"
          value={reason}
          maxLength={500}
          onChange={event => setReason(event.target.value)}
          placeholder="Why is this rejected? (required — kept on the job record)"
          aria-label="Rejection reason"
        />
        <button type="submit" className="primary-button danger" disabled={!!busy || !reason.trim()}>
          {busy === 'reject' ? 'Discarding…' : 'Reject & discard changes'}
        </button>
        <button type="button" className="ghost-button" onClick={() => setRejecting(false)} disabled={!!busy}>Keep reviewing</button>
      </form>}
    </div>}
    {!canDecide && jobStatus === 'review' && decideBlockedNote && <p className="muted changes-blocked">{decideBlockedNote}</p>}
  </section>
}
