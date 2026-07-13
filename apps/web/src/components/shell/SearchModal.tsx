import React from 'react'
import type { AppFeatures, ChatSession, Project } from '../../types'
import { search, type SearchResults } from '../../api/search'
import { isFeatureSessionEnabled } from '../../features'

const EMPTY: SearchResults = { projects: [], chats: [], messages: [] }

export function SearchModal(props: {
  token: string
  sessions: ChatSession[]
  projects: Project[]
  features: AppFeatures
  onClose: () => void
  onSelectSession: (s: ChatSession) => void
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

  const sessionEnabled = React.useCallback((id: number, title = '') => {
    const session = props.sessions.find(x => x.id === id)
    return isFeatureSessionEnabled(session || { title }, props.features)
  }, [props.features, props.sessions])
  const openSession = (id: number, title = '') => {
    if (!sessionEnabled(id, title)) return
    const s = props.sessions.find(x => x.id === id)
    if (s) { props.onSelectSession(s); props.onSelectView('chat') }
    props.onClose()
  }
  const openProject = (slug: string) => { const p = props.projects.find(x => x.slug === slug); if (p) props.onSelectProject(p); props.onClose() }

  const chats = res.chats.filter(c => sessionEnabled(c.id, c.title))
  const messages = res.messages.filter(m => sessionEnabled(m.session_id, m.session_title))
  const total = res.projects.length + chats.length + messages.length
  return <div className="modal-scrim" onClick={props.onClose}><div className="modal-card search-modal" onClick={e => e.stopPropagation()}>
    <input autoFocus className="ui-select search-input" placeholder="Search chats, tasks, projects, messages…" value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => { if (e.key === 'Escape') props.onClose() }} />
    <div className="search-results">
      {q.trim().length >= 2 && loading && <p className="muted">Searching…</p>}
      {q.trim().length >= 2 && !loading && total === 0 && <p className="muted">No matches.</p>}
      {res.projects.length > 0 && <div className="search-group"><p className="eyebrow">Projects</p>{res.projects.map(p => <button className="search-item" key={'p' + p.slug} onClick={() => openProject(p.slug)}><strong>{p.name}</strong><small>{p.slug}</small></button>)}</div>}
      {chats.length > 0 && <div className="search-group"><p className="eyebrow">Chats</p>{chats.map(c => <button className="search-item" key={'c' + c.id} onClick={() => openSession(c.id, c.title)}><strong>{c.title}</strong></button>)}</div>}
      {messages.length > 0 && <div className="search-group"><p className="eyebrow">Messages</p>{messages.map((m, i) => <button className="search-item" key={'m' + i} onClick={() => openSession(m.session_id, m.session_title)}><strong>{m.session_title}</strong><small>{m.role}: {m.snippet}</small></button>)}</div>}
    </div>
  </div></div>
}
