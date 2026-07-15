import React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { listAudit, type AuditEntry } from '../api/audit'
import { getDebugLogs, reapOrphanedJobs, type DebugLogs } from '../api/debug'
import { cancelRun } from '../api/runs'
import { changePassword } from '../api/auth'
import { ApiError } from '../api/client'
import {
  getCollaborationSettings,
  getImageGenSettings,
  getVideoGenSettings,
  saveCollaborationSettings,
  saveImageGenSettings,
  saveVideoGenSettings,
  testImageGenSettings,
  testVideoGenSettings,
  type HiggsfieldSettings,
  type ImageGenSettings,
  type VideoGenSettings,
  getPermissionSettings,
  savePermissionSettings,
} from '../api/settings'
import type { UpdateStatus } from '../api/updates'
import remoteAccessGuide from '../content/remote-access-guide.md?raw'
import type { AppFeatures, Profile, Project, Runner, User } from '../types'
import { RunnersScreen } from './RunnersScreen'
import { WikiScreen } from './WikiScreen'

function CollaborationSettingsPanel({ token }: { token: string }) {
  const [brainstormAgents, setBrainstormAgents] = React.useState<2 | 3>(3)
  const [debateRounds, setDebateRounds] = React.useState<2 | 3 | 4>(2)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState('')
  React.useEffect(() => {
    let alive = true
    getCollaborationSettings(token).then(r => {
      if (alive) { setBrainstormAgents(r.brainstorm_agents); setDebateRounds(r.debate_rounds) }
    }).catch(() => undefined)
    return () => { alive = false }
  }, [token])
  const save = async (nextBrainstorm = brainstormAgents, nextDebate = debateRounds) => {
    setBusy(true); setError('')
    try {
      const r = await saveCollaborationSettings(token, { brainstorm_agents: nextBrainstorm, debate_rounds: nextDebate })
      setBrainstormAgents(r.brainstorm_agents); setDebateRounds(r.debate_rounds)
    } catch (err) { setError(String(err)) } finally { setBusy(false) }
  }
  return <div className="panel"><div className="panel-head"><h3>Collaboration defaults</h3><span>Brainstorm &amp; Debate</span></div>
    <p className="muted">Defaults for multi-agent prompt modes. Brainstorm runs agents in parallel; Debate alternates two agents across rounds before synthesis.</p>
    <div className="settings-rows">
      <span className="srow-label">Brainstorm agents</span>
      <div className="seg sm">{([2, 3] as const).map(n => <button key={n} type="button" className={brainstormAgents === n ? 'active' : ''} disabled={busy} onClick={() => { setBrainstormAgents(n); void save(n, debateRounds) }}>{n}</button>)}</div>
      <span className="srow-label">Debate rounds</span>
      <div className="seg sm">{([2, 3, 4] as const).map(n => <button key={n} type="button" className={debateRounds === n ? 'active' : ''} disabled={busy} onClick={() => { setDebateRounds(n); void save(brainstormAgents, n) }}>{n}</button>)}</div>
    </div>
    {error && <p className="error-text">{error}</p>}
  </div>
}

function PermissionsPanel({ token }: { token: string }) {
  const [on, setOn] = React.useState(true)
  const [busy, setBusy] = React.useState(false)
  React.useEffect(() => { getPermissionSettings(token).then(r => setOn(r.auto_approve)).catch(() => undefined) }, [token])
  const toggle = async () => {
    setBusy(true)
    try { const r = await savePermissionSettings(token, !on); setOn(r.auto_approve) } catch { /* ignore */ } finally { setBusy(false) }
  }
  return <div className="panel"><div className="panel-head"><h3>Agent permissions</h3><span>{on ? 'auto-approve' : 'ask each time'}</span></div>
    <p className="muted">When on, agents run tools (commands, file edits) without asking — no approval cards. Off = you approve each sensitive action. Faster, but removes the safety prompt.</p>
    <button className={`toggle-pill ${on ? 'on' : ''}`} onClick={() => void toggle()} disabled={busy}><span className="toggle-knob" />{busy ? '…' : on ? 'Auto-approve on' : 'Ask each time'}</button>
  </div>
}
import { THEMES, FONTS, FONT_SIZE_MIN, FONT_SIZE_MAX, getTheme, getFont, getFontSize, applyTheme, applyFont, applyFontSize, type ThemeKey, type FontKey } from '../theme'
import { notifySupported, notifyEnabled, enableNotifications, setNotifyPref } from '../lib/notify'
import { getGoalMaxIter, setGoalMaxIter } from '../lib/goal'
import { Dropdown } from '../components/ui/Dropdown'

