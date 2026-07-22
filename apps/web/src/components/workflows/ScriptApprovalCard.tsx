import React from 'react'
import { getGraphNodeScript, type GraphNodeScript } from '../../api/graph'

// The script-trust approval surface (T6 + audit F4): the owner approves BYTES,
// not a filename. The card fetches the script's current content and sha256
// together, renders both, and hands that hash to the approve call so the
// server can refuse (409) if the file changed between review and click.
// Until the content has loaded there is nothing to approve — the button waits.
export function ScriptApprovalCard({ token, jobId, nodeId, command, approving, disabled, onApprove }: {
  token: string
  jobId: number
  nodeId: string
  command: string
  approving: boolean
  disabled: boolean
  onApprove: (sha256: string) => void
}) {
  const [script, setScript] = React.useState<GraphNodeScript | null>(null)
  const [error, setError] = React.useState('')
  React.useEffect(() => {
    let live = true
    setScript(null)
    setError('')
    getGraphNodeScript(token, jobId, nodeId)
      .then(loaded => { if (live) setScript(loaded) })
      .catch(cause => { if (live) setError(String(cause)) })
    return () => { live = false }
  }, [token, jobId, nodeId])
  return <div className="graph-script-approval">
    <p>
      This step wants to run <code>scripts/{command}</code>, and this version of
      the script hasn't been approved yet. Review the exact content below —
      approving trusts these bytes until the file changes again.
    </p>
    {error && <p className="error-text">{error}</p>}
    {script && <>
      <pre className="graph-script-content">{script.content}{script.truncated ? '\n… (truncated for display — the hash covers the whole file)' : ''}</pre>
      <p className="graph-script-hash">
        sha256 <code>{script.sha256}</code>
        {script.trusted_sha256 != null && script.trusted_sha256 !== script.sha256 && ' — changed since the last approved version'}
      </p>
    </>}
    <button
      className="primary-button"
      disabled={disabled || !script}
      title={script ? undefined : 'The script content must load before it can be approved'}
      onClick={() => { if (script) onApprove(script.sha256) }}
    >{approving ? 'Approving…' : 'Approve script & run'}</button>
  </div>
}
