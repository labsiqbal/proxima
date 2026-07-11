import React from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import type { GraphNode, GraphLink } from './wikiGraph'
import { ROOT_ID } from './wikiGraph'

// Obsidian-style graph: notes are nodes, [[wikilinks]] are edges.
export function WikiGraph({ nodes, links, activePath, onOpen }: { nodes: GraphNode[]; links: GraphLink[]; activePath: string | null; onOpen: (path: string) => void }) {
  const wrapRef = React.useRef<HTMLDivElement>(null)
  const [size, setSize] = React.useState({ w: 600, h: 420 })

  React.useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // react-force-graph mutates node/link objects (adds x/y) — feed fresh copies.
  const data = React.useMemo(() => ({ nodes: nodes.map(n => ({ ...n })), links: links.map(l => ({ ...l })) }), [nodes, links])

  const css = getComputedStyle(document.documentElement)
  const accent = css.getPropertyValue('--ui-accent').trim() || '#3b82f6'
  const textColor = css.getPropertyValue('--ui-text-secondary').trim() || '#555'
  // Edges: --ui-stroke is too faint on a light background; tint toward the
  // mid-gray text color so links read clearly in both themes.
  const tert = css.getPropertyValue('--ui-text-tertiary').trim() || '#888'
  const linkColor = /^#[0-9a-f]{6}$/i.test(tert) ? tert + 'aa' : tert

  if (nodes.length === 0) return <div className="wiki-pane-msg muted">No notes yet. Create notes and link them with [[Note]] to see the graph.</div>

  return <div className="wiki-graph" ref={wrapRef}>
    <ForceGraph2D
      width={size.w}
      height={size.h}
      graphData={data}
      nodeId="id"
      nodeLabel="name"
      linkColor={() => linkColor}
      linkWidth={1.4}
      cooldownTicks={80}
      onNodeClick={(n: { id?: string }) => { if (n.id && n.id !== ROOT_ID) onOpen(n.id) }}
      nodeCanvasObject={(n: { id?: string; name?: string; degree?: number; x?: number; y?: number }, ctx: CanvasRenderingContext2D, scale: number) => {
        const isRoot = n.id === ROOT_ID
        const r = isRoot ? 7 : 2.5 + Math.sqrt(n.degree || 0) * 1.4
        ctx.fillStyle = isRoot ? '#eab308' : (n.id === activePath ? '#ef4444' : accent)
        ctx.beginPath(); ctx.arc(n.x || 0, n.y || 0, r, 0, 2 * Math.PI); ctx.fill()
        if (isRoot) { ctx.lineWidth = 1.4 / scale; ctx.strokeStyle = '#facc15'; ctx.stroke() }
        // Always label the project root; label other nodes when zoomed in.
        if (isRoot || scale > 1.1) {
          const fs = (isRoot ? 12 : 11) / scale
          ctx.font = `${isRoot ? '600 ' : ''}${fs}px ui-sans-serif, system-ui, sans-serif`
          ctx.fillStyle = textColor
          ctx.textAlign = 'center'
          ctx.textBaseline = 'top'
          ctx.fillText(n.name || '', n.x || 0, (n.y || 0) + r + 1)
        }
      }}
    />
  </div>
}