const fmtTime = (s: string) => { try { return new Date(s.replace(' ', 'T') + (s.endsWith('Z') ? '' : 'Z')).toLocaleString() } catch { return s } }
const auditTone = (a: string) => /error|delete|remove|revoke/.test(a) ? 'danger' : /login|redeem|create|invite|provision/.test(a) ? 'accent' : ''
const shortText = (s?: string | null, max = 120) => {
  const clean = (s || '').replace(/\s+/g, ' ').trim()
  return clean.length > max ? `${clean.slice(0, max - 1)}…` : clean
}
type SettingsSectionKey = 'account' | 'agents' | 'knowledge' | 'media' | 'remote' | 'diagnostics'

const SETTINGS_SECTIONS: { key: SettingsSectionKey; label: string; hint: string }[] = [
  { key: 'account', label: 'Account & Preferences', hint: 'Account, appearance and notifications' },
  { key: 'agents', label: 'Agents & Collaboration', hint: 'Runners, goals and prompt modes' },
  { key: 'knowledge', label: 'Knowledge & Wiki', hint: 'Project notes, links, graph and search' },
  { key: 'media', label: 'Media & Integrations', hint: 'Image and video generation backends' },
  { key: 'remote', label: 'Remote Access', hint: 'Tailscale and Cloudflare setup' },
  { key: 'diagnostics', label: 'Diagnostics', hint: 'Updates, debug logs and audit history' },
]

