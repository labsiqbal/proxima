import type { GraphJob, Job, JobStatus, RunProjection } from '../types'

type RunSource = {
  status: JobStatus
  started_at?: string | null
  finished_at?: string | null
  created_at?: string | null
  run_projection?: RunProjection
  node_states?: GraphJob['node_states']
  steps_state?: Job['steps_state']
}

const TERMINAL = new Set<JobStatus>(['done', 'failed', 'cancelled'])

function timestamp(value?: string | null): number | null {
  if (!value || !/(?:Z|[+-]\d\d:\d\d)$/.test(value)) return null
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : null
}

function fallbackProjection(source: RunSource): RunProjection {
  const states = source.node_states?.length
    ? source.node_states
    : source.steps_state ?? []
  const childFailed = states.some(state => state.status === 'failed')
  const status = childFailed && !TERMINAL.has(source.status) ? 'failed' : source.status
  const starts = states
    .map(state => state.started_at)
    .filter((value): value is string => timestamp(value) !== null)
  const finishes = states
    .map(state => state.finished_at)
    .filter((value): value is string => timestamp(value) !== null)
  const started_at = source.started_at ?? starts.sort((a, b) => (timestamp(a) ?? 0) - (timestamp(b) ?? 0))[0] ?? null
  const finished_at = source.finished_at ?? finishes.sort((a, b) => (timestamp(b) ?? 0) - (timestamp(a) ?? 0))[0] ?? null
  const start = timestamp(started_at)
  const finish = timestamp(finished_at)
  return {
    status,
    started_at,
    finished_at,
    duration_seconds: start !== null && finish !== null && finish >= start
      ? Math.round((finish - start) / 1000)
      : null,
  }
}

function timezoneAwareOrNull(value?: string | null): boolean {
  return value == null || timestamp(value) !== null
}

export function projectRun(source: RunSource): RunProjection {
  const projection = source.run_projection
  if (
    projection
    && timezoneAwareOrNull(projection.started_at)
    && timezoneAwareOrNull(projection.finished_at)
  ) {
    return projection
  }
  return fallbackProjection(source)
}

export function runStatusLabel(status: JobStatus): string {
  return status.charAt(0).toUpperCase() + status.slice(1)
}

export function formatRunAge(
  projection: Pick<RunProjection, 'started_at'>,
  createdAt?: string | null,
  now = Date.now(),
): string {
  const started = timestamp(projection.started_at) ?? timestamp(createdAt)
  if (started === null) return 'Unknown'
  const seconds = Math.max(0, Math.round((now - started) / 1000))
  if (seconds < 60) return 'Just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return days === 1 ? 'Yesterday' : `${days}d ago`
}

export function formatRunDuration(
  projection: Pick<RunProjection, 'started_at' | 'finished_at' | 'duration_seconds'>,
  now = Date.now(),
): string {
  const start = timestamp(projection.started_at)
  if (start === null) return 'Not started'
  const seconds = projection.duration_seconds
    ?? Math.max(0, Math.round(((timestamp(projection.finished_at) ?? now) - start) / 1000))
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  if (minutes < 1) return `${remainder}s`
  if (minutes < 60) return `${minutes}m ${remainder}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}
