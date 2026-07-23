import React from 'react'
import type { AppFeatures, ChatSession, Project } from '../../types'
import { search, type SearchChatHit, type SearchMessageHit, type SearchResults } from '../../api/search'
import { isFeatureSessionEnabled } from '../../features'

const EMPTY: SearchResults = { projects: [], chats: [], messages: [] }

/** Build a navigable session from a search hit, preferring the live sessions list. */
export function sessionFromSearchHit(
  hit: { id: number; title: string; mode?: string | null; project_slug?: string | null; project_name?: string | null },
  sessions: ChatSession[],
): ChatSession {
  const existing = sessions.find(x => x.id === hit.id)
  if (existing) return existing
  const title = hit.title || ''
  const mode = hit.mode || (/^(Design:|Design System:)/i.test(title) ? 'design' : 'chat')
  return {
    id: hit.id,
    title,
    mode,
    project_slug: hit.project_slug ?? null,
    project_name: hit.project_name ?? null,
    runner_id: '',
    visibility: 'private',
  }
}

export function isDesignSearchHit(session: { title?: string | null; mode?: string | null }): boolean {
  const title = session.title || ''
  return session.mode === 'design' || /^(Design:|Design System:)/i.test(title)
}

/** Collapse whitespace and cap length so accessible names stay scannable. */
export function compactSearchLabel(parts: Array<string | null | undefined>, max = 140): string {
  const text = parts
    .map(p => (p || '').replace(/\s+/g, ' ').trim())
    .filter(Boolean)
    .join(' · ')
  if (text.length <= max) return text
  return `${text.slice(0, Math.max(0, max - 1)).trimEnd()}…`
}

export function searchProjectAriaLabel(name: string, slug: string): string {
  return compactSearchLabel([name, slug !== name ? slug : null])
}

export function searchChatAriaLabel(title: string, projectName?: string | null, design?: boolean): string {
  return compactSearchLabel([title, projectName, design ? 'Design' : null])
}

export function searchMessageAriaLabel(sessionTitle: string, role: string, snippet: string): string {
  return compactSearchLabel([sessionTitle, `${role}: ${snippet}`], 160)
}

export function SearchModal(props: {
  token: string
  sessions: ChatSession[]
  projects: Project[]
  features: AppFeatures
  onClose: () => void
  onSelectSession: (s: ChatSession) => void
  onOpenDesign: (s: ChatSession) => void
  onSelectProject: (p: Project) => void
  onSelectView: (v: 'chat') => void
}) {
  const [q, setQ] = React.useState('')
  const [res, setRes] = React.useState<SearchResults>(EMPTY)
  const [loading, setLoading] = React.useState(false)
  const searchSeq = React.useRef(0)
  const mountedRef = React.useRef(true)

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      searchSeq.current += 1
    }
  }, [])

  React.useEffect(() => {
    const term = q.trim()
    const seq = ++searchSeq.current
    if (term.length < 2) { setRes(EMPTY); setLoading(false); return }
    setLoading(true)
    const h = window.setTimeout(() => {
      search(props.token, term)
        .then(next => { if (mountedRef.current && seq === searchSeq.current) setRes(next) })
        .catch(() => { if (mountedRef.current && seq === searchSeq.current) setRes(EMPTY) })
        .finally(() => { if (mountedRef.current && seq === searchSeq.current) setLoading(false) })
    }, 200)
    return () => clearTimeout(h)
  }, [q, props.token])

  const sessionEnabled = React.useCallback((hit: { id?: number; title?: string; mode?: string | null }) => {
    const session = (hit.id != null ? props.sessions.find(x => x.id === hit.id) : undefined)
      || { title: hit.title, mode: hit.mode }
    return isFeatureSessionEnabled(session, props.features)
  }, [props.features, props.sessions])

  const openSession = (hit: SearchChatHit | (SearchMessageHit & { id: number; title: string })) => {
    if (!sessionEnabled(hit)) return
    const session = sessionFromSearchHit(hit, props.sessions)
    if (props.features.designStudio && isDesignSearchHit(session)) {
      props.onOpenDesign(session)
    } else if (isDesignSearchHit(session)) {
      // Design Studio off — do not silently dismiss as if it opened.
      return
    } else {
      props.onSelectSession(session)
      props.onSelectView('chat')
    }
    props.onClose()
  }

  const openProject = (slug: string) => { const p = props.projects.find(x => x.slug === slug); if (p) props.onSelectProject(p); props.onClose() }

  const chats = res.chats.filter(c => sessionEnabled(c))
  const messages = res.messages.filter(m => sessionEnabled({ id: m.session_id, title: m.session_title, mode: m.mode }))
  const total = res.projects.length + chats.length + messages.length
  return <div className="modal-scrim" onClick={props.onClose}><div className="modal-card search-modal" role="dialog" aria-modal="true" aria-label="Search" onClick={e => e.stopPropagation()}>
    <input
      autoFocus
      className="ui-select search-input"
      type="search"
      name="proxima-search"
      placeholder="Search chats, projects, messages…"
      aria-label="Search chats, projects, messages"
      value={q}
      onChange={e => setQ(e.target.value)}
      onKeyDown={e => { if (e.key === 'Escape') props.onClose() }}
    />
    <div className="search-results" role="listbox" aria-label="Search results">
      {q.trim().length >= 2 && loading && <p className="muted">Searching…</p>}
      {q.trim().length >= 2 && !loading && total === 0 && <p className="muted">No matches.</p>}
      {res.projects.length > 0 && <div className="search-group"><p className="eyebrow">Projects</p>{res.projects.map(p => <button type="button" role="option" className="search-item" key={'p' + p.slug} aria-label={searchProjectAriaLabel(p.name, p.slug)} onClick={() => openProject(p.slug)}><strong aria-hidden="true">{p.name}</strong><small aria-hidden="true">{p.slug}</small></button>)}</div>}
      {chats.length > 0 && <div className="search-group"><p className="eyebrow">Chats</p>{chats.map(c => <button type="button" role="option" className="search-item" key={'c' + c.id} aria-label={searchChatAriaLabel(c.title, c.project_name, isDesignSearchHit(c))} onClick={() => openSession(c)}><strong aria-hidden="true">{c.title}</strong>{c.project_name ? <small aria-hidden="true">{c.project_name}{isDesignSearchHit(c) ? ' · Design' : ''}</small> : isDesignSearchHit(c) ? <small aria-hidden="true">Design</small> : null}</button>)}</div>}
      {messages.length > 0 && <div className="search-group"><p className="eyebrow">Messages</p>{messages.map((m, i) => <button type="button" role="option" className="search-item" key={'m' + i} aria-label={searchMessageAriaLabel(m.session_title, m.role, m.snippet)} onClick={() => openSession({ id: m.session_id, title: m.session_title, mode: m.mode, project_slug: m.project_slug, project_name: m.project_name })}><strong aria-hidden="true">{m.session_title}</strong><small aria-hidden="true">{m.role}: {m.snippet}</small></button>)}</div>}
    </div>
  </div></div>
}