function DebugLogsPanel({ token }: { token: string }) {
  const [data, setData] = React.useState<DebugLogs | null>(null)
  const [limit, setLimit] = React.useState(240)
  const [busy, setBusy] = React.useState(false)
  const [cancelling, setCancelling] = React.useState(false)
  const [reapingJobs, setReapingJobs] = React.useState(false)
  const [error, setError] = React.useState('')
  const loadSeq = React.useRef(0)
  const actionSeq = React.useRef(0)
  const mountedRef = React.useRef(true)

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      loadSeq.current += 1
      actionSeq.current += 1
    }
  }, [])

  const load = React.useCallback(async () => {
    const seq = ++loadSeq.current
    setBusy(true)
    setError('')
    try {
      const next = await getDebugLogs(token, limit)
      if (!mountedRef.current || seq !== loadSeq.current) return
      setData(next)
    } catch (e) {
      if (mountedRef.current && seq === loadSeq.current) setError(String(e))
    } finally {
      if (mountedRef.current && seq === loadSeq.current) setBusy(false)
    }
  }, [token, limit])

  React.useEffect(() => { void load() }, [load])

  const runs = data?.runs || []
  const activeRuns = data?.activeRuns || []
  const staleRuns = data?.staleRuns || []
  const orphanedJobs = data?.orphanedJobs || []
  const lastRun = runs[0]
  const lineCount = data?.logs ? data.logs.split('\n').filter(Boolean).length : 0

  async function cancelStaleRuns() {
    if (!staleRuns.length || cancelling) return
    const seq = ++actionSeq.current
    setCancelling(true)
    setError('')
    try {
      await Promise.all(staleRuns.map(r => cancelRun(token, r.id)))
      if (mountedRef.current && seq === actionSeq.current) await load()
    } catch (e) {
      if (mountedRef.current && seq === actionSeq.current) setError(String(e))
    } finally {
      if (mountedRef.current && seq === actionSeq.current) setCancelling(false)
    }
  }

  async function cleanupOrphanedJobs() {
    if (!orphanedJobs.length || reapingJobs) return
    const seq = ++actionSeq.current
    setReapingJobs(true)
    setError('')
    try {
      await reapOrphanedJobs(token)
      if (mountedRef.current && seq === actionSeq.current) await load()
    } catch (e) {
      if (mountedRef.current && seq === actionSeq.current) setError(String(e))
    } finally {
      if (mountedRef.current && seq === actionSeq.current) setReapingJobs(false)
    }
  }

  return <div className="panel debug-panel">
    <div className="panel-head"><h3>Debug logs</h3><span>{busy ? 'loading' : `${lineCount} lines`}</span></div>
    <p className="muted">Service journal, queued/running sessions, and recent runs for quick error checks.</p>
    <div className="debug-toolbar">
      <button className="ghost-button" type="button" onClick={() => void load()} disabled={busy}>{busy ? 'Refreshing…' : 'Refresh'}</button>
      <Dropdown value={String(limit)} onChange={v => setLimit(Number(v))} minWidth={130} options={[120, 240, 500, 1000].map(n => ({ value: String(n), label: `${n} lines` }))} />
    </div>
    <div className="debug-summary">
      <div className="debug-stat"><span>Active sessions</span><strong>{data?.rawActiveSessionIds.length ?? 0}</strong><small>{data?.rawActiveSessionIds.join(', ') || 'none'}</small></div>
      <div className="debug-stat"><span>Queued/running</span><strong>{activeRuns.length}</strong><small>{activeRuns.map(r => `#${r.id}`).join(', ') || 'none'}</small></div>
      <div className={`debug-stat ${staleRuns.length ? 'danger' : ''}`}><span>Stale runs</span><strong>{staleRuns.length}</strong><small>{staleRuns.map(r => `#${r.id}`).join(', ') || 'none'}</small></div>
      <div className={`debug-stat ${orphanedJobs.length ? 'danger' : ''}`}><span>Orphaned jobs</span><strong>{orphanedJobs.length}</strong><small>{orphanedJobs.map(j => `#${j.id}`).join(', ') || 'none'}</small></div>
      <div className="debug-stat"><span>Last run</span><strong>{lastRun ? `#${lastRun.id}` : 'none'}</strong><small>{lastRun ? `${lastRun.status} · ${fmtTime(lastRun.created_at)}` : 'No runs yet'}</small></div>
    </div>
    {staleRuns.length > 0 && <div className="debug-stale-action">
      <p className="error-text">Some queued/running records are stale. They no longer count as active sessions; cancel them to clear stuck state.</p>
      <button className="ghost-button danger" type="button" onClick={() => void cancelStaleRuns()} disabled={busy || cancelling}>{cancelling ? 'Cancelling…' : 'Cancel stale runs'}</button>
    </div>}
    {orphanedJobs.length > 0 && <div className="debug-stale-action">
      <p className="error-text">Some workflow jobs are marked running but have no active run. Mark them failed to clear stuck workflow state.</p>
      <button className="ghost-button danger" type="button" onClick={() => void cleanupOrphanedJobs()} disabled={busy || reapingJobs}>{reapingJobs ? 'Cleaning…' : 'Mark orphaned jobs failed'}</button>
      <div className="debug-run-list">
        {orphanedJobs.slice(0, 5).map(j => <div className="debug-run-row" key={j.id}>
          <code>job #{j.id}</code>
          <span className="audit-action danger">{j.status}</span>
          <span title={j.title || j.session_title || ''}>{shortText(j.title || j.session_title || `Session ${j.session_id}`)}</span>
          <span className="muted">step {j.current_step_idx + 1}</span>
        </div>)}
      </div>
    </div>}
    <div className="debug-run-list">
      {runs.slice(0, 8).map(r => <div className="debug-run-row" key={r.id}>
        <code>#{r.id}</code>
        <span className={`audit-action ${/fail|error|cancel/.test(r.status) ? 'danger' : /running|queued/.test(r.status) ? 'accent' : ''}`}>{r.status}</span>
        <span title={r.prompt || r.session_title || ''}>{shortText(r.prompt || r.session_title || `Session ${r.session_id}`)}</span>
        <span className="muted" title={r.heartbeat_at || r.created_at}>{r.heartbeat_at ? fmtTime(r.heartbeat_at) : fmtTime(r.created_at)}</span>
      </div>)}
      {runs.length === 0 && <p className="muted">No recent runs.</p>}
    </div>
    {data?.logError && <p className="error-text">{data.logError}</p>}
    {error && <p className="error-text">{error}</p>}
    <pre className="debug-log">{data?.logs || 'No journal output.'}</pre>
  </div>
}

