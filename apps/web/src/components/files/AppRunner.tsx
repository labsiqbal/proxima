import React from 'react'
import { appStart, appStop, appStatus, appViewUrl, detectApps, getPublicConfig, previewAuth, type AppStatus, type DetectedApp } from '../../api/files'
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
export function AppRunner({ token, slug, onClose, initialDir, initialCommand }: { token: string; slug: string; onClose: () => void; initialDir?: string; initialCommand?: string }) {
  const [command, setCommand] = React.useState(() => initialCommand || localStorage.getItem('proxima.appcmd.' + slug) || 'npm run dev')
  const [dir, setDir] = React.useState(() => initialDir || localStorage.getItem('proxima.appdir.' + slug) || '')
  const [port, setPort] = React.useState(() => Number(localStorage.getItem('proxima.appport.' + slug)) || 5180)
  const [appsDomain, setAppsDomain] = React.useState<string | null>(null)
  React.useEffect(() => {
    getPublicConfig(token).then(c => setAppsDomain(c.apps_domain)).catch(() => undefined)
    void previewAuth(token).catch(() => undefined)  // mint the preview cookie so iframes load without a CF Access login
  }, [token])
  const [status, setStatus] = React.useState<AppStatus>({ running: false })
  const [apps, setApps] = React.useState<DetectedApp[]>([])
  const [vw, setVw] = React.useState<VKey>('desktop')
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState('')
  const [reloadKey, setReloadKey] = React.useState(0)
  const mountedRef = React.useRef(true)
  const statusSeq = React.useRef(0)
  const actionSeq = React.useRef(0)
  const appsSeq = React.useRef(0)
  const ownerPowerAck = React.useRef(false)

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
    setStatus({ running: false })
    setApps([])
    setBusy(false)
    setError('')
    setReloadKey(0)
    ownerPowerAck.current = false
  }, [slug])

  const poll = React.useCallback(async () => {
    const seq = ++statusSeq.current
    try {
      const next = await appStatus(token, slug)
      if (mountedRef.current && seq === statusSeq.current) setStatus(next)
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
      .then(r => { if (mountedRef.current && seq === appsSeq.current) setApps(r.apps) })
      .catch(() => { if (mountedRef.current && seq === appsSeq.current) setApps([]) })
    return () => { appsSeq.current += 1 }
  }, [token, slug])
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
    if (!ownerPowerAck.current) {
      const displayDir = dir.trim() ? `${slug}/${dir.trim()}` : `${slug} (project root)`
      const ok = await confirmDialog({
        title: 'Run project command?',
        message: `Proxima will run "${cmd}" in ${displayDir} with your account permissions. This can read and write project files, install dependencies, and start local servers.`,
        confirmLabel: 'Run app',
      })
      if (!ok) return
      ownerPowerAck.current = true
    }
    setError(''); setBusy(true)
    localStorage.setItem('proxima.appcmd.' + slug, cmd); localStorage.setItem('proxima.appport.' + slug, String(port)); localStorage.setItem('proxima.appdir.' + slug, dir)
    const seq = ++actionSeq.current
    try {
      await appStart(token, slug, cmd, port, dir)
      window.setTimeout(() => { if (mountedRef.current && seq === actionSeq.current) setReloadKey(k => k + 1) }, 1800)
      if (mountedRef.current && seq === actionSeq.current) poll()
    }
    catch (e) { if (mountedRef.current && seq === actionSeq.current) setError(String(e)) }
    finally { if (mountedRef.current && seq === actionSeq.current) setBusy(false) }
  }
  async function stop() {
    if (busy) return
    setBusy(true)
    const seq = ++actionSeq.current
    try {
      await appStop(token, slug)
      if (mountedRef.current && seq === actionSeq.current) poll()
    } catch (e) {
      if (mountedRef.current && seq === actionSeq.current) setError(String(e))
    } finally {
      if (mountedRef.current && seq === actionSeq.current) setBusy(false)
    }
  }

  // Remote: use the app's isolated preview subdomain (Cloudflare apps domain), or —
  // without one — the app's preview relay port on the same host: its own origin, so
  // absolute asset paths and HMR websockets work, gated by the proxima_preview
  // cookie (host-scoped cookies ignore ports) and credential-stripped upstream.
  // Local: use the *other* loopback hostname, because browser cookies are
  // host-scoped but not port-scoped; this keeps Proxima's localhost cookie away
  // from project code without a container. The same-origin sub-path proxy remains
  // only as a last resort (relay disabled): its opaque sandbox drops the session
  // cookie on subresources and absolute paths escape the prefix.
  const isRemote = location.hostname !== 'localhost' && location.hostname !== '127.0.0.1'
  const subdomainUrl = appsDomain && isRemote && status.running && status.ready ? `${location.protocol}//preview-${slug}.${appsDomain}/` : ''
  const relayUrl = !subdomainUrl && isRemote && status.running && status.preview_port ? `${location.protocol}//${location.hostname}:${status.preview_port}/` : ''
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
  const isolatedLoopbackHost = location.hostname === 'localhost' ? '127.0.0.1' : location.hostname === '127.0.0.1' ? 'localhost' : ''
  const directUrl = isolatedLoopbackHost && status.running && status.port ? `${location.protocol}//${isolatedLoopbackHost}:${status.port}/` : ''
  const baseUrl = subdomainUrl || relayUrl || directUrl || appViewUrl(slug)
  // Cache-bust per (re)load so switching apps on the same port doesn't show a
  // cached page; static servers don't send no-cache headers.
  const previewUrl = `${baseUrl}${baseUrl.includes('?') ? '&' : '?'}_proxima=${reloadKey}`
  const openUrl = subdomainUrl || relayUrl || directUrl || appViewUrl(slug)
  const isolatedOrigin = Boolean(subdomainUrl || relayUrl || directUrl)
  const width = VIEWPORTS.find(v => v.key === vw)?.w || '100%'

  return <div className="app-runner-dock">
    <div className="app-runner-head">
      <strong>Run &amp; Preview</strong>
      {status.running && <span className={`app-ready-badge ${status.ready ? 'ready' : 'starting'}`}>{status.ready ? '● Ready' : '◌ Starting…'}</span>}
      {status.running && status.ready && <div className="vp-seg">{VIEWPORTS.map(v => <button key={v.key} className={vw === v.key ? 'active' : ''} onClick={() => setVw(v.key)} title={v.label} aria-label={v.label}><v.Icon size={16} /></button>)}</div>}
      <span className="spacer" />
      {status.running && status.ready && <><a className="ghost-button sm app-act" href={openUrl} target="_blank" rel="noreferrer" title="Open in new tab"><span className="act-ico">↗</span><span className="btn-txt">Open</span></a><button className="ghost-button sm app-act" onClick={() => setReloadKey(k => k + 1)} title="Reload"><span className="act-ico">⟳</span><span className="btn-txt">Reload</span></button><button className="ghost-button sm danger app-act" onClick={() => void stop()} disabled={busy} title="Stop"><span className="act-ico">■</span><span className="btn-txt">Stop</span></button></>}
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
      <div className="app-runner-power">
        <span>Owner-power execution</span>
        <small>Runs the selected command inside this project with your account permissions.</small>
      </div>
      <div className="app-runner-bar">
        <input className="ui-select app-dir" value={dir} onChange={e => setDir(e.target.value)} placeholder="folder (root)" disabled={busy || status.running} />
        <input className="ui-select" value={command} onChange={e => setCommand(e.target.value)} placeholder="npm run dev" disabled={busy || status.running} />
        <input className="ui-select app-port" type="number" value={port} onChange={e => setPort(Number(e.target.value) || 5180)} title="Port hint (also $PORT); Proxima auto-detects the real port too" disabled={busy || status.running} />
        <button className="primary-button" onClick={() => void run()} disabled={busy || !command.trim()}>▶ Run</button>
      </div>
      <p className="app-runner-cwd muted">Working dir: <code>{slug}/{dir || ''}</code> · command runs here</p>
      {error && <p className="error-text">{error}</p>}
      {status.exited && <pre className="app-log">{(status.log || []).join('\n')}</pre>}
    </div>}

    {status.running && status.ready && <div className="app-preview-area">
      <div className="app-viewport" style={{ width, maxWidth: '100%' }}>
        <iframe key={reloadKey} className="app-frame" title="App preview" src={previewUrl} onLoad={() => { previewLoadedRef.current = true }} sandbox={isolatedOrigin ? 'allow-scripts allow-same-origin allow-forms allow-popups allow-modals' : 'allow-scripts allow-forms allow-popups allow-modals'} />
      </div>
    </div>}
    {status.running && !status.ready && <div className="app-booting">
      <div className="app-booting-inner">
        <span className="app-spinner" /><strong>Starting your app…</strong>
        <p className="muted">Running <code>{status.command}</code> — waiting for the server to come up.</p>
        {(status.log || []).length > 0 && <pre className="app-log">{(status.log || []).slice(-12).join('\n')}</pre>}
      </div>
    </div>}
  </div>
}
