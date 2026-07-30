// Canonical named events delivered by the existing session SSE contract.
// Keep this list aligned with backend event_types.py when adding projections.
export const SESSION_EVENT_TYPES = [
  'run.queued', 'run.started', 'message.delta', 'reasoning.delta', 'tool.start', 'tool.complete',
  'approval.request', 'approval.auto', 'approval.denied', 'message.complete', 'run.completed', 'run.failed', 'run.cancelled',
  'warning', 'wiki.draft', 'workflow.draft', 'goal.update',
  'collaboration.child.queued', 'collaboration.child.started', 'collaboration.child.delta',
  'collaboration.child.completed', 'collaboration.child.failed', 'collaboration.child.cancelled',
  'message_review.queued', 'message_review.started', 'message_review.completed',
  'message_review.failed', 'message_review.applied', 'message_review.restored',
  'graph.node.update', 'job.update',
  'satpam.steered', 'satpam.restart.queued', 'satpam.restarted', 'satpam.escalated',
  'master.task.started', 'master.task.completed', 'master.task.failed',
  'master.task.cancelled', 'master.task.review_ready', 'master.task.blocked',
  'master.task.recovered',
  'master.attention.required', 'master.supervisor.outcome',
  'master.satpam.steered', 'master.satpam.restart_queued',
  'master.satpam.restarted', 'master.satpam.recovery_failed',
  'master.satpam.escalated',
  'master.focus.changed',
  'graph.state.missing', 'graph.state.queued', 'graph.state.building',
  'graph.state.fresh', 'graph.state.stale', 'graph.state.failed',
] as const

export type SessionEventType = typeof SESSION_EVENT_TYPES[number]
