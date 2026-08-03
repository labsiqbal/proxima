import React from 'react'
import { getPublicConfig, previewAuth, type AppStatus } from '../../api/files'
import { IconMonitor, IconTablet, IconMobile } from '../shell/icons'
import { splitRefusal } from '../../lib/refusal'
import { openRunPreview } from '../../lib/runPreview'
import { appFrameSandbox, appPreviewOrigin } from './appPreview'
import { useAppStatus } from './useAppStatus'

// The running app renders in the Artifacts MAIN WINDOW (#147, ADR-0043
// decision 4) - the widest surface in the app, which is what a live app is
// worth looking at on. The Run controls stay in the dock's Preview tool
// (`AppRunner`): command, folder, port, Run/Stop, the owner-power consent, and
// the command logs. This surface owns only the picture and what you do to the
// picture - device width, reload, open in a new tab.
//
// Nothing about the security model moved with it. The frame's sandbox comes
// from `appPreview.ts`, the same model #140/ADR-0042 shipped: an isolated
// origin (per-app subdomain or the relay port) may be same-origin to itself,
// the same-origin `/api/appview` fallback never may, because that origin holds
// the owner's session.
//
// It polls status itself rather than being handed a snapshot. The dock is not
// its parent and may not even be open, and an app that dies has to stop being
// shown here - a stale page still framed after the server is gone is a lie.

const VIEWPORTS = [
  { key: 'desktop', label: 'Desktop', w: '100%', Icon: IconMonitor },
  { key: 'tablet', label: 'Tablet', w: '834px', Icon: IconTablet },
  { key: 'mobile', label: 'Mobile', w: '390px', Icon: IconMobile },
] as const
type VKey = typeof VIEWPORTS[number]['key']

// What the surface calls the state it cannot frame. Deliberately a title only:
// the diagnosis and the next step come from the server's own refusal
// (`status.message` / `next_step`), and the full picture - logs, Retry, Change
// port - belongs to the Run controls. Restating AppRunner's copy here would put
// one refusal in two files that have to be edited together.
function idleTitle(status: AppStatus): string {
  if (status.state === 'port_conflict') return 'Port conflict'
  if (status.state === 'ownership_unknown') return 'Preview ownership could not be verified'
  if (status.state === 'exited') return 'The command exited'
  return 'This app is not running'
}

export function AppViewport({ token, slug, projectName, backLabel = 'Gallery', onClose }: {
  token: string
  slug: string
  /** Display name for the title; the slug is the fallback. */
  projectName?: string
  /** Where the way back leads - the surface this viewport took over. */
  backLabel?: string
  onClose: () => void
}) {
  const { status } = useAppStatus(token, slug)
  const [appsDomain, setAppsDomain] = React.useState<string | null>(null)
  const [vw, setVw] = React.useState<VKey>('desktop')
  const [reloadKey, setReloadKey] = React.useState(0)
  const mountedRef = React.useRef(true)

  React.useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  React.useEffect(() => {
    getPublicConfig(token).then(config => { if (mountedRef.current) setAppsDomain(config.apps_domain) }).catch(() => undefined)
    // Mint the preview cookie so the frame loads without a fresh Access login.
    void previewAuth(token).catch(() => undefined)
  }, [token])

  // Reload the frame the moment a (new) server comes up - covers Stop → Run
  // another, even when both take the same port (the URL would not change).
  const ready = Boolean(status.running && status.ready)
  const prevReady = React.useRef(false)
  const prevPort = React.useRef<number | undefined>(undefined)
  React.useEffect(() => {
    if (ready && (!prevReady.current || status.port !== prevPort.current)) setReloadKey(key => key + 1)
    prevReady.current = ready
    prevPort.current = status.port
  }, [ready, status.port])

  const { baseUrl, openUrl, isolatedOrigin } = appPreviewOrigin(slug, status, appsDomain)
  // A freshly-provisioned preview subdomain can lag on DNS for a few seconds (and
  // a relay frame can race the preview-auth cookie mint). Retry ONLY while it has
  // not loaded - the frame's onLoad clears this - so a preview that loads first
  // try never visibly reloads.
  const previewLoadedRef = React.useRef(false)
  React.useEffect(() => {
    previewLoadedRef.current = false
    if (!ready || !isolatedOrigin) return
    const timers = [5000, 12000].map(ms => window.setTimeout(() => {
      if (!previewLoadedRef.current && mountedRef.current) setReloadKey(key => key + 1)
    }, ms))
    return () => timers.forEach(timer => window.clearTimeout(timer))
  }, [ready, isolatedOrigin, baseUrl])

  // Cache-bust per (re)load so switching apps on the same port never shows a
  // cached page; static servers send no no-cache headers.
  const previewUrl = `${baseUrl}${baseUrl.includes('?') ? '&' : '?'}_proxima=${reloadKey}`
  const width = VIEWPORTS.find(preset => preset.key === vw)?.w || '100%'
  const { reason, nextStep } = splitRefusal(status.message || '', status.next_step)

  return <section className="app-viewport-surface" aria-label={`App preview: ${projectName || slug}`}>
    <header className="app-viewport-bar">
      <div className="app-viewport-title">
        <button
          type="button"
          className="ghost-button app-viewport-back"
          onClick={onClose}
          aria-label={`Back to ${backLabel.toLowerCase()}`}
        >← {backLabel}</button>
        <h2 title={slug}>App preview: {projectName || slug}</h2>
        {ready
          ? <span className="app-ready-badge ready">● Ready</span>
          : status.running
            ? <span className="app-ready-badge starting">{status.state === 'ownership_unknown' ? '● Blocked' : '◌ Starting…'}</span>
            : <span className="app-ready-badge finished">● Stopped</span>}
        {ready && status.port != null && <span className="app-viewport-port">port {status.port}</span>}
      </div>
      <div className="app-viewport-actions">
        {ready && <div className="vp-seg">{VIEWPORTS.map(preset => <button
          key={preset.key}
          type="button"
          className={vw === preset.key ? 'active' : ''}
          onClick={() => setVw(preset.key)}
          title={preset.label}
          aria-label={preset.label}
        ><preset.Icon size={16} /></button>)}</div>}
        {ready && <a className="ghost-button" href={openUrl} target="_blank" rel="noreferrer" aria-label="Open in new tab">Open in new tab</a>}
        {ready && <button type="button" className="ghost-button" onClick={() => setReloadKey(key => key + 1)}>Reload</button>}
        <button type="button" className="ghost-button" onClick={openRunPreview}>Run controls</button>
      </div>
    </header>

    <div className="app-viewport-area">
      {ready
        ? <div className="app-viewport-stage" data-testid="app-viewport-stage" style={{ width }}>
            <iframe
              key={reloadKey}
              className="app-frame"
              title="App preview"
              src={previewUrl}
              onLoad={() => { previewLoadedRef.current = true }}
              sandbox={appFrameSandbox(isolatedOrigin)}
            />
          </div>
        : status.state === 'starting'
          ? <div className="app-viewport-idle" role="status">
              <span className="app-spinner" />
              <h3>Starting your app…</h3>
              <p className="muted">Running <code>{status.command}</code> — waiting for the server to come up.</p>
            </div>
          : <div className="app-viewport-idle" role="status">
              <h3>{idleTitle(status)}</h3>
              <p className="muted">{reason || 'The Run controls have this app\u2019s logs and what to do next.'}</p>
              {nextStep && <p className="muted">{nextStep}</p>}
            </div>}
    </div>
  </section>
}
