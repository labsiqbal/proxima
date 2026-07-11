import React from 'react'
import { createProfile, deleteProfile, updateProfile, runnerCapabilities } from '../api/profiles'
import type { Profile, RunnerCapabilities } from '../types'
import { Dropdown } from '../components/ui/Dropdown'

type RunnerReadiness = Record<string, { displayName: string; installed: boolean; ready: boolean; authHint: string }>

// Per-card instructions editor (the profile's "soul" / AGENTS.md). Saves on demand.
function InstructionsEditor({ token, profile, onSaved }: { token: string; profile: Profile; onSaved: () => Promise<void> }) {
  const [val, setVal] = React.useState(profile.instructions || '')
  const [saving, setSaving] = React.useState(false)
  const [error, setError] = React.useState('')
  const mountedRef = React.useRef(true)
  const saveSeq = React.useRef(0)
  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      saveSeq.current += 1
    }
  }, [])
  const dirty = val !== (profile.instructions || '')
  const save = async () => {
    if (saving) return
    const seq = ++saveSeq.current
    setSaving(true)
    setError('')
    try {
      await updateProfile(token, profile.id, { instructions: val })
      if (!mountedRef.current || seq !== saveSeq.current) return
      await onSaved()
    } catch (err) {
      if (mountedRef.current && seq === saveSeq.current) setError(String(err))
    } finally {
      if (mountedRef.current && seq === saveSeq.current) setSaving(false)
    }
  }
  return <div className="profile-instructions">
    <div className="profile-instructions-head"><span className="profile-key">Instructions</span>{dirty && <button className="ghost-button sm" disabled={saving} onClick={() => void save()}>Save</button>}</div>
    <textarea value={val} onChange={e => { setVal(e.target.value); if (error) setError('') }} placeholder="System instructions for this agent — its role, tone, rules (like a SOUL.md / AGENTS.md). Optional." rows={3} disabled={saving} />
    {error && <p className="error-text">{error}</p>}
  </div>
}

// Inline rename for a profile: click the name to edit, Enter/blur saves, Esc
// cancels. Empty names are ignored so a profile always keeps a label.
function NameEditor({ token, profile, onSaved }: { token: string; profile: Profile; onSaved: () => Promise<void> }) {
  const [editing, setEditing] = React.useState(false)
  const [val, setVal] = React.useState(profile.name)
  const [saving, setSaving] = React.useState(false)
  const mountedRef = React.useRef(true)
  React.useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false } }, [])
  const start = () => { setVal(profile.name); setEditing(true) }
  const commit = async () => {
    const next = val.trim()
    if (saving) return
    if (!next || next === profile.name) { setEditing(false); return }
    setSaving(true)
    try {
      await updateProfile(token, profile.id, { name: next })
      if (mountedRef.current) { setEditing(false); await onSaved() }
    } catch { if (mountedRef.current) setEditing(false) }
    finally { if (mountedRef.current) setSaving(false) }
  }
  if (!editing) return <button className="profile-name-btn" onClick={start} title="Rename agent">{profile.name}</button>
  return <input className="profile-name-input" autoFocus value={val} disabled={saving}
    onChange={e => setVal(e.target.value)} onBlur={() => void commit()}
    onKeyDown={e => { if (e.key === 'Enter') void commit(); else if (e.key === 'Escape') setEditing(false) }} />
}

// A compact trigger row on the profile card that opens the dedicated Skills & MCP
// window. Loads a lightweight count so the card shows N/total without opening.
function CapabilitiesTrigger({ token, profile, onSaved }: { token: string; profile: Profile; onSaved: () => Promise<void> }) {
  const [open, setOpen] = React.useState(false)
  const [caps, setCaps] = React.useState<RunnerCapabilities | null>(null)

  React.useEffect(() => {
    let alive = true
    if (!profile.runner_id) return
    runnerCapabilities(token, profile.runner_id).then(c => { if (alive) setCaps(c) }).catch(() => {})
    return () => { alive = false }
  }, [token, profile.runner_id, profile.capabilities])

  const sel = profile.capabilities
  const total = (caps?.skills.length || 0) + (caps?.mcp.length || 0)
  const active = caps ? caps.skills.filter(s => !sel?.skills || sel.skills.includes(s.id)).length
    + caps.mcp.filter(m => !sel?.mcp || sel.mcp.includes(m.name)).length : 0

  return <>
    <button className="profile-caps-open" onClick={() => setOpen(true)}>
      <span className="profile-key">Skills &amp; MCP</span>
      <span className="profile-caps-open-count">{caps ? <><b>{active}</b> / {total}</> : '…'}</span>
      <span className="profile-caps-open-cta">Manage →</span>
    </button>
    {open && <CapabilitiesModal token={token} profile={profile} onSaved={onSaved} onClose={() => setOpen(false)} />}
  </>
}