function AuditPanel({ token }: { token: string }) {
  const [entries, setEntries] = React.useState<AuditEntry[]>([])
  const [q, setQ] = React.useState('')
  const [error, setError] = React.useState('')
  const [open, setOpen] = React.useState(false)
  const loadSeq = React.useRef(0)
  const mountedRef = React.useRef(true)

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      loadSeq.current += 1
    }
  }, [])

  React.useEffect(() => {
    if (!open) {
      loadSeq.current += 1
      return
    }
    const seq = ++loadSeq.current
    setError('')
    listAudit(token)
      .then(b => {
        if (mountedRef.current && seq === loadSeq.current) setEntries(b.entries)
      })
      .catch(e => {
        if (mountedRef.current && seq === loadSeq.current) setError(String(e))
      })
  }, [token, open])

  const ql = q.trim().toLowerCase()
  const rows = ql ? entries.filter(e => `${e.actor} ${e.action} ${e.target_type} ${e.target_id} ${e.metadata}`.toLowerCase().includes(ql)) : entries

  return <div className="panel"><div className="panel-head"><h3>Audit log</h3><span>admin</span></div>
    <p className="muted">Every run, file change, settings test and admin action across the environment.</p>
    <button className="ghost-button" onClick={() => setOpen(true)}>Open audit log</button>
    {open && <div className="modal-scrim" onClick={() => setOpen(false)}><div className="modal-card audit-modal" onClick={e => e.stopPropagation()}>
      <div className="audit-modal-head"><h3>Audit log <span className="muted">({entries.length})</span></h3><button className="icon-button" aria-label="Close" onClick={() => setOpen(false)}>✕</button></div>
      <input className="ui-select audit-search" placeholder="Filter by actor, action, target…" value={q} onChange={e => setQ(e.target.value)} />
      <div className="audit-list scrollable">
        {rows.length === 0 && <p className="muted">No entries.</p>}
        {rows.map(e => <div className="audit-row" key={e.id}>
          <span className="audit-time">{fmtTime(e.created_at)}</span>
          <span className="audit-actor">{e.actor || 'system'}</span>
          <span className={`audit-action ${auditTone(e.action)}`}>{e.action}</span>
          <span className="audit-target" title={`${e.target_type}:${e.target_id}`}>{e.target_type}:{e.target_id}</span>
          {e.metadata && e.metadata !== '{}' ? <span className="audit-meta" title={e.metadata}>{e.metadata}</span> : <span />}
        </div>)}
      </div>
      {error && <p className="error-text">{error}</p>}
    </div></div>}
  </div>
}

