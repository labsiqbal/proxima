import type { ChatMessage } from '../types'

export type MasterHistoryScope =
  | { kind: 'roving' }
  | { kind: 'fleet' }
  | { kind: 'container'; containerId: number }

export type MasterHistoryMessage = ChatMessage & {
  clientId?: string
  pending?: boolean
  historyKind: 'focused-segment' | 'system-event' | 'focus-boundary' | null
}

function isFocusBoundary(message: ChatMessage): boolean {
  return message.role === 'system'
    && /^Master Focus changed to (?:Container \d+|Fleet mode)\.$/.test(message.content)
}

/**
 * Project the one canonical Master thread.  A Container receives its immutable
 * Focus segments plus server-owned events about that Container.  Fleet excludes
 * Container-subject events even when they originated from a Fleet turn.
 */
export function projectMasterHistory(
  messages: readonly ChatMessage[],
  scope: MasterHistoryScope,
): MasterHistoryMessage[] {
  const seen = new Set<number | string>()
  return messages.flatMap((message, index) => {
    const attribution = message.message_focus
    const focused = attribution?.focus_container_id ?? null
    const subject = attribution?.subject_container_id ?? null
    const matches = scope.kind === 'roving'
      || (scope.kind === 'fleet' && focused == null && subject == null)
      || (scope.kind === 'container' && (
        focused === scope.containerId || subject === scope.containerId
      ))
    const key = message.id ?? `transient:${index}`
    if (!matches || seen.has(key)) return []
    seen.add(key)
    const historyKind = isFocusBoundary(message)
      ? 'focus-boundary'
      : scope.kind === 'container' && subject === scope.containerId
        ? 'system-event'
        : scope.kind === 'container' && focused === scope.containerId
          ? 'focused-segment'
          : null
    return [{ ...message, historyKind }]
  })
}