// Dedicated Skills & MCP window: detects what the profile's runner has on this host
// and lets the user pick which to activate. NULL selection = inherit all; the first
// toggle makes it explicit. Skills grouped by category into collapsible sections.
function CapabilitiesModal({ token, profile, onSaved, onClose }: { token: string; profile: Profile; onSaved: () => Promise<void>; onClose: () => void }) {
  const [caps, setCaps] = React.useState<RunnerCapabilities | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [saving, setSaving] = React.useState(false)
  const [filter, setFilter] = React.useState('')
  const [expanded, setExpanded] = React.useState<Record<string, boolean>>({})
  const [err, setErr] = React.useState('')

  React.useEffect(() => {
    if (!profile.runner_id) { setLoading(false); return }
    runnerCapabilities(token, profile.runner_id)
      .then(setCaps).catch(e => setErr(String(e?.message || e))).finally(() => setLoading(false))
  }, [profile.runner_id, token])

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const total = (caps?.skills.length || 0) + (caps?.mcp.length || 0)
  const sel = profile.capabilities
  const skillOn = (id: string) => !sel?.skills || sel.skills.includes(id)
  const mcpOn = (name: string) => !sel?.mcp || sel.mcp.includes(name)
  const activeCount = caps ? caps.skills.filter(s => skillOn(s.id)).length + caps.mcp.filter(m => mcpOn(m.name)).length : 0

  async function commit(mutate: (next: { skills: string[]; mcp: string[] }) => void) {
    if (!caps || saving) return
    const next = { skills: caps.skills.map(s => s.id).filter(skillOn), mcp: caps.mcp.map(m => m.name).filter(mcpOn) }
    mutate(next)
    setSaving(true); setErr('')
    try { await updateProfile(token, profile.id, { capabilities: next }); await onSaved() }
    catch (e: any) { setErr(String(e?.message || e)) }
    finally { setSaving(false) }
  }
  const flip = (arr: string[], id: string) => { const i = arr.indexOf(id); if (i >= 0) arr.splice(i, 1); else arr.push(id) }
  const toggleSkill = (id: string) => commit(n => flip(n.skills, id))
  const toggleMcp = (name: string) => commit(n => flip(n.mcp, name))
  const setMany = (ids: string[], on: boolean) => commit(n => { const set = new Set(n.skills); ids.forEach(id => on ? set.add(id) : set.delete(id)); n.skills = [...set] })
  const setAll = (on: boolean) => commit(n => { n.skills = on ? (caps?.skills.map(s => s.id) || []) : []; n.mcp = on ? (caps?.mcp.map(m => m.name) || []) : [] })

  const q = filter.trim().toLowerCase()
  const match = (s: { id: string; name?: string; description?: string }) =>
    !q || s.id.toLowerCase().includes(q) || (s.name || '').toLowerCase().includes(q) || (s.description || '').toLowerCase().includes(q)
  const groups = new Map<string, RunnerCapabilities['skills']>()
  for (const s of caps?.skills || []) {
    if (!match(s)) continue
    const g = s.group || ''
    ;(groups.get(g) || groups.set(g, []).get(g)!).push(s)
  }
  const mcp = (caps?.mcp || []).filter(m => !q || m.name.toLowerCase().includes(q))
  const leaf = (id: string) => id.includes('/') ? id.slice(id.indexOf('/') + 1) : id

  return <div className="modal-scrim" onClick={onClose}>
    <div className="modal-card caps-modal" onClick={e => e.stopPropagation()} role="dialog" aria-modal="true">
      <div className="caps-modal-head">
        <div>
          <h3>Skills &amp; MCP</h3>
          <p className="muted caps-modal-sub">Runtime <code>{profile.runner_id}</code> · profile “{profile.name}” · <b>{activeCount}</b>/{total} on{saving && ' · saving…'}</p>
        </div>
        <button className="ghost-button sm" onClick={onClose}>Close</button>
      </div>
      <p className="muted caps-modal-note">Detected on this host from the runtime's own config — enable what this profile should use. Unchecked = off; everything on = inherit the host's full set.</p>
      {loading && <p className="muted">Detecting…</p>}
      {err && <p className="error-text">{err}</p>}
      {caps && total === 0 && <p className="muted">No skills or MCP servers found for this runner on this host.</p>}
      {caps && total > 0 && <>
        <div className="caps-modal-bar">
          <input className="profile-caps-filter" value={filter} onChange={e => setFilter(e.target.value)} placeholder="Filter skills…" />
          <div className="profile-caps-bulk">
            <button className="link-button" disabled={saving} onClick={() => setAll(true)}>All</button>
            <span className="muted">·</span>
            <button className="link-button" disabled={saving} onClick={() => setAll(false)}>None</button>
          </div>
        </div>
        <div className="profile-caps-list caps-modal-list">
          {[...groups.entries()].map(([g, items]) => {
            if (!g) return items.map(s => <SkillRow key={s.id} s={s} on={skillOn(s.id)} disabled={saving} onToggle={() => void toggleSkill(s.id)} label={s.id} />)
            const ids = items.map(s => s.id)
            const on = items.filter(s => skillOn(s.id)).length
            const isOpen = expanded[g] ?? !!q
            return <div className="profile-caps-group" key={g}>
              <div className="profile-caps-ghead">
                <button className="profile-caps-gtoggle" onClick={() => setExpanded(e => ({ ...e, [g]: !isOpen }))}>
                  <span className="profile-caps-chev">{isOpen ? '▾' : '▸'}</span>{g}
                </button>
                <span className="profile-caps-gcount muted">{on}/{items.length}</span>
                <button className="link-button" disabled={saving} onClick={() => setMany(ids, on < items.length)}>{on < items.length ? 'all' : 'none'}</button>
              </div>
              {isOpen && items.map(s => <SkillRow key={s.id} s={s} on={skillOn(s.id)} disabled={saving} onToggle={() => void toggleSkill(s.id)} label={leaf(s.id)} indent />)}
            </div>
          })}
          {mcp.length > 0 && <div className="profile-caps-group">
            <div className="profile-caps-ghead"><span className="profile-caps-gtoggle profile-caps-glabel">MCP servers</span><span className="profile-caps-gcount muted">{mcp.filter(m => mcpOn(m.name)).length}/{mcp.length}</span></div>
            {mcp.map(m => <label key={m.name} className="profile-caps-item profile-caps-indent" title={m.detail || ''}>
              <input type="checkbox" checked={mcpOn(m.name)} disabled={saving} onChange={() => void toggleMcp(m.name)} />
              <span className="profile-caps-name">{m.name}</span>
              <span className="profile-caps-tag">{m.kind}</span>
            </label>)}
          </div>}
        </div>
      </>}
    </div>
  </div>
}