function ImageGenerationPanel({ token }: { token: string }) {
  const [cfg, setCfg] = React.useState<ImageGenSettings | null>(null)
  const [provider, setProvider] = React.useState('codex')
  const [baseUrl, setBaseUrl] = React.useState('')
  const [model, setModel] = React.useState('')
  const [apiKey, setApiKey] = React.useState('')
  const [busy, setBusy] = React.useState<'load' | 'save' | 'test' | null>('load')
  const [status, setStatus] = React.useState('')
  const [err, setErr] = React.useState('')
  const requestSeq = React.useRef(0)
  const mountedRef = React.useRef(true)

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      requestSeq.current += 1
    }
  }, [])

  const load = React.useCallback(async () => {
    const seq = ++requestSeq.current
    setBusy('load'); setErr('')
    try {
      const c = await getImageGenSettings(token)
      if (!mountedRef.current || seq !== requestSeq.current) return
      setCfg(c); setProvider(c.provider || c.defaultProvider || 'codex'); setBaseUrl(c.baseUrl || ''); setModel(c.model || ''); setApiKey('')
      const cr = c.codexReady
      setStatus(cr ? (cr.ready ? 'Codex ready — logged in.' : cr.detail) : '')
    } catch (e) { if (mountedRef.current && seq === requestSeq.current) setErr(String(e)) } finally { if (mountedRef.current && seq === requestSeq.current) setBusy(null) }
  }, [token])
  React.useEffect(() => { void load() }, [load])

  const selected = cfg?.providers.find(p => p.id === provider)
  const isCodex = selected?.kind === 'codex'
  const isHttp = selected?.kind === 'http'
  const showModel = selected?.kind === 'http' || selected?.kind === 'oauth' || selected?.kind === 'higgsfield'
  const needsKey = !!selected?.requiresKey
  const save = async () => {
    const seq = ++requestSeq.current
    setBusy('save'); setErr('')
    try {
      await saveImageGenSettings(token, { provider, baseUrl: isHttp ? baseUrl.trim() : null, model: showModel ? model.trim() : null, apiKey: needsKey ? apiKey.trim() || null : null })
      if (!mountedRef.current || seq !== requestSeq.current) return
      setStatus('Saved.'); setApiKey('')
      await load()
    } catch (e) { if (mountedRef.current && seq === requestSeq.current) setErr(String(e)) } finally { if (mountedRef.current && seq === requestSeq.current) setBusy(null) }
  }
  const test = async () => {
    const seq = ++requestSeq.current
    setBusy('test'); setErr(''); setStatus('')
    try {
      const r = await testImageGenSettings(token, { provider, baseUrl: isHttp ? baseUrl.trim() : null, model: showModel ? model.trim() : null, apiKey: needsKey ? apiKey.trim() || null : null })
      if (!mountedRef.current || seq !== requestSeq.current) return
      const ok = r.ok ?? r.ready ?? false
      setStatus(`${ok ? 'Ready' : 'Not ready'} — ${r.detail}`)
    } catch (e) { if (mountedRef.current && seq === requestSeq.current) setErr(String(e)) } finally { if (mountedRef.current && seq === requestSeq.current) setBusy(null) }
  }

  return <div className="panel">
    <div className="panel-head"><h3>Image generation</h3><span>{selected?.displayName || (isCodex ? 'codex' : 'endpoint')}</span></div>
    <p className="muted">Image generation can use Codex/ChatGPT OAuth, xAI OAuth, Higgsfield, or an OpenAI-compatible endpoint. Chat stays on ACP; this only changes the image backend.</p>
    {busy === 'load' && <p className="muted">Loading…</p>}
    {cfg && <div className="settings-rows">
      <span className="srow-label">Provider</span>
      <Dropdown value={provider} onChange={v => { setProvider(v); const p = cfg.providers.find(x => x.id === v); setBaseUrl(p?.defaultBaseUrl || ''); setModel(''); setStatus('') }} minWidth={260} options={cfg.providers.map(p => ({ value: p.id, label: p.displayName }))} />
      <span className="srow-label">Status</span>
      <span className={status.startsWith('Ready') || status.includes('ready') ? 'ok-text' : 'muted'}>{status || (cfg.hasApiKey ? 'API key saved.' : 'Not tested yet.')}</span>
      {isHttp && <>
        <span className="srow-label">Endpoint</span>
        <input className="ui-select" placeholder="https://api.openai.com/v1" value={baseUrl} onChange={e => setBaseUrl(e.target.value)} />
      </>}
      {showModel && <>
        <span className="srow-label">Model</span>
        <input className="ui-select" placeholder={provider === 'xai-oauth' ? 'grok-2-image or provider default' : provider === 'higgsfield' ? 'nano_banana_2' : 'gpt-image-1 or provider model id'} value={model} onChange={e => setModel(e.target.value)} />
      </>}
      {needsKey && <>
        <span className="srow-label">API key</span>
        <input className="ui-select" type="password" placeholder={cfg.hasApiKey ? 'Saved — leave blank to keep existing key' : 'Paste API key'} value={apiKey} onChange={e => setApiKey(e.target.value)} />
      </>}
    </div>}
    {selected?.note && <p className="muted">{selected.note}</p>}
    <div className="settings-actions">
      <button className="ghost-button" onClick={() => void test()} disabled={!!busy}>{busy === 'test' ? 'Testing…' : 'Test connection'}</button>
      <button className="primary-button" onClick={() => void save()} disabled={!!busy}>{busy === 'save' ? 'Saving…' : 'Save provider'}</button>
    </div>
    {err && <p className="error-text">{err}</p>}
  </div>
}

