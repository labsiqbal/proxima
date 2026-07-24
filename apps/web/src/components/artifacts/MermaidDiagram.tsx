import React from 'react'

let mermaidConfiguredForTheme = ''

function cssToken(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback
  return window.getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
}

export function MermaidDiagram({ source, onEdit }: {
  source: string
  onEdit: () => void
}) {
  const [svg, setSvg] = React.useState('')
  const [error, setError] = React.useState('')
  const renderId = React.useId().replace(/[^a-zA-Z0-9_-]/g, '')

  React.useEffect(() => {
    let alive = true
    setSvg('')
    setError('')
    void import('mermaid').then(async module => {
      const mermaid = module.default
      const themeKey = document.documentElement.dataset.theme || 'default'
      if (mermaidConfiguredForTheme !== themeKey) {
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          theme: 'base',
          themeVariables: {
            background: cssToken('--ui-surface', '#ffffff'),
            primaryColor: cssToken('--ui-surface-subtle', '#f8fafc'),
            primaryBorderColor: cssToken('--ui-stroke-primary', '#cbd5e1'),
            primaryTextColor: cssToken('--ui-text-primary', '#111827'),
            lineColor: cssToken('--ui-text-secondary', '#4b5563'),
            secondaryColor: cssToken('--ui-row-active-background', '#eef4ff'),
            tertiaryColor: cssToken('--ui-chat-surface-background', '#fbfcfe'),
            fontFamily: cssToken('--font-sans', 'sans-serif'),
          },
        })
        mermaidConfiguredForTheme = themeKey
      }
      const result = await mermaid.render(`proxima-mermaid-${renderId}`, source)
      if (alive) setSvg(result.svg)
    }).catch(cause => {
      if (alive) setError(cause instanceof Error ? cause.message : 'Could not render this Mermaid diagram.')
    })
    return () => { alive = false }
  }, [source, renderId])

  return <section className="av-mermaid" aria-label="Mermaid diagram">
    <div className="av-mermaid-head">
      <span><strong>Diagram</strong><small>Mermaid</small></span>
      <button type="button" className="ghost-button" onClick={onEdit}>Edit as whiteboard</button>
    </div>
    {error
      ? <div className="av-mermaid-error"><p>Could not render this diagram.</p><code>{error}</code><button type="button" className="ghost-button" onClick={onEdit}>Open source in whiteboard</button></div>
      : svg
        ? <div className="av-mermaid-svg" dangerouslySetInnerHTML={{ __html: svg }} />
        : <div className="av-mermaid-loading muted">Rendering diagram...</div>}
  </section>
}
