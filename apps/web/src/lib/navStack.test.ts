import { describe, expect, it } from 'vitest'
import {
  canGoBack,
  chromeBackLabel,
  isDeepShell,
  popDeep,
  projectSwitcherLocked,
  pushDeep,
  shouldKeepAlive,
  viewOriginLabel,
  type DeepShellFlags,
  type NavStackEntry,
} from './navStack'

const baseFlags = (over: Partial<DeepShellFlags> = {}): DeepShellFlags => ({
  view: 'chat',
  graphStage: 'home',
  archiveRecord: null,
  designCanvasOpen: false,
  settingsStack: false,
  ...over,
})

describe('navStack deep detection', () => {
  it('treats top-level surfaces as not deep', () => {
    for (const view of ['chat', 'master', 'activity', 'workflows', 'artifacts', 'design', 'settings'] as const) {
      expect(isDeepShell(baseFlags({ view }))).toBe(false)
      expect(projectSwitcherLocked(baseFlags({ view }))).toBe(false)
    }
  })

  it('locks project on task, workflow editor, archive record, design canvas, settings stack', () => {
    expect(projectSwitcherLocked(baseFlags({ view: 'task' }))).toBe(true)
    expect(projectSwitcherLocked(baseFlags({ view: 'workflows', graphStage: 'editor' }))).toBe(true)
    // A record panel is a deep surface inside Artifacts (#139, #144).
    expect(projectSwitcherLocked(baseFlags({
      view: 'artifacts',
      archiveRecord: { project: 'demo', slug: 'a1' },
    }))).toBe(true)
    expect(projectSwitcherLocked(baseFlags({ view: 'design', designCanvasOpen: true }))).toBe(true)
    expect(projectSwitcherLocked(baseFlags({ view: 'settings', settingsStack: true }))).toBe(true)
    // Design home / gallery are not deep.
    expect(projectSwitcherLocked(baseFlags({ view: 'design', designCanvasOpen: false }))).toBe(false)
  })
})

describe('navStack push/pop + chrome Back labels', () => {
  it('starts with Back disabled (empty stack)', () => {
    expect(canGoBack([])).toBe(false)
    expect(chromeBackLabel([])).toBe('Back')
  })

  it('returns to origin surface metadata on pop', () => {
    let stack: NavStackEntry[] = []
    stack = pushDeep(stack, {
      kind: 'task',
      originView: 'activity',
      originLabel: 'Tasks',
      meta: { jobId: 42 },
    })
    expect(canGoBack(stack)).toBe(true)
    expect(chromeBackLabel(stack)).toBe('Back to Tasks')

    const { stack: next, popped } = popDeep(stack)
    expect(popped).toEqual({
      kind: 'task',
      originView: 'activity',
      originLabel: 'Tasks',
      meta: { jobId: 42 },
    })
    expect(next).toEqual([])
    expect(canGoBack(next)).toBe(false)
  })

  it('keeps original origin when replacing same deep kind (task→task)', () => {
    let stack: NavStackEntry[] = []
    stack = pushDeep(stack, {
      kind: 'task',
      originView: 'master',
      originLabel: 'Master',
      meta: { jobId: 1 },
    })
    stack = pushDeep(stack, {
      kind: 'task',
      originView: 'activity',
      originLabel: 'Tasks',
      meta: { jobId: 2 },
    })
    expect(stack).toHaveLength(1)
    expect(stack[0].originView).toBe('master')
    expect(stack[0].originLabel).toBe('Master')
    expect(stack[0].meta).toEqual({ jobId: 2 })
  })

  it('stacks different deep kinds so nested Back works (task → archive)', () => {
    let stack: NavStackEntry[] = []
    stack = pushDeep(stack, {
      kind: 'task',
      originView: 'activity',
      originLabel: 'Tasks',
    })
    stack = pushDeep(stack, {
      kind: 'archive-record',
      originView: 'task',
      originLabel: 'Task',
      meta: { project: 'p', slug: 's' },
    })
    expect(stack).toHaveLength(2)
    expect(chromeBackLabel(stack)).toBe('Back to Task')
    const first = popDeep(stack)
    expect(first.popped?.kind).toBe('archive-record')
    expect(chromeBackLabel(first.stack)).toBe('Back to Tasks')
  })
})

describe('viewOriginLabel + keep-alive', () => {
  it('names primary loop surfaces', () => {
    expect(viewOriginLabel('chat')).toBe('Chat')
    expect(viewOriginLabel('activity')).toBe('Tasks')
    expect(viewOriginLabel('workflows')).toBe('Workflows')
    expect(viewOriginLabel('artifacts')).toBe('Artifacts')
  })

  it('keeps primary multitask surfaces alive', () => {
    expect(shouldKeepAlive('chat')).toBe(true)
    expect(shouldKeepAlive('master')).toBe(true)
    expect(shouldKeepAlive('artifacts')).toBe(true)
    expect(shouldKeepAlive('settings')).toBe(false)
  })
})