function VideoGenerationPanel({ token }: { token: string }) {
  const [cfg, setCfg] = React.useState<VideoGenSettings | null>(null)
  const [provider, setProvider] = React.useState('xai-oauth')
  const [model, setModel] = React.useState('')
  const [videoPolicy, setVideoPolicy] = React.useState<HiggsfieldSettings['videoPolicy']>('confirm-credits')
  const [maxVideoCredits, setMaxVideoCredits] = React.useState(50)
  const [busy, setBusy] = React.useState<'load' | 'save' | 'test' | null>('load')
  const [status, setStatus] = React.useState('')
  const [err, setErr] = React.useState('')
  const requestSeq = React.useRef(0)
  const mountedRef = React.useRef(true)

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      requestSeq.current += 1
    }
  }, [])

  const load = React.useCallback(async () => {
    const seq = ++requestSeq.current
    setBusy('load'); setErr('')
    try {
      const next = await getVideoGenSettings(token)
      if (!mountedRef.current || seq !== requestSeq.current) return
      setCfg(next); setProvider(next.provider || next.defaultProvider || 'xai-oauth'); setModel(next.model || ''); setVideoPolicy(next.videoPolicy); setMaxVideoCredits(next.maxVideoCredits)
      setStatus(next.status?.detail || '')
    } catch (e) {
      if (mountedRef.current && seq === requestSeq.current) setErr(String(e))
    } finally {
      if (mountedRef.current && seq === requestSeq.current) setBusy(null)
    }
  }, [token])

  React.useEffect(() => { void load() }, [load])

  const selected = cfg?.providers.find(p => p.id === provider)
  const isHiggsfield = selected?.kind === 'higgsfield'

  async function save() {
    const seq = ++requestSeq.current
    setBusy('save'); setErr('')
    try {
      const next = await saveVideoGenSettings(token, { provider, model: model.trim() || null, videoPolicy, maxVideoCredits })
      if (!mountedRef.current || seq !== requestSeq.current) return
      setStatus(next.status?.detail || 'Saved.'); await load()
    } catch (e) {
      if (mountedRef.current && seq === requestSeq.current) setErr(String(e))
    } finally {
      if (mountedRef.current && seq === requestSeq.current) setBusy(null)
    }
  }

  async function test() {
    const seq = ++requestSeq.current
    setBusy('test'); setErr(''); setStatus('')
    try {
      const r = await testVideoGenSettings(token, { provider })
      if (!mountedRef.current || seq !== requestSeq.current) return
      const ok = r.ok ?? r.ready ?? false
      setStatus(`${ok ? 'Ready' : 'Not ready'} — ${r.detail}`)
    } catch (e) {
      if (mountedRef.current && seq === requestSeq.current) setErr(String(e))
    } finally {
      if (mountedRef.current && seq === requestSeq.current) setBusy(null)
    }
  }

  return <div className="panel">
    <div className="panel-head"><h3>Video generation</h3><span>{selected?.displayName || provider}</span></div>
    <p className="muted">Video generation now has its own backend picker. xAI uses Hermes OAuth; Higgsfield uses the local CLI plus Proxima's credit policy.</p>
    {busy === 'load' && <p className="muted">Loading…</p>}
    {cfg && <div className="settings-rows">
      <span className="srow-label">Provider</span>
      <Dropdown value={provider} onChange={v => { setProvider(v); const p = cfg.providers.find(x => x.id === v); setModel(v === 'xai-oauth' ? 'grok-imagine-video' : ''); setStatus(p?.note || '') }} minWidth={260} options={cfg.providers.map(p => ({ value: p.id, label: p.displayName }))} />
      <span className="srow-label">Status</span>
      <span className={status.startsWith('Ready') || status.includes('available') || status.includes('connected') ? 'ok-text' : 'muted'}>{status || 'Not tested yet.'}</span>
      <span className="srow-label">Model</span>
      <input className="ui-select" value={model} onChange={e => setModel(e.target.value)} placeholder={isHiggsfield ? 'Optional Higgsfield model id' : 'grok-imagine-video'} />
      <span className="srow-label">Credit policy</span>
      <Dropdown value={videoPolicy} onChange={v => setVideoPolicy(v as HiggsfieldSettings['videoPolicy'])} minWidth={220} options={[
        { value: 'confirm-credits', label: 'Confirm credits' },
        { value: 'allow-with-limit', label: 'Allow with limit' },
        { value: 'disabled', label: 'Disabled' },
      ]} />
      <span className="srow-label">Credit cap</span>
      <input className="ui-select" type="number" min={0} value={maxVideoCredits} onChange={e => setMaxVideoCredits(Number(e.target.value || 0))} />
      {isHiggsfield && <>
        <span className="srow-label">Server login</span>
        <code>higgsfield auth login</code>
        <span className="srow-label">Workspace</span>
        <code>higgsfield workspace list && higgsfield workspace set &lt;workspace_id&gt;</code>
      </>}
    </div>}
    {selected?.note && <p className="muted">{selected.note}</p>}
    <div className="settings-actions">
      <button className="ghost-button" onClick={() => void test()} disabled={!!busy}>{busy === 'test' ? 'Testing…' : 'Test connection'}</button>
      <button className="primary-button" onClick={() => void save()} disabled={!!busy}>{busy === 'save' ? 'Saving…' : 'Save provider'}</button>
    </div>
    {err && <p className="error-text">{err}</p>}
  </div>
}

function RemoteAccessGuide() {
  return <div className="panel remote-guide">
    <div className="panel-head"><h3>Remote access</h3><span>setup guide</span></div>
    <div className="remote-guide-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: props => <a {...props} target="_blank" rel="noreferrer" /> }}>{remoteAccessGuide}</ReactMarkdown>
    </div>
  </div>
}

