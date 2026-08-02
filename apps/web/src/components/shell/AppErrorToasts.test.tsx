import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AppErrorToasts } from './AppErrorToasts'
import { clearAppErrors, reportApiFailure, reportAppError } from '../../lib/errorSurface'

// jsdom re-reports an unhandled `error` event to the runner, so swallow the
// default action while the probe event travels.
const dispatchSwallowed = (event: Event) => {
  const swallow = (dispatched: Event) => dispatched.preventDefault()
  window.addEventListener(event.type, swallow)
  try { act(() => { window.dispatchEvent(event) }) } finally { window.removeEventListener(event.type, swallow) }
}

const throwFromWindow = (error: unknown) => {
  const event = new Event('error', { cancelable: true }) as ErrorEvent
  Object.defineProperty(event, 'error', { configurable: true, value: error })
  dispatchSwallowed(event)
}

const rejectFromWindow = (reason: unknown) => {
  const event = new Event('unhandledrejection', { cancelable: true }) as PromiseRejectionEvent
  Object.defineProperty(event, 'reason', { configurable: true, value: reason })
  dispatchSwallowed(event)
}

describe('AppErrorToasts', () => {
  beforeEach(() => {
    clearAppErrors()
  })

  it('renders nothing while the app is healthy', () => {
    const { container } = render(<AppErrorToasts />)
    expect(container).toBeEmptyDOMElement()
  })

  it('surfaces a thrown error as a visible toast', () => {
    render(<AppErrorToasts />)
    throwFromWindow(new Error('render exploded'))
    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong')
    expect(screen.getByRole('alert')).toHaveTextContent('render exploded')
  })

  it('surfaces an unhandled promise rejection as a visible toast', () => {
    render(<AppErrorToasts />)
    rejectFromWindow(new Error('background task died'))
    expect(screen.getByRole('alert')).toHaveTextContent('background task died')
  })

  it('collapses an error storm into a single toast with a repeat count', () => {
    render(<AppErrorToasts />)
    act(() => {
      for (let i = 0; i < 100; i += 1) reportAppError('error', new Error('render loop'))
    })
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(screen.getByText('×100')).toBeInTheDocument()
  })

  it('suggests a reload when a dynamic import fails after a redeploy', async () => {
    const reload = vi.fn()
    render(<AppErrorToasts onReload={reload} />)
    rejectFromWindow(new TypeError('Failed to fetch dynamically imported module: /assets/index-abc.js'))
    const toast = screen.getByRole('alert')
    expect(toast).toHaveTextContent('Proxima was updated')
    await userEvent.click(screen.getByRole('button', { name: 'Reload Proxima' }))
    expect(reload).toHaveBeenCalled()
  })

  it('exposes the message and stack snippet for a bug report', async () => {
    render(<AppErrorToasts />)
    const error = new Error('detail me')
    error.stack = 'Error: detail me\n    at doThing (app.js:1:1)'
    act(() => { reportAppError('error', error) })
    await userEvent.click(screen.getByText('Details'))
    expect(screen.getByText(/at doThing/)).toBeInTheDocument()
  })

  it('surfaces an API call that would otherwise die in the console', () => {
    render(<AppErrorToasts />)
    act(() => {
      reportApiFailure({ status: 0, method: 'POST', path: '/api/app/start', message: 'POST /api/app/start failed: Failed to fetch' })
    })
    expect(screen.getByRole('alert')).toHaveTextContent('/api/app/start')
  })

  it('dismisses a toast', async () => {
    render(<AppErrorToasts />)
    act(() => { reportAppError('error', new Error('go away')) })
    await userEvent.click(screen.getByRole('button', { name: /dismiss/i }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
