import React from 'react'

// Workflows owns a tabbed library home and a focused graph editor. Schedule
// management lives in each workflow row, so there is no second destination or mode.
export function WorkflowsScreen({ graphContent }: {
  /** The graph canvas. */
  graphContent?: React.ReactNode
}) {
  return <section className="workflow-advanced-view">
    {graphContent ?? <div className="placeholder-view"><div className="assistant-bubble compact">
      <h1>Workflows</h1>
      <p className="muted">The workflow editor is switched off on this server. The setup docs cover turning it back on.</p>
    </div></div>}
  </section>
}