function ChangePasswordPanel({ token, onTokenChange }: { token: string; onTokenChange: (t: string) => void }) {
  const [cur, setCur] = React.useState('')
  const [next, setNext] = React.useState('')
  const [confirm, setConfirm] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [msg, setMsg] = React.useState<{ ok: boolean; text: string } | null>(null)
  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setMsg(null)
    if (next.length < 8) { setMsg({ ok: false, text: 'New password must be at least 8 characters.' }); return }
    if (next !== confirm) { setMsg({ ok: false, text: 'New passwords don’t match.' }); return }
    setBusy(true)
    try {
      const s = await changePassword(token, cur, next)
      onTokenChange(s.token)  // old session was revoked; adopt the fresh one
      setCur(''); setNext(''); setConfirm('')
      setMsg({ ok: true, text: 'Password changed. Other devices were signed out.' })
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError && err.status === 401 ? 'Current password is incorrect.' : 'Could not change the password.' })
    } finally { setBusy(false) }
  }
  return <div className="panel"><div className="panel-head"><h3>Password</h3><span>security</span></div>
    <form className="settings-rows auth-change-form" onSubmit={submit}>
      <input className="auth-input" type="password" placeholder="Current password" value={cur} onChange={e => setCur(e.target.value)} autoComplete="current-password" />
      <input className="auth-input" type="password" placeholder="New password" value={next} onChange={e => setNext(e.target.value)} autoComplete="new-password" />
      <input className="auth-input" type="password" placeholder="Confirm new password" value={confirm} onChange={e => setConfirm(e.target.value)} autoComplete="new-password" />
      {msg && <p className={msg.ok ? 'muted' : 'auth-error'}>{msg.text}</p>}
      <button className="primary-button" type="submit" disabled={busy || !cur || !next}>{busy ? 'Changing…' : 'Change password'}</button>
    </form>
  </div>
}