function SkillRow({ s, on, disabled, onToggle, label, indent }: { s: { description?: string }; on: boolean; disabled: boolean; onToggle: () => void; label: string; indent?: boolean }) {
  return <label className={`profile-caps-item${indent ? ' profile-caps-indent' : ''}`} title={s.description || ''}>
    <input type="checkbox" checked={on} disabled={disabled} onChange={onToggle} />
    <span className="profile-caps-name">{label}</span>
  </label>
}

export function ProfilesScreen({ token, profiles, onActiveProfile, onRefresh }: { token: string; profiles: Profile[]; onActiveProfile: (p: Profile) => void; onRefresh: () => Promise<void> }) {
  const [name, setName] = React.useState('')
  const [runner, setRunner] = React.useState('')
  const [instructions, setInstructions] = React.useState('')
  const [readiness, setReadiness] = React.useState<RunnerReadiness>({})
  const [error, setError] = React.useState('')
  const [busy, setBusy] = React.useState<'create' | `default:${number}` | `delete:${number}` | `runner:${number}` | null>(null)
  const mountedRef = React.useRef(true)
  const actionSeq = React.useRef(0)
  const readinessSeq = React.useRef(0)

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      actionSeq.current += 1
      readinessSeq.current += 1
    }
  }, [])

  React.useEffect(() => {
    if (!token) return
    const seq = ++readinessSeq.current
    fetch('/api/runners/detect', { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.json())
      .then(b => { if (mountedRef.current && seq === readinessSeq.current) setReadiness(b.runnerReadiness || {}) })
      .catch(() => { if (mountedRef.current && seq === readinessSeq.current) setReadiness({}) })
    return () => { readinessSeq.current += 1 }
  }, [token])

  // Pre-select the first ready runner once detection loads (no vendor hardcoded).
  // If none is ready, leaving it '' lets the backend resolve via default_runner().
  React.useEffect(() => {
    if (runner) return
    const firstReady = Object.entries(readiness).find(([, r]) => r.ready)?.[0]
    if (firstReady) setRunner(firstReady)
  }, [readiness, runner])

  async function create(event: React.FormEvent) {
    event.preventDefault()
    if (busy) return
    setError('')
    setBusy('create')
    const seq = ++actionSeq.current
    try {
      const p = await createProfile(token, { name, runner_id: runner, instructions: instructions || undefined })
      if (!mountedRef.current || seq !== actionSeq.current) return
      setName(''); setRunner(''); setInstructions('')
      onActiveProfile(p)
      await onRefresh()
    } catch (err) { if (mountedRef.current && seq === actionSeq.current) setError(String(err)) } finally { if (mountedRef.current && seq === actionSeq.current) setBusy(null) }
  }
  async function makeDefault(profile: Profile) {
    if (busy || profile.is_default) return
    setBusy(`default:${profile.id}`)
    setError('')
    const seq = ++actionSeq.current
    try {
      const p = await updateProfile(token, profile.id, { is_default: true })
      if (!mountedRef.current || seq !== actionSeq.current) return
      onActiveProfile(p)
      await onRefresh()
    } catch (err) { if (mountedRef.current && seq === actionSeq.current) setError(String(err)) } finally { if (mountedRef.current && seq === actionSeq.current) setBusy(null) }
  }
  async function remove(profile: Profile) {
    if (busy || profile.is_default || profiles.length <= 1) return
    setBusy(`delete:${profile.id}`)
    setError('')
    const seq = ++actionSeq.current
    try {
      await deleteProfile(token, profile.id)
      if (!mountedRef.current || seq !== actionSeq.current) return
      await onRefresh()
    } catch (err) { if (mountedRef.current && seq === actionSeq.current) setError(String(err)) } finally { if (mountedRef.current && seq === actionSeq.current) setBusy(null) }
  }
  async function changeRunner(profile: Profile, runner_id: string) {
    if (busy || runner_id === profile.runner_id) return
    setBusy(`runner:${profile.id}`)
    setError('')
    const seq = ++actionSeq.current
    try {
      await updateProfile(token, profile.id, { runner_id })
      if (!mountedRef.current || seq !== actionSeq.current) return
      await onRefresh()
    } catch (err) { if (mountedRef.current && seq === actionSeq.current) setError(String(err)) } finally { if (mountedRef.current && seq === actionSeq.current) setBusy(null) }
  }

  const runnerOptions = Object.values(readiness).length
    ? Object.entries(readiness).map(([id, r]) => ({ value: id, label: r.displayName + (r.installed ? '' : ' (not installed)'), badge: r.ready ? 'ready' : undefined }))
    : []

  return <section className="profiles-view">
    <div className="panel"><div className="panel-head"><h3>Agent profiles</h3><span>{profiles.length}</span></div>
      <p className="muted">Each profile is an agent persona with its own runner, managed home, and instructions.</p>
      <div className="runner-grid">{profiles.map(profile => <article className={`runner-card ${profile.is_default ? 'is-default' : ''}`} key={profile.id}>
        <div className="runner-card-head"><NameEditor token={token} profile={profile} onSaved={onRefresh} />{profile.is_default && <span>Default</span>}</div>
        <div className="profile-meta">
          <div className="profile-row"><span className="profile-key">Runner</span><Dropdown value={profile.runner_id || ''} onChange={v => void changeRunner(profile, v)} options={runnerOptions} minWidth={150} disabled={!!busy} /></div>
          <div className="profile-row"><span className="profile-key">Home</span><span className="profile-home" title={profile.hermes_home || ''}>{profile.hermes_home || 'managed by Proxima'}</span></div>
        </div>
        <InstructionsEditor token={token} profile={profile} onSaved={onRefresh} />
        <CapabilitiesTrigger token={token} profile={profile} onSaved={onRefresh} />
        <div className="button-row"><button onClick={() => void makeDefault(profile)} disabled={!!busy || profile.is_default}>{busy === `default:${profile.id}` ? 'Saving…' : 'Set default'}</button><button className="ghost-button danger" onClick={() => void remove(profile)} disabled={!!busy || profile.is_default || profiles.length <= 1}>{busy === `delete:${profile.id}` ? 'Deleting…' : 'Delete'}</button></div>
      </article>)}</div>
    </div>
    <div className="panel"><div className="panel-head"><h3>Create agent profile</h3><span>persona</span></div>
      <form className="stack-form" onSubmit={create}>
        <label>Name<input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Research Agent" /></label>
        <label>Runner<Dropdown value={runner} onChange={setRunner} options={runnerOptions} /></label>
        {readiness[runner] && !readiness[runner].ready && readiness[runner].authHint && <p className="muted" style={{ marginTop: -4, fontSize: 'var(--text-xs)' }}>{readiness[runner].authHint}</p>}
        <label>Instructions <span className="muted">(optional — the agent's role &amp; rules)</span><textarea value={instructions} onChange={e => setInstructions(e.target.value)} placeholder="e.g. You are a careful research assistant. Cite sources, ask before assuming…" rows={4} /></label>
        <button className="primary-button" disabled={!!busy || !name.trim()}>{busy === 'create' ? 'Creating…' : 'Create profile'}</button>
      </form>{error && <p className="error-text">{error}</p>}
    </div>
  </section>
}
