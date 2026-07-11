import React from 'react'
import { Terminal, type ITheme } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

// Light palette tuned for readability on white (every color dark enough; white/
// bright-white remapped to ink so emphasis text doesn't vanish).
const LIGHT_THEME: ITheme = {
  background: '#ffffff', foreground: '#24292f', cursor: '#24292f', cursorAccent: '#ffffff',
  selectionBackground: '#b6d7ff', selectionForeground: '#24292f',
  black: '#24292f', red: '#cf222e', green: '#0a7b34', yellow: '#8a6400',
  blue: '#0860ca', magenta: '#7c3fce', cyan: '#16767c', white: '#5b636d',
  brightBlack: '#4b535d', brightRed: '#b21f2c', brightGreen: '#0c7a33',
  brightYellow: '#6d4c00', brightBlue: '#0860ca', brightMagenta: '#6f33bd',
  brightCyan: '#136a70', brightWhite: '#24292f',
}
// Standard dark palette — the right fit for TUIs (Hermes, vim, htop) which assume
// a dark background. Matches the app's "dark" theme surface (#0d1117).
const DARK_THEME: ITheme = {
  background: '#0d1117', foreground: '#e6edf3', cursor: '#e6edf3', cursorAccent: '#0d1117',
  selectionBackground: '#264f78', selectionForeground: '#e6edf3',
  black: '#484f58', red: '#ff7b72', green: '#3fb950', yellow: '#d29922',
  blue: '#58a6ff', magenta: '#bc8cff', cyan: '#39c5cf', white: '#b1bac4',
  brightBlack: '#6e7681', brightRed: '#ffa198', brightGreen: '#56d364',
  brightYellow: '#e3b341', brightBlue: '#79c0ff', brightMagenta: '#d2a8ff',
  brightCyan: '#56d4dd', brightWhite: '#f0f6fc',
}
// The app marks dark themes via <html data-theme>. Only "dark" is a dark surface;
// the rest are light. Pick the terminal palette to match.
function isDarkTheme(): boolean {
  return document.documentElement.getAttribute('data-theme') === 'dark'
}

// In-browser PTY terminal wired to the Proxima terminal WebSocket. Work in the
// project's shell directly from the cockpit — no SSH. Follows the app theme.
export function TerminalView({ token, projectSlug }: { token: string; projectSlug?: string }) {
  const hostRef = React.useRef<HTMLDivElement>(null)
  const [status, setStatus] = React.useState<'connecting' | 'open' | 'closed'>('connecting')
  const [dark, setDark] = React.useState(isDarkTheme())
  const sessionSeq = React.useRef(0)

  React.useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const seq = ++sessionSeq.current
    const active = () => seq === sessionSeq.current
    setStatus('connecting')
    const term = new Terminal({
      cursorBlink: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
      fontSize: 13,
      theme: isDarkTheme() ? DARK_THEME : LIGHT_THEME,
    })
    // Live-update the palette when the app theme changes.
    const themeObserver = new MutationObserver(() => {
      if (!active()) return
      const d = isDarkTheme()
      term.options.theme = d ? DARK_THEME : LIGHT_THEME
      setDark(d)
    })
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(host)
    try { fit.fit() } catch { /* host not measured yet */ }

    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const qs = new URLSearchParams()
    if (token) qs.set('token', token)
    if (projectSlug) qs.set('project', projectSlug)
    const ws = new WebSocket(`${proto}://${location.host}/api/ws/terminal?${qs.toString()}`)
    ws.binaryType = 'arraybuffer'

    const sendResize = () => {
      if (!active()) return
      try { fit.fit() } catch { /* noop */ }
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'resize', rows: term.rows, cols: term.cols }))
    }

    ws.onopen = () => { if (!active()) return; setStatus('open'); term.focus(); sendResize() }
    ws.onmessage = (ev) => {
      if (!active()) return
      const data = ev.data
      if (data instanceof ArrayBuffer) term.write(new Uint8Array(data))
      else term.write(String(data))
    }
    ws.onclose = () => { if (!active()) return; setStatus('closed'); term.write('\r\n\x1b[90m[session closed]\x1b[0m\r\n') }
    ws.onerror = () => { if (active()) setStatus('closed') }

    const dataSub = term.onData((d) => { if (ws.readyState === WebSocket.OPEN) ws.send(d) })
    const ro = new ResizeObserver(() => sendResize())
    ro.observe(host)
    window.addEventListener('resize', sendResize)

    return () => {
      if (seq === sessionSeq.current) sessionSeq.current += 1
      window.removeEventListener('resize', sendResize)
      themeObserver.disconnect()
      ro.disconnect()
      dataSub.dispose()
      ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null
      try { ws.close() } catch { /* noop */ }
      term.dispose()
    }
  }, [token, projectSlug])

  return <div className={`terminal-wrap ${dark ? 'dark' : ''}`}>
    <div className={`terminal-status ${status}`}>{status === 'open' ? '● connected' : status === 'connecting' ? '… connecting' : '○ closed'}</div>
    <div className="terminal-host" ref={hostRef} />
  </div>
}