export function SettingsScreen({ token, user, profiles, projects, activeProject, onActiveProject, runners, features, onRefresh, onTokenChange, updateStatus, updateChecking, onCheckUpdates, onOpenUpdate }: { token: string; user: User; profiles: Profile[]; projects: Project[]; activeProject: Project | null; onActiveProject: (project: Project) => void; runners: Runner[]; features: AppFeatures; onRefresh: () => Promise<void>; onTokenChange: (t: string) => void; updateStatus?: UpdateStatus | null; updateChecking?: boolean; onCheckUpdates?: () => void | Promise<void>; onOpenUpdate?: () => void }) {
  const [activeSection, setActiveSection] = React.useState<SettingsSectionKey>('account')
  const [theme, setTheme] = React.useState<ThemeKey>(getTheme())
  const [font, setFont] = React.useState<FontKey>(getFont())
  const [fontSize, setFontSize] = React.useState<number>(getFontSize())
  const [notif, setNotif] = React.useState(notifyEnabled())
  const [notifBusy, setNotifBusy] = React.useState(false)
  const [goalMax, setGoalMax] = React.useState(getGoalMaxIter())
  const mountedRef = React.useRef(true)
  const notifSeq = React.useRef(0)

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      notifSeq.current += 1
    }
  }, [])

  async function toggleNotif() {
    if (notifBusy) return
    if (notif) { setNotifyPref(false); setNotif(false); return }
    const seq = ++notifSeq.current
    setNotifBusy(true)
    try {
      const next = await enableNotifications()
      if (mountedRef.current && seq === notifSeq.current) setNotif(next)
    } finally {
      if (mountedRef.current && seq === notifSeq.current) setNotifBusy(false)
    }
  }

  const goalOptions = [3, 5, 8, 12, 20]
  const settingsSections = SETTINGS_SECTIONS.map(section => section.key === 'media' && !features.video
    ? { ...section, hint: 'Image generation backend' }
    : section)
  const activeMeta = settingsSections.find(s => s.key === activeSection) ?? settingsSections[0]

  const accountPanel = <div className="panel"><div className="panel-head"><h3>Account</h3></div><div className="settings-account"><strong>{user.username}</strong><span className="muted">{profiles.length} profile{profiles.length !== 1 ? 's' : ''} · {projects.length} project{projects.length !== 1 ? 's' : ''}</span></div></div>
  const sourceLink = <a className="ghost-button" href="https://github.com/labsiqbal/proxima" target="_blank" rel="noopener noreferrer">Source code · AGPL-3.0</a>
  const updatesPanel = <div className="panel"><div className="panel-head"><h3>Updates</h3><span>releases</span></div>
    <div className="settings-updates">
      <strong>Proxima v{updateStatus?.current_version ?? '…'}</strong>
      {updateStatus?.update_available && updateStatus.latest
        ? <button type="button" className="primary-button" onClick={onOpenUpdate}>Update to v{updateStatus.latest.version}</button>
        : <span className="muted">{updateStatus ? `Up to date${updateStatus.checked_at ? ` · checked ${new Date(updateStatus.checked_at).toLocaleString()}` : ' · not checked yet'}` : 'Loading…'}</span>}
      <button type="button" className="ghost-button" onClick={() => void onCheckUpdates?.()} disabled={!!updateChecking}>{updateChecking ? 'Checking…' : 'Check for updates'}</button>
      {/* AGPL §13: network users must be able to get the source of the running app. */}
      {sourceLink}
    </div></div>
  const appearancePanel = <div className="panel"><div className="panel-head"><h3>Appearance</h3><span>theme &amp; font</span></div><p className="eyebrow">Theme</p><div className="theme-grid">{THEMES.map(t => <button key={t.key} className={`theme-swatch ${theme === t.key ? 'active' : ''}`} onClick={() => { applyTheme(t.key); setTheme(t.key) }} title={t.label} type="button"><span className="swatch-pv" style={{ background: t.surface }}><i style={{ background: t.accent }} /></span><small>{t.label}</small></button>)}</div><div className="settings-rows"><span className="srow-label">Font</span><Dropdown value={font} onChange={f => { applyFont(f as FontKey); setFont(f as FontKey) }} minWidth={220} options={FONTS.map(f => ({ value: f.key, label: f.label }))} /><span className="srow-label">Font size</span><div className="fontsize-slider"><input type="range" min={FONT_SIZE_MIN} max={FONT_SIZE_MAX} step={0.5} value={fontSize} onChange={e => { const px = Number(e.target.value); applyFontSize(px); setFontSize(px) }} aria-label="Font size" /><span className="fontsize-value">{fontSize}px</span></div></div></div>
  const notificationsPanel = <div className="panel"><div className="panel-head"><h3>Notifications</h3><span>desktop</span></div><p className="muted">Get a desktop alert when an agent finishes a chat or task while this tab is in the background.</p>{notifySupported() ? <button className={`toggle-pill ${notif ? 'on' : ''}`} onClick={() => void toggleNotif()} disabled={notifBusy}><span className="toggle-knob" />{notifBusy ? 'Requesting…' : notif ? 'On' : 'Off'}</button> : <p className="muted">Not supported in this browser.</p>}</div>
  const goalsPanel = <><div className="panel"><div className="panel-head"><h3>Agent goals</h3><span>/goal loop</span></div><p className="muted">Maximum autonomous iterations before a goal loop stops itself.</p><div className="seg sm">{goalOptions.map(n => <button key={n} className={goalMax === n ? 'active' : ''} onClick={() => { setGoalMaxIter(n); setGoalMax(n) }}>{n}</button>)}</div></div><PermissionsPanel token={token} /></>

  const content = activeSection === 'account'
    ? <>{accountPanel}<ChangePasswordPanel token={token} onTokenChange={onTokenChange} />{appearancePanel}{notificationsPanel}</>
    : activeSection === 'agents'
      ? <><RunnersScreen token={token} runners={runners} onRefresh={onRefresh} />{goalsPanel}<CollaborationSettingsPanel token={token} /></>
      : activeSection === 'knowledge'
        ? <WikiScreen token={token} projects={projects} activeProject={activeProject} onActiveProject={onActiveProject} />
        : activeSection === 'media'
        ? <><ImageGenerationPanel token={token} />{features.video && <VideoGenerationPanel token={token} />}</>
        : activeSection === 'remote'
          ? <RemoteAccessGuide />
          : <>{updatesPanel}<DebugLogsPanel token={token} /><AuditPanel token={token} /></>

  return <section className="settings-view">
    <aside className="settings-menu" aria-label="Settings sections">
      <p className="eyebrow">Settings</p>
      <div className="settings-menu-list">
        {settingsSections.map(section => <button
          key={section.key}
          type="button"
          className={`settings-menu-item ${activeSection === section.key ? 'active' : ''}`}
          onClick={() => setActiveSection(section.key)}
          aria-current={activeSection === section.key ? 'page' : undefined}
        >
          <strong>{section.label}</strong>
          <small>{section.hint}</small>
        </button>)}
      </div>
    </aside>
    <div className="settings-content" aria-live="polite">
      <div className="settings-content-head">
        <h2>{activeMeta.label}</h2>
        <p className="muted">{activeMeta.hint}</p>
      </div>
      {content}
    </div>
  </section>
}
