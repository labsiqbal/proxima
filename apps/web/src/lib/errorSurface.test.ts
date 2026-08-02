import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  APP_ERROR_LIMIT,
  appErrorSnapshot,
  clearAppErrors,
  dismissAppError,
  installGlobalErrorHandlers,
  noteApiSuccess,
  reportApiFailure,
  reportAppError,
  subscribeAppErrors,
} from './errorSurface'

// jsdom re-reports an unhandled `error` event to the runner, so swallow the
// default action while the probe event travels.
const dispatchSwallowed = (event: Event) => {
  const swallow = (dispatched: Event) => dispatched.preventDefault()
  window.addEventListener(event.type, swallow)
  try { window.dispatchEvent(event) } finally { window.removeEventListener(event.type, swallow) }
}

const dispatchError = (error: unknown, message = 'boom') => {
  const event = new Event('error', { cancelable: true }) as ErrorEvent
  Object.defineProperty(event, 'error', { configurable: true, value: error })
  Object.defineProperty(event, 'message', { configurable: true, value: message })
  dispatchSwallowed(event)
}

const dispatchRejection = (reason: unknown) => {
  const event = new Event('unhandledrejection', { cancelable: true }) as PromiseRejectionEvent
  Object.defineProperty(event, 'reason', { configurable: true, value: reason })
  dispatchSwallowed(event)
}

describe('errorSurface', () => {
  beforeEach(() => {
    clearAppErrors()
  })

  it('surfaces an uncaught window error', () => {
    const uninstall = installGlobalErrorHandlers(window)
    dispatchError(new Error('render exploded'))
    const [entry] = appErrorSnapshot()
    expect(entry).toBeTruthy()
    expect(entry.kind).toBe('error')
    expect(entry.body).toContain('render exploded')
    expect(entry.count).toBe(1)
    uninstall()
  })

  it('surfaces an unhandled promise rejection', () => {
    const uninstall = installGlobalErrorHandlers(window)
    dispatchRejection(new Error('promise died'))
    const [entry] = appErrorSnapshot()
    expect(entry.kind).toBe('rejection')
    expect(entry.body).toContain('promise died')
    uninstall()
  })

  it('keeps a stack snippet in the detail for bug reports', () => {
    const error = new Error('detail me')
    error.stack = ['Error: detail me', ...Array.from({ length: 40 }, (_, i) => `    at frame${i} (app.js:${i})`)].join('\n')
    reportAppError('error', error)
    const [entry] = appErrorSnapshot()
    expect(entry.detail).toContain('detail me')
    expect(entry.detail).toContain('at frame0')
    expect(entry.detail.split('\n').length).toBeLessThanOrEqual(9)
  })

  it('collapses repeated identical errors into one entry with a count', () => {
    for (let i = 0; i < 100; i += 1) reportAppError('error', new Error('loop'))
    const entries = appErrorSnapshot()
    expect(entries).toHaveLength(1)
    expect(entries[0].count).toBe(100)
  })

  it('caps the visible stack even when every error is different', () => {
    for (let i = 0; i < 20; i += 1) reportAppError('error', new Error(`distinct ${i}`))
    expect(appErrorSnapshot().length).toBe(APP_ERROR_LIMIT)
    // The newest failures win; the oldest are dropped.
    expect(appErrorSnapshot().at(-1)?.body).toContain('distinct 19')
  })

  it('suggests a reload when a dynamic import fails after a redeploy', () => {
    reportAppError('rejection', new TypeError('Failed to fetch dynamically imported module: /assets/index-abc123.js'))
    const [entry] = appErrorSnapshot()
    expect(entry.kind).toBe('chunk')
    expect(entry.suggestReload).toBe(true)
    expect(entry.body.toLowerCase()).toContain('reload')
  })

  it.each([
    'ResizeObserver loop completed with undelivered notifications.',
    'The user aborted a request.',
    'The operation was aborted.',
    'signal is aborted without reason',
  ])('ignores benign browser noise: %s', message => {
    reportAppError('error', new Error(message))
    expect(appErrorSnapshot()).toHaveLength(0)
  })

  it('ignores an AbortError by name even when the message is unusual', () => {
    const error = new Error('cancelled')
    error.name = 'AbortError'
    reportAppError('rejection', error)
    expect(appErrorSnapshot()).toHaveLength(0)
  })

  it('surfaces unreachable-server API failures', () => {
    reportApiFailure({ status: 0, method: 'POST', path: '/api/app/start', message: 'POST /api/app/start failed: Failed to fetch' })
    const [entry] = appErrorSnapshot()
    expect(entry.kind).toBe('api')
    expect(entry.body).toContain('/api/app/start')
    expect(entry.detail).toContain('Failed to fetch')
  })

  it('surfaces server-side 5xx failures', () => {
    reportApiFailure({ status: 500, method: 'GET', path: '/api/projects', message: 'boom' })
    expect(appErrorSnapshot()[0]?.kind).toBe('api')
  })

  it('leaves 4xx refusals to the flow that made the call', () => {
    reportApiFailure({ status: 403, method: 'POST', path: '/api/app/start', message: 'forbidden' })
    reportApiFailure({ status: 404, method: 'GET', path: '/api/projects/x', message: 'missing' })
    expect(appErrorSnapshot()).toHaveLength(0)
  })

  it('collapses a polling storm on the same endpoint', () => {
    for (let i = 0; i < 50; i += 1) {
      reportApiFailure({ status: 0, method: 'GET', path: '/api/app/status', message: 'Failed to fetch' })
    }
    expect(appErrorSnapshot()).toHaveLength(1)
    expect(appErrorSnapshot()[0].count).toBe(50)
  })

  it('retires the unreachable toast once a call succeeds again', () => {
    reportApiFailure({ status: 0, method: 'GET', path: '/api/runs/active', message: 'Failed to fetch' })
    reportApiFailure({ status: 500, method: 'GET', path: '/api/projects', message: 'boom' })
    reportAppError('error', new Error('unrelated'))
    noteApiSuccess()
    const kinds = appErrorSnapshot().map(entry => entry.title)
    expect(kinds).not.toContain('Could not reach Proxima')
    // A server-side failure and a real code error are not cured by reachability.
    expect(kinds).toContain('The server failed a request')
    expect(kinds).toContain('Something went wrong')
  })

  it('goes quiet once the page is unloading', () => {
    const uninstall = installGlobalErrorHandlers(window)
    window.dispatchEvent(new Event('pagehide'))
    reportApiFailure({ status: 0, method: 'GET', path: '/api/projects', message: 'Failed to fetch' })
    expect(appErrorSnapshot()).toHaveLength(0)
    uninstall()
  })

  it('notifies subscribers and supports dismissal', () => {
    const listener = vi.fn()
    const unsubscribe = subscribeAppErrors(listener)
    reportAppError('error', new Error('notify me'))
    expect(listener).toHaveBeenCalled()
    const id = appErrorSnapshot()[0].id
    dismissAppError(id)
    expect(appErrorSnapshot()).toHaveLength(0)
    unsubscribe()
  })

  it('returns a stable snapshot reference while nothing changes', () => {
    const first = appErrorSnapshot()
    expect(appErrorSnapshot()).toBe(first)
    reportAppError('error', new Error('change'))
    expect(appErrorSnapshot()).not.toBe(first)
  })

  it('stops reporting once the handlers are uninstalled', () => {
    const uninstall = installGlobalErrorHandlers(window)
    uninstall()
    dispatchError(new Error('after uninstall'))
    dispatchRejection(new Error('after uninstall too'))
    expect(appErrorSnapshot()).toHaveLength(0)
  })
})
