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
