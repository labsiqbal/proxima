import React from 'react'
import { listGraphTemplates } from '../api/graph'
import { ScheduleManager } from '../components/workflows/ScheduleManager'
import type { GraphTemplate } from '../types'

// Workflows owns two modes: the graph editor, and the schedules that run its saved
// templates. The Sequential recipe editor that used to live here is retired — a linear
// recipe is just a graph with no branches, and the canvas authors those too. The linear
// ENGINE remains for pre-existing jobs and sessions; what is gone is its authoring UI.
export function WorkflowsScreen({ mode = 'graph', onModeChange, graphContent, token, onOpenJob }: {
  mode?: 'graph' | 'scheduled'
  onModeChange?: (mode: 'graph' | 'scheduled') => void
  /** The feature-gated graph canvas. Absent when the graph engine is disabled. */
  graphContent?: React.ReactNode
  token: string
  onOpenJob?: (jobId: number) => void
}) {
  const [templates, setTemplates] = React.useState<GraphTemplate[]>([])
  const [error, setError] = React.useState('')
  const mounted = React.useRef(true)
  const loadSeq = React.useRef(0)

  React.useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; loadSeq.current += 1 }
  }, [])

  // Schedules reference workflows across every project, so the lookup list is fetched
  // unscoped — a schedule whose template belongs to another project must still be able
  // to say its name.
  React.useEffect(() => {
    if (mode !== 'scheduled') return
    const seq = ++loadSeq.current
    listGraphTemplates(token)
      .then(body => { if (mounted.current && seq === loadSeq.current) { setTemplates(body.items); setError('') } })
      .catch(cause => { if (mounted.current && seq === loadSeq.current) setError(String(cause)) })
  }, [mode, token])

  const modeNav = <div className="workflow-mode-nav seg" role="tablist" aria-label="Workflow view">
    <button className={mode === 'graph' ? 'active' : ''} role="tab" aria-selected={mode === 'graph'} onClick={() => onModeChange?.('graph')}>Editor</button>
    <button className={mode === 'scheduled' ? 'active' : ''} role="tab" aria-selected={mode === 'scheduled'} onClick={() => onModeChange?.('scheduled')}>Scheduled</button>
  </div>

  if (mode === 'scheduled') {
    return <section className="tasks-view scheduled-view">
      {modeNav}
      {error && <div className="error-bar">{error}</div>}
      <ScheduleManager token={token} workflows={templates} onOpenJob={onOpenJob} />
    </section>
  }

  return <section className="workflow-advanced-view">
    {modeNav}
    {graphContent ?? <div className="placeholder-view"><div className="assistant-bubble compact">
      <h1>Workflows</h1>
      <p className="muted">The workflow graph engine is disabled. Enable <code>PROXIMA_FEATURE_WORKFLOW_GRAPH</code> and restart to author workflows.</p>
    </div></div>}
  </section>
}
