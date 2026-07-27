import type { View } from '../types'

/** Deep surfaces that lock the project switcher and enable chrome Back. */
export type DeepKind =
  | 'task'
  | 'workflow-editor'
  | 'archive-record'
  | 'design-canvas'
  | 'settings-stack'

export type NavStackEntry = {
  kind: DeepKind
  /** Origin surface the owner entered from (chrome Back restores this). */
  originView: View
  /** Short label for "Back to {label}" (e.g. Tasks, Chat, Workflows). */
  originLabel: string
  /** Optional restore payload (job id, archive slug, etc.). */
  meta?: Record<string, unknown>
}

/** Flags describing whether the shell is currently on a deep surface. */
export type DeepShellFlags = {
  view: View
  graphStage: 'home' | 'editor'
  archiveRecord: { project: string; slug: string } | null
  designCanvasOpen: boolean
  /** True when Settings was entered as a stacked deep route (not top-level). */
  settingsStack: boolean
}

/** Human label for a primary / known view (chrome Back origin names). */
export function viewOriginLabel(view: View): string {
  switch (view) {
    case 'chat':
      return 'Chat'
    case 'master':
      return 'Master'
    case 'activity':
      return 'Tasks'
    case 'home':
      return 'New task'
    case 'workflows':
    case 'graph':
      return 'Workflows'
    case 'artifacts':
      return 'Archive'
    case 'design':
      return 'Design'
    case 'settings':
    case 'projects':
      return 'Settings'
    case 'profiles':
      return 'Agents'
    case 'wiki':
      return 'Wiki'
    case 'runners':
      return 'Runners'
    case 'task':
      return 'Task'
    default:
      return 'Back'
  }
}

/** Whether the current shell placement is a project-locked deep surface. */
export function isDeepShell(flags: DeepShellFlags): boolean {
  if (flags.view === 'task') return true
  if (flags.view === 'workflows' && flags.graphStage === 'editor') return true
  if (flags.view === 'graph' && flags.graphStage === 'editor') return true
  if (flags.view === 'artifacts' && flags.archiveRecord != null) return true
  if (flags.view === 'design' && flags.designCanvasOpen) return true
  if (flags.view === 'settings' && flags.settingsStack) return true
  return false
}

/** Project switcher is locked on deep surfaces. */
export function projectSwitcherLocked(flags: DeepShellFlags): boolean {
  return isDeepShell(flags)
}

export function pushDeep(stack: NavStackEntry[], entry: NavStackEntry): NavStackEntry[] {
  // Replacing the same kind (e.g. task→task, record→record) keeps one frame so
  // chrome Back still returns to the original origin, not the previous deep.
  const top = stack[stack.length - 1]
  if (top && top.kind === entry.kind) {
    return [...stack.slice(0, -1), { ...entry, originView: top.originView, originLabel: top.originLabel }]
  }
  return [...stack, entry]
}

export function popDeep(stack: NavStackEntry[]): {
  stack: NavStackEntry[]
  popped: NavStackEntry | null
} {
  if (stack.length === 0) return { stack, popped: null }
  const popped = stack[stack.length - 1]
  return { stack: stack.slice(0, -1), popped }
}

export function canGoBack(stack: NavStackEntry[]): boolean {
  return stack.length > 0
}

/** Accessible / title label when enabled; when disabled still "Back". */
export function chromeBackLabel(stack: NavStackEntry[]): string {
  const top = stack[stack.length - 1]
  if (!top) return 'Back'
  return top.originLabel === 'Back' ? 'Back' : `Back to ${top.originLabel}`
}

/** Keep-alive: primary multitask surfaces that should not unmount on leave. */
export const KEEP_ALIVE_VIEWS: readonly View[] = [
  'chat',
  'master',
  'activity',
  'workflows',
  'artifacts',
  'design',
] as const

export function shouldKeepAlive(view: View): boolean {
  return (KEEP_ALIVE_VIEWS as readonly string[]).includes(view)
}
