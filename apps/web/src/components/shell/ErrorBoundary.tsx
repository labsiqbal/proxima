import { Component, type ErrorInfo, type ReactNode } from 'react'

// Top-level render crash guard. Without this, a single throw in any screen
// unmounts the whole SPA (white screen). The fallback is deliberately
// self-contained: it relies only on the global --ui-* theme tokens (inline) so a
// stylesheet bug can't also break the recovery UI. "Try again" re-mounts the
// children (clears the boundary state) for transient render errors; "Reload" is
// the hard reset for state that went bad upstream.
interface Props {
  children: ReactNode
}
interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('[proxima-os] render crash:', error, info)
  }

  private reset = () => this.setState({ error: null })

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    const overlay: React.CSSProperties = {
      position: 'fixed', inset: 0, zIndex: 9999,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
      background: 'rgba(0,0,0,.55)', backdropFilter: 'blur(2px)',
    }
    const card: React.CSSProperties = {
      maxWidth: 440, width: '100%', padding: 28, borderRadius: 16,
      background: 'var(--ui-surface, #fff)', color: 'var(--ui-text-primary, #111)',
      border: '1px solid var(--ui-stroke-secondary, #ddd)', boxShadow: '0 20px 60px rgba(0,0,0,.35)',
      display: 'flex', flexDirection: 'column', gap: 12,
    }
    const eyebrow: React.CSSProperties = {
      fontFamily: 'var(--font-mono, ui-monospace)', fontSize: 11, letterSpacing: '.08em',
      textTransform: 'uppercase', color: 'var(--ui-danger, #dc2626)', fontWeight: 600,
    }
    const actions: React.CSSProperties = { display: 'flex', gap: 8, marginTop: 8 }
    return (
      <div style={overlay} role="alertdialog" aria-live="assertive">
        <div style={card}>
          <div style={eyebrow}>Render error</div>
          <h1 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>The cockpit hit an unexpected error</h1>
          <p style={{ margin: 0, fontSize: 13, color: 'var(--ui-text-secondary, #555)', wordBreak: 'break-word' }}>
            {String(error?.message || error)}
          </p>
          <div style={actions}>
            <button className="primary-button" onClick={() => window.location.reload()}>Reload</button>
            <button className="ghost-button" onClick={this.reset}>Try again</button>
          </div>
        </div>
      </div>
    )
  }
}
