import React from 'react'
import { TerminalView } from './TerminalView'
import { IconClose } from '../shell/icons'

// Several independent PTY terminals as tabs. Each tab is a full TerminalView (its
// own xterm + WebSocket + shell on the backend). Inactive tabs are hidden, NOT
// unmounted, so their shells keep running while you switch — like VS Code terminals.
export function TerminalTabs({ token, projectSlug }: { token: string; projectSlug?: string }) {
  const counter = React.useRef(1)
  const [tabs, setTabs] = React.useState<{ id: number; n: number }[]>([{ id: 1, n: 1 }])
  const [active, setActive] = React.useState(1)

  const add = () => {
    const n = ++counter.current
    setTabs(t => [...t, { id: n, n }])
    setActive(n)
  }
  const close = (id: number) => {
    // Removing a tab unmounts its TerminalView -> WebSocket closes -> the backend
    // SIGHUPs the shell. Closing the last one leaves an empty state (nothing runs).
    const rest = tabs.filter(t => t.id !== id)
    setTabs(rest)
    if (active === id && rest.length) setActive(rest[rest.length - 1].id)
  }

  return <div className="term-tabs">
    <div className="term-tabbar" role="tablist">
      {tabs.map(t => (
        <div key={t.id} role="tab" aria-selected={active === t.id} className={`term-tab ${active === t.id ? 'on' : ''}`} onClick={() => setActive(t.id)}>
          <span className="term-tab-dot" />
          <span className="term-tab-name">Terminal {t.n}</span>
          <button className="term-tab-x" onClick={e => { e.stopPropagation(); close(t.id) }} aria-label={`Close Terminal ${t.n}`} title="Close terminal (ends the session)"><IconClose size={12} /></button>
        </div>
      ))}
      <button className="term-tab-add" onClick={add} title="New terminal" aria-label="New terminal">+</button>
    </div>
    <div className="term-panes">
      {tabs.length === 0
        ? <div className="term-empty">
            <p>No terminals running.</p>
            <button className="primary-button" onClick={add}>+ New terminal</button>
          </div>
        : tabs.map(t => (
          <div key={t.id} className={`term-pane ${active === t.id ? 'on' : 'off'}`}>
            <TerminalView token={token} projectSlug={projectSlug} />
          </div>
        ))}
    </div>
  </div>
}
