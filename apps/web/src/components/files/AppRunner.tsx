import React from 'react'
import { appExitSummary, appStart, appStop, detectApps, type DetectedApp } from '../../api/files'
import { confirmDialog } from '../ui/Dialog'
import { splitRefusal } from '../../lib/refusal'
import { useAppStatus } from './useAppStatus'

// Run a project's dev server as a managed process. This is the CONTROLS half of
// Run & Preview: which command, which folder, which port, Run and Stop, the
// owner-power consent, the command logs, and every fail-closed refusal with the
// next step that clears it. It lives in the right dock, next to whatever the
// owner is working on.
//
// Since #147 (ADR-0043 decision 4) it does not frame the app. The running app
// renders in the Artifacts main window (`AppViewport`), because a live app is
// worth the widest surface in the product and a 420px-wide panel was never it.
// What stays here is a compact status - Ready/Starting, the command, the port -
// and one action that brings that viewport up. Starting an app opens it
// automatically, so Run puts the app in front of the owner without a second
// click.
const PORT_PIN_KEY = 'proxima.appport.v2.'
const LEGACY_PORT_PIN_KEY = 'proxima.appport.'
// The owner-power acknowledgement is asked once per browser, then persisted.
// It used to be a component ref, which re-asked on every panel mount and every
// project switch - pure friction for a single owner who already accepted what
// "runs with your account permissions" means (PRUNE-SPEC B6). Global, not
// per-project: the dialog explains a category of power, not a project fact.
const OWNER_POWER_ACK_KEY = 'proxima.ownerpower.ack'
export function AppRunner({ token, slug, onClose, onOpenViewport, initialDir, initialCommand }: {
  token: string
  slug: string
  onClose: () => void
  /**
   * Bring this project's app viewport up in the main window (#147). Absent
   * where the shell cannot route one, and then "Show app" is absent too -
   * a control whose destination does not exist is worse than no control.
   */
  onOpenViewport?: () => void
  initialDir?: string
  initialCommand?: string
}) {
  const [command, setCommand] = React.useState(() => initialCommand || localStorage.getItem('proxima.appcmd.' + slug) || 'npm run dev')
  const [dir, setDir] = React.useState(() => initialDir || localStorage.getItem('proxima.appdir.' + slug) || '')
  // 0 = auto: let the server take any free port. Only a deliberate pin is stored.
  // Pins live under a versioned key. The unversioned one cannot be trusted: it
  // held the old hardcoded 5180 default, and later a build that echoed the
  // server's own auto-assigned port back into the box — neither is an owner
  // choice, and honouring them pins previews to ports nothing should hold.
  // Read the versioned key only and drop the old one on sight.
  const [port, setPort] = React.useState(() => {
    localStorage.removeItem(LEGACY_PORT_PIN_KEY + slug)
    return Number(localStorage.getItem(PORT_PIN_KEY + slug)) || 0
  })
  // Mirror the reported port into the editable box only when the owner has to
  // act on it — a conflict or unverified ownership, where "Change port" is the
  // way out. On a healthy run it stays read-only: writing it back would
  // silently turn "auto" into a pin that the next start must honour, and then
  // fail closed the first time anything else held that port.
  const { status, setStatus, refresh: poll } = useAppStatus(token, slug, next => {
    const candidatePort = next.requested_port ?? next.port
    const needsOwnerChoice = next.state === 'port_conflict' || next.state === 'ownership_unknown'
    if (candidatePort != null && needsOwnerChoice) setPort(candidatePort)
  })
  const [apps, setApps] = React.useState<DetectedApp[]>([])
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState('')
  const [showLogs, setShowLogs] = React.useState(false)
  const portInputRef = React.useRef<HTMLInputElement>(null)
  const mountedRef = React.useRef(true)
  const actionSeq = React.useRef(0)
  const appsSeq = React.useRef(0)

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      actionSeq.current += 1
      appsSeq.current += 1
    }
  }, [])

  React.useEffect(() => {
    actionSeq.current += 1
    appsSeq.current += 1
    setApps([])
    setBusy(false)
    setError('')
    setShowLogs(false)
  }, [slug])

  React.useEffect(() => {
    const seq = ++appsSeq.current
    detectApps(token, slug)
      .then(r => {
        if (!mountedRef.current || seq !== appsSeq.current) return
        setApps(r.apps)
        // One clear match and the form is still on defaults → fill it so Run
        // does the right thing without an extra click (empty projects stay on npm).
        if (r.apps.length === 1 && !initialDir && !initialCommand) {
          const savedCmd = localStorage.getItem('proxima.appcmd.' + slug)
          const savedDir = localStorage.getItem('proxima.appdir.' + slug)
          const atDefault = (!savedCmd || savedCmd === 'npm run dev') && !savedDir
          if (atDefault) {
            setDir(r.apps[0].dir)
            setCommand(r.apps[0].command)
          }
        }
      })
      .catch(() => { if (mountedRef.current && seq === appsSeq.current) setApps([]) })
    return () => { appsSeq.current += 1 }
  }, [token, slug, initialDir, initialCommand])
  React.useEffect(() => {
    if (initialDir != null) setDir(initialDir)
    if (initialCommand) setCommand(initialCommand)
  }, [initialDir, initialCommand])
  const close = () => { if (!busy) onClose() }
  const pick = (a: DetectedApp) => {
    if (busy) return
    setDir(a.dir)
    setCommand(a.command)
  }

  async function run() {
    if (busy) return
    const cmd = command.trim()
    if (!cmd) return
    if (localStorage.getItem(OWNER_POWER_ACK_KEY) !== '1') {
      const displayDir = dir.trim() ? `${slug}/${dir.trim()}` : `${slug} (project root)`
      const ok = await confirmDialog({
        title: 'Run project command?',
        message: `Proxima will run "${cmd}" in ${displayDir} with your account permissions. This can read and write project files, install dependencies, and start local servers.`,
        confirmLabel: 'Run app',
      })
      if (!ok) return
      localStorage.setItem(OWNER_POWER_ACK_KEY, '1')
    }
    setError(''); setBusy(true)
    localStorage.setItem('proxima.appcmd.' + slug, cmd); localStorage.setItem('proxima.appdir.' + slug, dir)
    // Empty box = auto; do not persist a port the owner never chose.
    if (port) localStorage.setItem(PORT_PIN_KEY + slug, String(port))
    else localStorage.removeItem(PORT_PIN_KEY + slug)
    const seq = ++actionSeq.current
    try {
      await appStart(token, slug, cmd, port, dir)
      // The app the owner just started is what they want to look at: put its
      // viewport in the main window now rather than making them find it (#147).
      // Guarded like every other post-await step: a panel closed or a project
      // switched during the start round trip must not drag the shell to another
      // Container's app.
      if (mountedRef.current && seq === actionSeq.current) {
        onOpenViewport?.()
        poll()
      }
    }
    catch (e) {
      if (mountedRef.current && seq === actionSeq.current) {
        setError(String(e))
        await poll()
      }
    }
    finally { if (mountedRef.current && seq === actionSeq.current) setBusy(false) }
  }
  async function stop(): Promise<boolean> {
    if (busy) return false
    setBusy(true)
    const seq = ++actionSeq.current
    let succeeded = false
    try {
      await appStop(token, slug)
      succeeded = true
    } catch (e) {
      if (mountedRef.current && seq === actionSeq.current) setError(String(e))
    }
    if (mountedRef.current && seq === actionSeq.current) {
      await poll()
      setBusy(false)
    }
    return succeeded && mountedRef.current && seq === actionSeq.current
  }

  async function changePort() {
    if (busy) return
    const stopped = await stop()
    if (!mountedRef.current || !stopped) return
    setStatus({ state: 'stopped', running: false, ready: false })
    setError('')
    window.setTimeout(() => {
      portInputRef.current?.focus()
      portInputRef.current?.select()
    }, 0)
  }

  const exitInfo = status.state === 'exited' ? appExitSummary(status) : null
  const conflictPort = status.requested_port ?? port
  const hasLogs = (status.log || []).length > 0
  const logText = hasLogs ? (status.log || []).join('\n') : 'No command logs yet.'
  const stateActions = (options: { retry?: boolean; changePort?: boolean; stop?: boolean; showApp?: boolean }) => <div className="app-state-actions">
    {options.stop !== false && <button className="ghost-button sm danger" onClick={() => void stop()} disabled={busy}>Stop</button>}
    <button className="ghost-button sm" onClick={() => setShowLogs(value => !value)}>{showLogs ? 'Hide logs' : 'View logs'}</button>
    {options.showApp && onOpenViewport && <button className="ghost-button sm" onClick={onOpenViewport}>Show app</button>}
    {options.retry && <button className="ghost-button sm" onClick={() => void run()} disabled={busy}>Retry</button>}
    {options.changePort && <button className="primary-button sm" onClick={() => void changePort()} disabled={busy}>Change port</button>}
  </div>

  // Governance may refuse; it may never refuse silently (prune B5, #133). The
  // server ends every refusal message with its next step and repeats it in
  // `next_step`; splitting them keeps the instruction on its own line instead
  // of printing it twice.
  const refusalCard = (fallbackReason: string) => {
    const { reason, nextStep } = splitRefusal(status.message || fallbackReason, status.next_step)
    return <>
      <p>{reason || fallbackReason}</p>
      {nextStep && <p className="app-next-step">{nextStep}</p>}
    </>
  }

  return <div className="app-runner-dock">
    <div className="app-runner-head">
      <strong>Run &amp; Preview</strong>
      {status.running && <span className={`app-ready-badge ${status.ready ? 'ready' : 'starting'}`}>{status.ready ? '● Ready' : status.state === 'ownership_unknown' ? '● Blocked' : '◌ Starting…'}</span>}
      {status.state === 'port_conflict' && <span className="app-ready-badge failed">● Port conflict</span>}
      {exitInfo && <span className={`app-ready-badge ${exitInfo.tone === 'fail' ? 'failed' : 'finished'}`}>{exitInfo.tone === 'fail' ? '● Failed' : '● Finished'}</span>}
      <span className="spacer" />
      <button className="icon-button" onClick={close} disabled={busy} aria-label="Close">✕</button>
    </div>

    {status.running && status.broad_bind && <div className="app-bind-warning" role="alert">
      ⚠ This dev server is listening on all network interfaces - other devices on your network can reach it
      directly, with no authentication. Bind it to <code>127.0.0.1</code> (e.g. <code>--host 127.0.0.1</code>);
      remote preview still works through Proxima's gated relay.
    </div>}

    {!status.running && <div className="app-runner-setup">
      {apps.length > 0 && <div className="app-detected">
        <span className="app-detected-label">Detected apps — pick one:</span>
        <div className="app-detected-list">{apps.map((a, i) => <button key={i} className={`app-detected-item ${dir === a.dir && command === a.command ? 'active' : ''}`} onClick={() => pick(a)} disabled={busy}>
          <span className="app-detected-dir">{a.dir || '(project root)'}</span><span className="app-detected-kind">{a.kind}</span>
        </button>)}</div>
      </div>}
      {apps.length === 0 && <p className="app-runner-empty muted">No app detected here yet. Add a <code>package.json</code>, <code>app.py</code>/<code>main.py</code>, Django <code>manage.py</code>, or <code>index.html</code> — or type a command below.</p>}
      <div className="app-runner-power">
        <span>Owner-power execution</span>
        <small>Runs the selected command inside this project with your account permissions.</small>
      </div>
      <div className="app-runner-bar">
        <input className="ui-select app-dir" value={dir} onChange={e => setDir(e.target.value)} placeholder="folder (root)" disabled={busy || status.running} />
        <input className="ui-select" value={command} onChange={e => setCommand(e.target.value)} placeholder="npm run dev" disabled={busy || status.running} />
        <input ref={portInputRef} className="ui-select app-port" type="number" value={port || ''} placeholder="auto" onChange={e => setPort(Number(e.target.value) || 0)} title="Leave empty to let Proxima pick a free port. Set one only when the app needs a fixed port; preview opens only after Proxima verifies ownership." disabled={busy || status.running} />
        <button className="primary-button" onClick={() => void run()} disabled={busy || !command.trim()}>▶ Run</button>
      </div>
      <p className="app-runner-cwd muted">Working dir: <code>{slug}/{dir || ''}</code> · command runs here</p>
      {status.state === 'stopped' && <section className="app-state-card" role="status">
        <h3>{status.command ? 'App stopped' : 'Command logs'}</h3>
        {refusalCard(status.command ? 'The managed app is stopped. Its most recent bounded log buffer is still available.' : 'No app is running. Command output will appear here after you run one.')}
        {stateActions({ retry: Boolean(status.command), changePort: Boolean(status.command), stop: false })}
        {showLogs && <pre className="app-log">{logText}</pre>}
      </section>}
      {status.state === 'port_conflict' && <section className="app-state-card danger" role="alert">
        <h3>Port {conflictPort} is already in use</h3>
        {refusalCard(`Another process claimed port ${conflictPort}. Proxima did not open, proxy, or stop it.`)}
        {stateActions({ retry: true, changePort: true })}
        {showLogs && <pre className="app-log">{logText}</pre>}
      </section>}
      {error && status.state !== 'port_conflict' && status.reason !== 'output_sink_unavailable' && <p className="error-text">{error}</p>}
      {exitInfo && <div className={`app-exit-note ${exitInfo.tone}`} role="status">
        <strong>{exitInfo.title}</strong>
        <p>{exitInfo.hint}</p>
        <div className="app-state-actions">
          <button className="ghost-button sm" onClick={() => setShowLogs(value => !value)}>{showLogs ? 'Hide logs' : 'View logs'}</button>
          <button className="ghost-button sm" onClick={() => void run()} disabled={busy}>Retry</button>
          <button className="primary-button sm" onClick={() => void changePort()} disabled={busy}>Change port</button>
        </div>
      </div>}
      {status.state === 'exited' && showLogs && <pre className="app-log">{logText}</pre>}
    </div>}

    {status.running && status.ready && showLogs && <section className="app-ready-logs" aria-label="Command logs">
      <pre className="app-log">{logText}</pre>
    </section>}
    {/* The compact status the dock keeps once the viewport owns the picture
        (#147): what is running, where, and one way to bring it up. Stop stays
        here because starting and stopping are the controls' job. */}
    {status.running && status.ready && <section className="app-run-status" role="status">
      <h3>Running in the main window</h3>
      <p className="app-run-status-meta">
        <code>{status.command}</code>{status.port != null && <> · port {status.port}</>}
      </p>
      <p className="muted">The live app renders in the Artifacts main window, at full width.</p>
      <div className="app-state-actions">
        {onOpenViewport && <button className="primary-button sm" onClick={onOpenViewport}>Show app</button>}
        <button className="ghost-button sm" onClick={() => setShowLogs(value => !value)} aria-expanded={showLogs}>{showLogs ? 'Hide logs' : 'Logs'}</button>
        <button className="ghost-button sm danger" onClick={() => void stop()} disabled={busy}>Stop</button>
      </div>
    </section>}
    {status.state === 'ownership_unknown' && <div className="app-booting">
      <section className="app-state-card warning" role="alert">
        <h3>Preview ownership could not be verified</h3>
        {refusalCard('A server answered on this port and Proxima cannot verify who owns it, so it will not proxy it.')}
        {stateActions({})}
        {showLogs && <pre className="app-log">{logText}</pre>}
      </section>
    </div>}
    {status.state === 'starting' && status.prolonged_start && <div className="app-booting">
      <section className="app-state-card warning" role="status">
        <h3>Still waiting for a preview server</h3>
        <p>This is taking longer than expected. The command is still running, but no ownership-verified server is ready.</p>
        {stateActions({ showApp: true })}
        {showLogs && <pre className="app-log">{logText}</pre>}
      </section>
    </div>}
    {status.state === 'starting' && !status.prolonged_start && <div className="app-booting">
      <div className="app-booting-inner">
        <span className="app-spinner" /><strong>Starting your app…</strong>
        <p className="muted">Running <code>{status.command}</code> — waiting for the server to come up.</p>
        {stateActions({ showApp: true })}
        {showLogs && <pre className="app-log">{hasLogs ? (status.log || []).slice(-12).join('\n') : logText}</pre>}
      </div>
    </div>}
  </div>
}
