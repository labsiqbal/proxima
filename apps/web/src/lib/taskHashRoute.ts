export type TaskHistoryState = {
  proximaTaskPolicy?: unknown
  proximaView?: unknown
  [key: string]: unknown
}

/** In-app Task opens stamp history with preserve-work; cold permalinks do not. */
export function taskHashPreservesWorkProject(
  initial: boolean,
  historyState: unknown,
): boolean {
  if (initial) return false
  if (!historyState || typeof historyState !== 'object') return false
  return (historyState as TaskHistoryState).proximaTaskPolicy === 'preserve-work'
}

/** Drop the in-app Task policy so a later non-history hash open takes the permalink path. */
export function withoutTaskPolicy(historyState: unknown): Record<string, unknown> {
  const base =
    historyState && typeof historyState === 'object'
      ? { ...(historyState as Record<string, unknown>) }
      : {}
  delete base.proximaTaskPolicy
  return base
}

/** Stamp/restamp the in-app Task open policy used by openTask and Task-linked Design return. */
export function withInAppTaskPolicy(historyState: unknown): Record<string, unknown> {
  const base =
    historyState && typeof historyState === 'object'
      ? { ...(historyState as Record<string, unknown>) }
      : {}
  return {
    ...base,
    proximaView: 'task',
    proximaTaskPolicy: 'preserve-work',
  }
}

export type TaskRouteContext<TJob = unknown> = {
  jobId: number
  projectSlug: string | null
  initialJob: TJob | null
}

/** Reuse resolved ownership when history restores the same in-app Task. */
export function nextPreserveWorkTaskContext<TJob>(
  current: TaskRouteContext<TJob> | null,
  jobId: number,
): TaskRouteContext<TJob> {
  if (current?.jobId === jobId) return current
  return { jobId, projectSlug: null, initialJob: null }
}

/** Fill ownership fields once a Task payload resolves; never drop an existing seed. */
export function withResolvedTaskOwnership<
  TJob extends { id: number; project_slug?: string | null },
>(
  current: TaskRouteContext<TJob> | null,
  job: TJob,
): TaskRouteContext<TJob> | null {
  if (!current || current.jobId !== job.id) return current
  const projectSlug = job.project_slug || null
  if (current.projectSlug === projectSlug && current.initialJob != null) return current
  return {
    ...current,
    projectSlug,
    initialJob: current.initialJob ?? job,
  }
}
