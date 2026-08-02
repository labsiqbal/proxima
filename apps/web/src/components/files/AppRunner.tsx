import React from 'react'
import { appExitSummary, appStart, appStop, appStatus, appViewUrl, detectApps, getPublicConfig, previewAuth, type AppStatus, type DetectedApp } from '../../api/files'
import { IconMonitor, IconTablet, IconMobile } from '../shell/icons'
import { confirmDialog } from '../ui/Dialog'
import { usePolling } from '../../hooks/usePolling'

const VIEWPORTS = [
  { key: 'desktop', label: 'Desktop', w: '100%', Icon: IconMonitor },
  { key: 'tablet', label: 'Tablet', w: '834px', Icon: IconTablet },
  { key: 'mobile', label: 'Mobile', w: '390px', Icon: IconMobile },
] as const
type VKey = typeof VIEWPORTS[number]['key']

// Run a project's dev server as a managed process and preview it live — docked
// panel (not a popup), with viewport presets like a real preview tool.
const PORT_PIN_KEY = 'proxima.appport.v2.'
const LEGACY_PORT_PIN_KEY = 'proxima.appport.'
// The owner-power acknowledgement is asked once per browser, then persisted.
// It used to be a component ref, which re-asked on every panel mount and every
// project switch - pure friction for a single owner who already accepted what
// "runs with your account permissions" means (PRUNE-SPEC B6). Global, not
// per-project: the dialog explains a category of power, not a project fact.
const OWNER_POWER_ACK_KEY = 'proxima.ownerpower.ack'
export function AppRunner({ token, slug, onClose, initialDir, initialCommand }: { token: string; slug: string; onClose: () => void; initialDir?: string; initialCommand?: string }) {
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
  const [appsDomain, setAppsDomain] = React.useState<string | null>(null)
  React.useEffect(() => {
    getPublicConfig(token).then(c => setAppsDomain(c.apps_domain)).catch(() => undefined)
    void previewAuth(token).catch(() => undefined)  // mint the preview cookie so iframes load without a CF Access login
  }, [token])
  const [status, setStatus] = React.useState<AppStatus>({ state: 'stopped', running: false, ready: false })
  const [apps, setApps] = React.useState<DetectedApp[]>([])
  const [vw, setVw] = React.useState<VKey>('desktop')
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState('')
  const [showLogs, setShowLogs] = React.useState(false)
  const [reloadKey, setReloadKey] = React.useState(0)
  const portInputRef = React.useRef<HTMLInputElement>(null)
  const mountedRef = React.useRef(true)
  const statusSeq = React.useRef(0)
  const actionSeq = React.useRef(0)
  const appsSeq = React.useRef(0)

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      statusSeq.current += 1
      actionSeq.current += 1
      appsSeq.current += 1
    }
  }, [])

  React.useEffect(() => {
    statusSeq.current += 1
    actionSeq.current += 1
    appsSeq.current += 1
    setStatus({ state: 'stopped', running: false, ready: false })
    setApps([])
    setBusy(false)
    setError('')
    setShowLogs(false)
    setReloadKey(0)
  }, [slug])

  const poll = React.useCallback(async () => {
    const seq = ++statusSeq.current
    try {
      const next = await appStatus(token, slug)
      if (mountedRef.current && seq === statusSeq.current) {
        setStatus(next)
        // Mirror the port into the editable box only when the owner has to act
        // on it — a conflict or unverified ownership, where "Change port" is the
        // way out. On a healthy run it stays read-only: writing it back would
        // silently turn "auto" into a pin that the next start must honour, and
        // then fail closed the first time anything else held that port.
        const candidatePort = next.requested_port ?? next.port
        const needsOwnerChoice = next.state === 'port_conflict' || next.state === 'ownership_unknown'
        if (candidatePort != null && needsOwnerChoice) setPort(candidatePort)
      }
    } catch { /* a stopped or booting app is represented by the last known status */ }
  }, [token, slug])
  usePolling(poll, 2000, { restartKey: `${token}:${slug}` })
  // Reload the preview the moment a (new) server comes up — covers Stop → Run
  // another, even when both use the same port (the iframe URL wouldn't change).
  const prevReady = React.useRef(false)
  const prevPort = React.useRef<number | undefined>(undefined)
  React.useEffect(() => {
    const ready = !!(status.running && status.ready)
    if (ready && (!prevReady.current || status.port !== prevPort.current)) setReloadKey(k => k + 1)
    prevReady.current = ready
    prevPort.current = status.port
  }, [status.running, status.ready, status.port])
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
      window.setTimeout(() => { if (mountedRef.current && seq === actionSeq.current) setReloadKey(k => k + 1) }, 1800)
      if (mountedRef.current && seq === actionSeq.current) poll()
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

  // Prefer an isolated origin: remote apps-domain subdomain when configured, else
  // the app's preview relay port on the same host (local and remote). Absolute
  // asset paths and HMR websockets work on that origin, gated by the
  // proxima_preview cookie (host-scoped cookies ignore ports) and
  // credential-stripped upstream. Fall back to same-origin appview only when no
  // isolated origin is available.
  const isRemote = location.hostname !== 'localhost' && location.hostname !== '127.0.0.1'
  const subdomainUrl = appsDomain && isRemote && status.running && status.ready ? `${location.protocol}//preview-${slug}.${appsDomain}/` : ''
  // The relay speaks plain HTTP only. From an https page (Tailscale Serve, a
  // fronting proxy) an https://host:relayPort iframe dies in the TLS handshake
  // and an http:// one is blocked as mixed content — so https origins skip the
  // relay and use the same-origin /api/appview proxy, which rides the page's own
  // TLS. The relay stays preferred on http origins for its isolated origin.
  const relayUrl = !subdomainUrl && status.running && status.preview_port && location.protocol === 'http:'
    ? `http://${location.hostname}:${status.preview_port}/`
    : ''
  // A freshly-provisioned preview subdomain can lag on DNS for a few seconds (and a
  // relay iframe can race the preview-auth cookie mint). Retry the frame ONLY while
  // it hasn't loaded yet — the iframe's onLoad clears this — so a preview that loads
  // first try (the normal case now) never visibly reloads.
  const previewLoadedRef = React.useRef(false)
  React.useEffect(() => {
    previewLoadedRef.current = false
    if (!subdomainUrl && !relayUrl) return
    const timers = [5000, 12000].map(ms => setTimeout(() => { if (!previewLoadedRef.current) setReloadKey(k => k + 1) }, ms))
    return () => timers.forEach(clearTimeout)
  }, [subdomainUrl, relayUrl])
  const baseUrl = subdomainUrl || relayUrl || appViewUrl(slug)
  // Cache-bust per (re)load so switching apps on the same port doesn't show a
  // cached page; static servers don't send no-cache headers.
  const previewUrl = `${baseUrl}${baseUrl.includes('?') ? '&' : '?'}_proxima=${reloadKey}`
  const openUrl = subdomainUrl || relayUrl || appViewUrl(slug)
  const isolatedOrigin = Boolean(subdomainUrl || relayUrl)
  const width = VIEWPORTS.find(v => v.key === vw)?.w || '100%'
  const exitInfo = status.state === 'exited' ? appExitSummary(status) : null
  const conflictPort = status.requested_port ?? port
  const hasLogs = (status.log || []).length > 0
  const logText = hasLogs ? (status.log || []).join('\n') : 'No command logs yet.'
  const stateActions = (options: { retry?: boolean; changePort?: boolean; stop?: boolean }) => <div className="app-state-actions">
    {options.stop !== false && <button className="ghost-button sm danger" onClick={() => void stop()} disabled={busy}>Stop</button>}
    <button className="ghost-button sm" onClick={() => setShowLogs(value => !value)}>{showLogs ? 'Hide logs' : 'View logs'}</button>
    {options.retry && <button className="ghost-button sm" onClick={() => void run()} disabled={busy}>Retry</button>}
    {options.changePort && <button className="primary-button sm" onClick={() => void changePort()} disabled={busy}>Change port</button>}
  </div>

  return <div className="app-runner-dock">
    <div className="app-runner-head">
      <strong>Run &amp; Preview</strong>
      {status.running && <span className={`app-ready-badge ${status.ready ? 'ready' : 'starting'}`}>{status.ready ? '● Ready' : status.state === 'ownership_unknown' ? '● Blocked' : '◌ Starting…'}</span>}
      {status.state === 'port_conflict' && <span className="app-ready-badge failed">● Port conflict</span>}
      {exitInfo && <span className={`app-ready-badge ${exitInfo.tone === 'fail' ? 'failed' : 'finished'}`}>{exitInfo.tone === 'fail' ? '● Failed' : '● Finished'}</span>}
      {status.running && status.ready && <div className="vp-seg">{VIEWPORTS.map(v => <button key={v.key} className={vw === v.key ? 'active' : ''} onClick={() => setVw(v.key)} title={v.label} aria-label={v.label}><v.Icon size={16} /></button>)}</div>}
      <span className="spacer" />
      {status.running && status.ready && <><a className="ghost-button sm app-act" href={openUrl} target="_blank" rel="noreferrer" title="Open in new tab" aria-label="Open in new tab"><span className="act-ico">↗</span><span className="btn-txt">Open</span></a><button className="ghost-button sm app-act" onClick={() => setReloadKey(k => k + 1)} title="Reload" aria-label="Reload"><span className="act-ico">⟳</span><span className="btn-txt">Reload</span></button><button className="ghost-button sm app-act" onClick={() => setShowLogs(value => !value)} title={showLogs ? 'Hide logs' : 'Logs'} aria-label={showLogs ? 'Hide logs' : 'Logs'} aria-expanded={showLogs}><span className="act-ico">≡</span><span className="btn-txt">{showLogs ? 'Hide logs' : 'Logs'}</span></button><button className="ghost-button sm danger app-act" onClick={() => void stop()} disabled={busy} title="Stop" aria-label="Stop"><span className="act-ico">■</span><span className="btn-txt">Stop</span></button></>}
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
        <p>{status.message || (status.command ? 'The managed app is stopped. Its most recent bounded log buffer is still available.' : 'No app is running. Command output will appear here after you run one.')}</p>
        {stateActions({ retry: Boolean(status.command), changePort: Boolean(status.command), stop: false })}
        {showLogs && <pre className="app-log">{logText}</pre>}
      </section>}
      {status.state === 'port_conflict' && <section className="app-state-card danger" role="alert">
        <h3>Port {conflictPort} is already in use</h3>
        <p>{status.message || `Another process claimed port ${conflictPort}. Proxima did not open, proxy, or stop it.`}</p>
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
    {status.running && status.ready && <div className="app-preview-area">
      <div className="app-viewport" style={{ width, maxWidth: '100%' }}>
        <iframe key={reloadKey} className="app-frame" title="App preview" src={previewUrl} onLoad={() => { previewLoadedRef.current = true }} sandbox={isolatedOrigin ? 'allow-scripts allow-same-origin allow-forms allow-popups allow-modals' : 'allow-scripts allow-forms allow-popups allow-modals'} />
      </div>
    </div>}
    {status.state === 'ownership_unknown' && <div className="app-booting">
      <section className="app-state-card warning" role="alert">
        <h3>Preview ownership could not be verified</h3>
        <p>{status.message || 'Proxima cannot verify who owns the listener on this host.'} Proxima will not proxy this port.</p>
        {stateActions({})}
        {showLogs && <pre className="app-log">{logText}</pre>}
      </section>
    </div>}
    {status.state === 'starting' && status.prolonged_start && <div className="app-booting">
      <section className="app-state-card warning" role="status">
        <h3>Still waiting for a preview server</h3>
        <p>This is taking longer than expected. The command is still running, but no ownership-verified server is ready.</p>
        {stateActions({})}
        {showLogs && <pre className="app-log">{logText}</pre>}
      </section>
    </div>}
    {status.state === 'starting' && !status.prolonged_start && <div className="app-booting">
      <div className="app-booting-inner">
        <span className="app-spinner" /><strong>Starting your app…</strong>
        <p className="muted">Running <code>{status.command}</code> — waiting for the server to come up.</p>
        {stateActions({})}
        {showLogs && <pre className="app-log">{hasLogs ? (status.log || []).slice(-12).join('\n') : logText}</pre>}
      </div>
    </div>}
  </div>
}
